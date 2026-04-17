import os
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import SplitResult, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from markupsafe import Markup

from . import auth
from .app_settings import app_settings
from .config import get_git_host
from .csrf import get_or_create_token
from .version import __version__ as APP_VERSION


class TemplateUser(str):
    """String subclass that also exposes `.username` for Jinja templates."""

    @property
    def username(self) -> str:  # pragma: no cover - trivial accessor
        return self


def _normalize_base_path(raw_value: Optional[str]) -> str:
    value = (raw_value or "").strip()
    if not value or value == "/":
        return ""
    return "/" + value.strip("/")


APP_BASE_PATH = _normalize_base_path(os.getenv("AUTOBUILD_BASE_PATH"))


def get_app_base_path() -> str:
    return APP_BASE_PATH


def app_path(path: str = "/") -> str:
    if not path:
        path = "/"
    if path.startswith(("http://", "https://", "//")):
        return path

    parts = urlsplit(path)
    raw_path = parts.path or "/"
    if not raw_path.startswith("/"):
        raw_path = f"/{raw_path}"

    if APP_BASE_PATH and raw_path not in {APP_BASE_PATH, f"{APP_BASE_PATH}/"}:
        prefixed_path = f"{APP_BASE_PATH}/" if raw_path == "/" else f"{APP_BASE_PATH}{raw_path}"
    else:
        prefixed_path = raw_path

    return urlunsplit(SplitResult("", "", prefixed_path, parts.query, parts.fragment))


def redirect_to(path: str, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(url=app_path(path), status_code=status_code)


templates = Jinja2Templates(directory="templates")
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["app_path"] = app_path
templates.env.globals["app_base_path"] = APP_BASE_PATH


def _csrf_input(token: str = "") -> Markup:
    """Generate a hidden CSRF input field for use in templates: {{ csrf_input(csrf_token) }}"""
    return Markup(f'<input type="hidden" name="csrf_token" value="{token}">')


templates.env.globals["csrf_input"] = _csrf_input


def format_datetime_taipei(value: Optional[str]) -> str:
    """
    統一的時間格式化函數
    將 ISO 格式時間轉換為台北時區的可讀格式
    使用此函數避免時區二次轉換問題
    """
    if not value:
        return "-"
    text = str(value).strip()
    if not text:
        return "-"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone(app_settings.tz)
    return local_dt.strftime("%Y-%m-%d %H:%M:%S")


# 開放給模板使用的過濾器（保持向後相容性）
templates.env.filters["to_taipei"] = format_datetime_taipei


def render_page(
    request,
    template_name: str,
    *,
    current_page: str = "",
    token_ok: Optional[bool] = None,
    status_code: int = 200,
    **ctx,
):
    raw_user = request.session.get("user")
    session_user = TemplateUser(raw_user) if raw_user else None
    if token_ok is None:
        token_ok = True
        if session_user:
            try:
                token_ok = auth.has_gitlab_token(session_user)
            except Exception:
                token_ok = False
    base = {
        "request": request,
        "user": session_user,
        "current_page": current_page or "",
        "git_host": get_git_host(),
        "token_ok": token_ok,
        "csrf_token": get_or_create_token(request),
        "app_base_path": APP_BASE_PATH,
    }
    base.update(ctx)
    # Ensure user is always a TemplateUser (or None) even if ctx provided its own value.
    final_user = base.get("user") or session_user
    if final_user and not isinstance(final_user, TemplateUser):
        final_user = TemplateUser(str(final_user))
    base["user"] = final_user
    return templates.TemplateResponse(template_name, base, status_code=status_code)
