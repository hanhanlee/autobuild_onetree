import os
from typing import Optional

from fastapi.templating import Jinja2Templates

from . import auth
from .config import get_git_host
from .version import __version__ as APP_VERSION


class TemplateUser(str):
    """String subclass that also exposes `.username` for Jinja templates."""

    @property
    def username(self) -> str:  # pragma: no cover - trivial accessor
        return self


templates = Jinja2Templates(directory="templates")
templates.env.globals["app_version"] = APP_VERSION


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
    }
    base.update(ctx)
    # Ensure user is always a TemplateUser (or None) even if ctx provided its own value.
    final_user = base.get("user") or session_user
    if final_user and not isinstance(final_user, TemplateUser):
        final_user = TemplateUser(str(final_user))
    base["user"] = final_user
    return templates.TemplateResponse(template_name, base, status_code=status_code)
