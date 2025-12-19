import os
from typing import Optional

from fastapi.templating import Jinja2Templates

from . import auth
from .config import get_git_host

templates = Jinja2Templates(directory="templates")


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
            try:
                token_ok = auth.has_gitlab_token(user)
            except Exception:
                token_ok = False
    base = {
        "request": request,
        "user": user,
        "current_page": current_page or "",
        "git_host": get_git_host(),
        "token_ok": token_ok,
    }
    base.update(ctx)
    return templates.TemplateResponse(template_name, base, status_code=status_code)
