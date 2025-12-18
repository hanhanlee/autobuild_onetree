import os
import grp
import pwd
from pathlib import Path
from typing import Optional

from .config import get_token_root


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


def has_gitlab_token(username: str) -> bool:
    path = token_path_for_user(username)
    if not path.exists() or not path.is_file():
        return False
    try:
        content = path.read_text(encoding="utf-8").strip()
    except Exception:
        return False
    return bool(content)


def write_gitlab_token(username: str, token: str) -> None:
    token = (token or "").strip()
    if not token:
        raise ValueError("Token is required")
    root = get_token_root()
    root.mkdir(parents=True, exist_ok=True)
    path = token_path_for_user(username)
    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(token + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
    try:
        os.chmod(path, 0o600)
    except PermissionError:
        try:
            os.chmod(path, 0o640)
        except PermissionError:
            pass


def save_gitlab_token(username: str, token: str) -> Optional[str]:
    try:
        write_gitlab_token(username, token)
    except Exception as exc:  # pragma: no cover - defensive
        return str(exc)
    return None
