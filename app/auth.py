import os
import sys
from typing import Optional

try:
    import pam
except ImportError:  # pragma: no cover - dependency should be installed in prod
    pam = None


def pam_auth(username: str, password: str) -> bool:
    if sys.platform.startswith("linux") and pam is not None:
        p = pam.pam()
        return bool(p.authenticate(username, password))
    # Only allow PAM-backed login on Linux; deny elsewhere.
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

