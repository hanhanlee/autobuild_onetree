from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..config import get_token_root
from ..web import render_page

router = APIRouter()


def _current_user(request: Request):
    return request.session.get("user")


@router.get("/token")
async def token_status(request: Request):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    token_ok = None  # let render_page compute if None
    return render_page(
        request,
        "token.html",
        current_page="token",
        user=user,
        token_ok=token_ok,
        token_root=str(get_token_root()),
        status_code=200,
    )
