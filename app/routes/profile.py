from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from .. import auth
from ..web import render_page

router = APIRouter()


def _current_user(request: Request) -> Optional[str]:
    return request.session.get("user")


def _require_login(request: Request) -> Optional[RedirectResponse]:
    if not _current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


@router.get("/profile")
async def profile_page(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    user = _current_user(request)
    token_data = auth.load_user_tokens(user)
    return render_page(
        request,
        "profile.html",
        current_page="profile",
        user=user,
        token_ok=auth.has_gitlab_token(user),
        token_primary=token_data.get("primary", "") if token_data else "",
        token_secondary=token_data.get("secondary", "") if token_data else "",
        git_username_primary=token_data.get("username_primary", "") if token_data else "",
        git_username_secondary=token_data.get("username_secondary", "") if token_data else "",
        status_code=200,
    )


@router.post("/profile")
async def profile_update(
    request: Request,
    git_token_primary: str = Form(""),
    git_token_secondary: str = Form(""),
    git_username_primary: str = Form(""),
    git_username_secondary: str = Form(""),
):
    redirect = _require_login(request)
    if redirect:
        return redirect
    user = _current_user(request)
    try:
        auth.save_user_tokens(user, git_token_primary, git_token_secondary, git_username_primary, git_username_secondary)
        message = "Tokens saved."
        return render_page(
            request,
            "profile.html",
            current_page="profile",
            user=user,
            token_ok=True,
            message=message,
            token_primary=(git_token_primary or "").strip(),
            token_secondary=(git_token_secondary or "").strip(),
            git_username_primary=(git_username_primary or "").strip(),
            git_username_secondary=(git_username_secondary or "").strip(),
            status_code=200,
        )
    except ValueError as exc:
        return render_page(
            request,
            "profile.html",
            current_page="profile",
            user=user,
            token_ok=False,
            error=str(exc),
            token_primary=(git_token_primary or "").strip(),
            token_secondary=(git_token_secondary or "").strip(),
            git_username_primary=(git_username_primary or "").strip(),
            git_username_secondary=(git_username_secondary or "").strip(),
            status_code=400,
        )
