from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..crud_settings import get_system_settings, update_system_settings
from ..database import SessionLocal
from ..web import render_page

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _current_user(request: Request):
    return request.session.get("user")


def _require_login(request: Request) -> Optional[RedirectResponse]:
    if not _current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


@router.get("/settings")
async def settings_page(request: Request, db: Session = Depends(get_db)):
    redirect = _require_login(request)
    if redirect:
        return redirect
    settings = get_system_settings(db)
    token_ok = bool(settings.gitlab_token)
    return render_page(
        request,
        "settings.html",
        current_page="settings",
        token_ok=token_ok,
        settings=settings,
        status_code=200,
    )


@router.post("/settings/update")
async def update_settings(
    request: Request,
    db: Session = Depends(get_db),
    prune_days: int = Form(...),
    delete_days: int = Form(...),
    gitlab_token: str = Form(""),
    disk_min: int = Form(...),
):
    redirect = _require_login(request)
    if redirect:
        return redirect
    errors = []
    if prune_days < 0:
        errors.append("Prune days must be 0 or greater.")
    if delete_days < 0:
        errors.append("Delete days must be 0 or greater.")
    if disk_min < 0:
        errors.append("Disk threshold must be 0 or greater.")
    if errors:
        content = '<div class="alert alert-danger mb-0" role="alert">{}</div>'.format(" ".join(errors))
        return HTMLResponse(content=content, status_code=400)
    updated = update_system_settings(
        db,
        prune_days_age=prune_days,
        delete_days_age=delete_days,
        gitlab_token=gitlab_token,
        disk_min_free_gb=disk_min,
    )
    content = (
        '<div class="alert alert-success mb-0" role="alert">'
        "Settings saved. "
        f"Prune after {updated.prune_days_age} days; delete after {updated.delete_days_age} days."
        "</div>"
    )
    return HTMLResponse(content=content, status_code=200)
