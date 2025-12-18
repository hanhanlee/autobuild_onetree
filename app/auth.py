import logging
import os
import grp
import pwd
from pathlib import Path
from typing import Optional

from .config import get_token_root

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


def has_gitlab_token(username: str) -> bool:
    path = token_path_for_user(username)
    if not path.exists() or not path.is_file():
        return False
    try:
        normalize_token_perms(path.parent, path, create_root=False)
        content = path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning("Failed to read token for user %s: %s", username, exc)
        return False
    return bool(content)


def write_gitlab_token(username: str, token: str) -> None:
    token = (token or "").strip()
    if not token:
        raise ValueError("Token is required")
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
    try:
        tmp_path.write_text(token + "\n", encoding="utf-8")
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


def save_gitlab_token(username: str, token: str) -> Optional[str]:
    try:
        write_gitlab_token(username, token)
    except Exception as exc:  # pragma: no cover - defensive
        return str(exc)
    return None
