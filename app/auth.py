import os
import grp
import pwd
from typing import Optional


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


def save_gitlab_token(username: str, token: str) -> Optional[str]:
    if not token:
        return "Token is required"
    home = os.path.expanduser(f"~{username}")
    target_dir = os.path.join(home, ".autobuild")
    token_path = os.path.join(target_dir, "gitlab_token")
    os.makedirs(target_dir, exist_ok=True)
    try:
        if os.name == "posix":
            os.chmod(target_dir, 0o700)
    except PermissionError:
        pass
    try:
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(token.strip())
        if os.name == "posix":
            os.chmod(token_path, 0o600)
    except Exception as exc:  # pragma: no cover - defensive
        return str(exc)
    return None
