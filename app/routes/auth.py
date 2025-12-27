import os
import pwd
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from .. import auth
from ..config import get_db_path, get_jobs_root, get_presets_root, get_token_root
from ..web import render_page


router = APIRouter()


def _current_user(request: Request) -> Optional[str]:
    return request.session.get("user")


def _prg(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


@router.get("/")
async def index(request: Request):
    user = _current_user(request)
    if not user:
        return _prg("/login")
    from ..dashboard import get_dashboard_context

    ctx = get_dashboard_context()
    return render_page(
        request,
        "dashboard.html",
        current_page="dashboard",
        user=user,
        token_ok=None,
        **ctx,
    )


@router.get("/login")
async def login_page(request: Request, error: Optional[str] = None, message: Optional[str] = None):
    error_msg = error or request.query_params.get("error")
    info_msg = message or request.query_params.get("message")
    status = 200 if not error_msg else 401
    return render_page(request, "login.html", current_page="", error=error_msg, message=info_msg, status_code=status)


@router.post("/login")
async def login_post(request: Request, username: str = Form(...)):
    username = (username or "").strip()
    if not auth.username_auth(username):
        return _prg("/login?error=Unauthorized+user+%28check+Linux+account%2Fgroup%29")
    try:
        pwd.getpwnam(username)
    except KeyError:
        return _prg("/login?error=User+does+not+exist+on+this+system.")
    request.session.pop("pending_user", None)
    request.session["user"] = username
    return _prg("/")


@router.get("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    request.session.pop("pending_user", None)
    return _prg("/login?message=You+have+been+logged+out.")


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
