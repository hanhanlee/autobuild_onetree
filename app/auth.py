import json
import logging
import os
import grp
import pwd
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

from .config import get_git_host, get_token_root

logger = logging.getLogger(__name__)


def username_auth(username: str) -> bool:
    username = (username or "").strip()
    if not username:
        return False
    try:
        user_info = pwd.getpwnam(username)
    except KeyError:
        return False
    allowed_group = os.environ.get("AUTOBUILD_ALLOWED_GROUP", "scm-bmc")
    try:
        target_group = grp.getgrnam(allowed_group)
    except KeyError:
        return False
    target_gid = target_group.gr_gid
    if user_info.pw_gid == target_gid:
        return True
    if username in target_group.gr_mem:
        return True
    for group in grp.getgrall():
        if group.gr_gid == target_gid and username in group.gr_mem:
            return True
    return False


def token_path_for_user(username: str) -> Path:
    return get_token_root() / f"{username}.token"


def normalize_token_perms(token_root: Path, token_path: Path, create_root: bool = False) -> None:
    group_name = os.environ.get("AUTOBUILD_ALLOWED_GROUP", "scm-bmc")
    try:
        gid = grp.getgrnam(group_name).gr_gid
    except KeyError:
        gid = None
    if create_root:
        try:
            token_root.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning("Failed to create token root %s: %s", token_root, exc)
        try:
            os.chmod(token_root, 0o2770)
        except PermissionError:
            pass
        if gid is not None:
            try:
                os.chown(token_root, -1, gid)
            except PermissionError:
                pass
    if token_path.exists():
        if gid is not None:
            try:
                os.chown(token_path, -1, gid)
            except PermissionError:
                pass
        try:
            os.chmod(token_path, 0o640)
        except PermissionError:
            pass


def _percent_encode(value: str) -> str:
    return quote(value, safe="")


def _normalize_git_host(raw: Optional[str]) -> str:
    value = (raw or "").strip()
    if "://" in value:
        parsed = urlparse(value)
        value = parsed.netloc
    value = value.rstrip("/")
    if not value or "/" in value:
        raise ValueError("Invalid git host")
    return value


def _run_git_config(owner: str, home_dir: Path, key: str, value: str) -> None:
    preferred_git = Path("/usr/bin/git")
    git_bin = str(preferred_git) if preferred_git.exists() else "git"
    cmd = [
        "sudo",
        "-n",
        "-u",
        owner,
        "env",
        f"HOME={home_dir}",
        f"USER={owner}",
        "PATH=/usr/bin:/bin",
        git_bin,
        "config",
        "--global",
        key,
        value,
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"git config failed for {key}: {stderr or 'exit code ' + str(result.returncode)}")


def _write_user_git_credentials_file_via_sudo(owner: str, home_dir: Path, content: str) -> None:
    """
    Write credentials file as the target user using sudo, with stdin carrying the secret.
    """
    cmd = [
        "sudo",
        "-n",
        "-u",
        owner,
        "env",
        f"HOME={home_dir}",
        f"USER={owner}",
        "PATH=/usr/bin:/bin",
        "bash",
        "-lc",
        r'''
set -euo pipefail
CONF_DIR="${HOME}/.config/autobuild"
mkdir -p "${CONF_DIR}"
chmod 700 "${CONF_DIR}"
umask 077
tmp="$(mktemp "${CONF_DIR}/git-credentials.tmpXXXXXX")"
cat > "${tmp}"
chmod 600 "${tmp}"
mv -f "${tmp}" "${CONF_DIR}/git-credentials"
''',
    ]
    result = subprocess.run(cmd, input=content, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"failed to write git credentials: {stderr or 'exit code ' + str(result.returncode)}")


def has_gitlab_token(username: str) -> bool:
    data = load_user_tokens(username)
    if not data:
        return False
    return bool((data.get("primary") or "").strip() or (data.get("secondary") or "").strip() or (data.get("raw") or "").strip())


def write_gitlab_token(username: str, token: str) -> None:
    token = (token or "").strip()
    if not token:
        raise ValueError("Token is required")
    # Legacy: store as primary token
    save_user_tokens(username, token, None)


def load_user_tokens(username: str) -> dict:
    path = token_path_for_user(username)
    if not path.exists() or not path.is_file():
        return {}
    try:
        normalize_token_perms(path.parent, path, create_root=False)
        content = path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning("Failed to read token for user %s: %s", username, exc)
        return {}
    if not content:
        return {}
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return {
                "primary": (parsed.get("gitlab_token_primary") or parsed.get("primary") or "").strip(),
                "secondary": (parsed.get("gitlab_token_secondary") or parsed.get("secondary") or "").strip(),
                "raw": content,
            }
    except Exception:
        pass
    return {"primary": content, "secondary": "", "raw": content}


def save_user_tokens(username: str, token_primary: Optional[str], token_secondary: Optional[str]) -> None:
    token_primary = (token_primary or "").strip()
    token_secondary = (token_secondary or "").strip()
    if not token_primary and not token_secondary:
        raise ValueError("At least one token is required")
    root = get_token_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o2770)
    except PermissionError:
        pass
    try:
        gid = grp.getgrnam(os.environ.get("AUTOBUILD_ALLOWED_GROUP", "scm-bmc")).gr_gid
    except KeyError:
        gid = None
    if gid is not None:
        try:
            os.chown(root, -1, gid)
        except PermissionError:
            pass
    path = token_path_for_user(username)
    tmp_path = path.with_suffix(".tmp")
    payload = json.dumps(
        {
            "gitlab_token_primary": token_primary or "",
            "gitlab_token_secondary": token_secondary or "",
        }
    )
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, path)
        if gid is not None:
            try:
                os.chown(path, -1, gid)
            except PermissionError:
                pass
        normalize_token_perms(root, path, create_root=True)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


def setup_user_git_credentials(owner: str, token_value: str, git_host: Optional[str] = None, git_username: Optional[str] = None) -> None:
    token_value = (token_value or "").strip()
    if not token_value:
        raise ValueError("Token is required for git credentials")
    git_host = _normalize_git_host(git_host or get_git_host() or "gitlab.example.com")
    user_info = pwd.getpwnam(owner)
    home_dir = Path(user_info.pw_dir)
    effective_username = git_username or owner

    encoded_username = _percent_encode(effective_username)
    encoded_token = _percent_encode(token_value)
    credentials_path = home_dir / ".config" / "autobuild" / "git-credentials"
    content = f"https://{encoded_username}:{encoded_token}@{git_host}\n"

    _write_user_git_credentials_file_via_sudo(owner, home_dir, content)

    helper_value = f"store --file {credentials_path}"
    _run_git_config(owner, home_dir, "credential.helper", helper_value)


def try_setup_user_git_credentials(
    owner: str, token_value: str, git_host: Optional[str] = None, git_username: Optional[str] = None
) -> tuple[bool, Optional[str]]:
    try:
        setup_user_git_credentials(owner, token_value, git_host=git_host, git_username=git_username)
        return True, None
    except Exception as exc:  # pragma: no cover - defensive
        msg = str(exc)
        truncated = msg[:300] if len(msg) > 300 else msg
        logger.warning("Failed to configure git credentials for user %s: %s", owner, truncated)
        return False, truncated


def save_gitlab_token(username: str, token: str) -> Optional[str]:
    try:
        write_gitlab_token(username, token)
    except Exception as exc:  # pragma: no cover - defensive
        return str(exc)
    return None
