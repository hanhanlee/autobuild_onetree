from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..presets import load_presets_for_user, summarize_presets
from ..web import redirect_to


router = APIRouter()


def _get_current_user(request: Request):
    return request.session.get("user")


@router.get("/api/presets")
async def list_presets(request: Request):
    user = _get_current_user(request)
    if not user:
        return redirect_to("/login")
    presets = load_presets_for_user(user)
    return summarize_presets(presets)
