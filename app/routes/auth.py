import os
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from .. import auth
from ..config import get_db_path, get_git_host, get_jobs_root, get_presets_root, get_token_root
from ..web import render_page


router = APIRouter()


def _current_user(request: Request) -> Optional[str]:
    return request.session.get("user")


def _prg(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


def _bool_param(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def _encode(value: str) -> str:
    return urllib.parse.quote_plus(value)


@router.get("/")
async def index(request: Request):
    user = _current_user(request)
    if not user:
        return _prg("/login")
    return _prg("/new")


@router.get("/login")
async def login_page(request: Request, error: Optional[str] = None):
    error_msg = error or request.query_params.get("error")
    return render_page(request, "login.html", current_page="", error=error_msg, status_code=200 if not error_msg else 401)


@router.post("/login")
async def login_post(request: Request, username: str = Form(...)):
    username = (username or "").strip()
    if auth.username_auth(username):
        if auth.has_gitlab_token(username):
            request.session.pop("pending_user", None)
            request.session["user"] = username
            return _prg("/new")
        request.session["pending_user"] = username
        request.session.pop("user", None)
        return _prg("/login/token")
    return _prg("/login?error=Unauthorized+user+%28check+Linux+account%2Fgroup%29")


@router.get("/login/token")
async def login_token_page(
    request: Request,
    error: Optional[str] = None,
    token_saved: Optional[str] = None,
    git_error: Optional[str] = None,
    git_ok: Optional[str] = None,
):
    if _current_user(request):
        return _prg("/new")
    pending = request.session.get("pending_user")
    if not pending:
        return _prg("/login")
    token_saved_flag = _bool_param(token_saved or request.query_params.get("token_saved")) or False
    git_configured = _bool_param(git_ok or request.query_params.get("git_ok"))
    git_error_msg = git_error or request.query_params.get("git_error")
    error_msg = error or request.query_params.get("error")
    return render_page(
        request,
        "login_token.html",
        current_page="token",
        error=error_msg,
        username=pending,
        token_saved=token_saved_flag,
        git_error=git_error_msg,
        git_credentials_configured=git_configured,
        status_code=200 if not error_msg else 400,
        git_credentials_error=git_error_msg,
    )


@router.post("/login/token")
async def login_token_post(request: Request, token: str = Form(...), username: str = Form(...)):
    pending = request.session.get("pending_user")
    if not pending:
        return _prg("/login")
    if username != pending:
        return _prg("/login/token?error=Username+mismatch")
    if not auth.username_auth(username):
        return _prg("/login?error=Unauthorized+user+%28check+Linux+account%2Fgroup%29")
    token = (token or "").strip()
    if not token:
        return _prg("/login/token?error=Token+is+required")
    try:
        auth.write_gitlab_token(username, token)
    except ValueError as exc:
        return _prg(f"/login/token?error={_encode(str(exc))}")
    except Exception:
        return _prg("/login/token?error=Failed+to+save+token")
    git_credentials_ok, git_error = auth.try_setup_user_git_credentials(username, token, git_host=get_git_host())
    if not git_credentials_ok:
        params = f"token_saved=1&git_ok=0&git_error={_encode(git_error or 'unknown error')}"
        return _prg(f"/login/token?{params}")
    request.session.pop("pending_user", None)
    request.session["user"] = username
    return _prg("/new")


@router.on_event("startup")
async def ensure_paths():
    get_jobs_root().mkdir(parents=True, exist_ok=True)
    get_presets_root().mkdir(parents=True, exist_ok=True)
    get_db_path().parent.mkdir(parents=True, exist_ok=True)
    token_root = get_token_root()
    token_root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(token_root, 0o2770)
    except PermissionError:
        pass
