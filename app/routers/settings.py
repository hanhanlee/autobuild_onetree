import os
import subprocess
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
    return render_page(
        request,
        "settings.html",
        current_page="settings",
        token_ok=None,
        settings=settings,
        status_code=200,
    )


@router.post("/settings/update")
async def update_settings(
    request: Request,
    db: Session = Depends(get_db),
    prune_days: int = Form(...),
    delete_days: int = Form(...),
    gitlab_host: str = Form(""),
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
    current = get_system_settings(db)
    gitlab_host_val = gitlab_host.strip() or (current.gitlab_host or "")
    updated = update_system_settings(
        db,
        prune_days_age=prune_days,
        delete_days_age=delete_days,
        disk_min_free_gb=disk_min,
        gitlab_host=gitlab_host_val,
    )
    content = (
        '<div class="alert alert-success mb-0" role="alert">'
        "Settings saved. "
        f"Prune after {updated.prune_days_age} days; delete after {updated.delete_days_age} days."
        "</div>"
    )
    return HTMLResponse(content=content, status_code=200)


@router.post("/settings/cleanup")
async def cleanup_cache(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    data = {}
    try:
        form = await request.form()
        data = dict(form)
    except Exception:
        data = {}
    if not data:
        try:
            payload = await request.json()
            if isinstance(payload, dict):
                data = payload
        except Exception:
            data = {}
    target = str(data.get("target") or "").strip().lower()
    days_raw = data.get("days")
    try:
        days = int(days_raw)
    except Exception:
        days = -1
    if target not in {"sstate", "downloads"}:
        return HTMLResponse('<div class="alert alert-danger mb-0" role="alert">Invalid cleanup target.</div>', status_code=400)
    if days < 0:
        return HTMLResponse('<div class="alert alert-danger mb-0" role="alert">Days must be 0 or greater.</div>', status_code=400)

    if target == "sstate":
        base_path = os.environ.get("SSTATE_DIR") or "/work/sstate-cache"
        cmd = ["find", base_path, "-type", "f", "-atime", f"+{days}", "-delete"]
        label = "SState cache"
    else:
        base_path = os.environ.get("DL_DIR") or "/work/downloads"
        cmd = ["find", base_path, "-maxdepth", "1", "-type", "f", "-atime", f"+{days}", "-delete"]
        label = "Downloads"

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        msg = (result.stderr or "").strip() or "Cleanup failed."
        return HTMLResponse(f'<div class="alert alert-danger mb-0" role="alert">{msg}</div>', status_code=500)

    msg = f"{label} cleanup complete. Deleted files older than {days} days in {base_path}."
    return HTMLResponse(f'<div class="alert alert-success mb-0" role="alert">{msg}</div>', status_code=200)
