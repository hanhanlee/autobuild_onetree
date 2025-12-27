import os
from typing import Optional

from fastapi.templating import Jinja2Templates

from .config import get_git_host
from .crud_settings import get_system_settings
from .database import SessionLocal

templates = Jinja2Templates(directory="templates")


def _system_tokens_configured() -> bool:
    try:
        with SessionLocal() as session:
            settings = get_system_settings(session)
        if not settings:
            return False
        tokens = [
            getattr(settings, "gitlab_token_primary", None),
            getattr(settings, "gitlab_token_secondary", None),
            getattr(settings, "gitlab_token", None),
        ]
        return any((t or "").strip() for t in tokens)
    except Exception:
        return False


def render_page(
    request,
    template_name: str,
    *,
    current_page: str = "",
    token_ok: Optional[bool] = None,
    status_code: int = 200,
    **ctx,
):
    user = request.session.get("user")
    if token_ok is None:
        token_ok = True
        if user:
            token_ok = _system_tokens_configured()
    base = {
        "request": request,
        "user": user,
        "current_page": current_page or "",
        "git_host": get_git_host(),
        "token_ok": token_ok,
    }
    base.update(ctx)
    return templates.TemplateResponse(template_name, base, status_code=status_code)
