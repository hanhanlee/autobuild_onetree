import asyncio
import os
from pathlib import Path
from typing import Dict, Optional

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import auth, db, jobs, projects
from .config import get_db_path, get_git_host, get_jobs_root, get_presets_root, get_secret_key, get_token_root
from .presets import load_presets_for_user
from .routes import presets as presets_routes
from .routes import projects as projects_routes
from .routes import token as token_routes
from .routes import recipes as recipes_routes
from .routes import jobs as jobs_routes
from .web import render_page


app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=get_secret_key(), session_cookie="autobuild_session")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(presets_routes.router)
app.include_router(projects_routes.router)
app.include_router(token_routes.router)
app.include_router(recipes_routes.router)
app.include_router(jobs_routes.router)

db.ensure_db()
projects.ensure_migrations()


def get_current_user(request: Request) -> Optional[str]:
    return request.session.get("user")


def require_login(request: Request) -> Optional[RedirectResponse]:
    if not get_current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/new", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return render_page(request, "login.html", current_page="", error=None)


@app.post("/login")
async def login_post(request: Request, username: str = Form(...)):
    if auth.username_auth(username):
        if auth.has_gitlab_token(username):
            request.session.pop("pending_user", None)
            request.session["user"] = username
            return RedirectResponse(url="/new", status_code=303)
        request.session["pending_user"] = username
        return RedirectResponse(url="/login/token", status_code=303)
    return render_page(request, "login.html", current_page="", error="Unauthorized user (check Linux account/group)", status_code=401)


@app.get("/login/token", response_class=HTMLResponse)
async def login_token_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/new", status_code=303)
    pending = request.session.get("pending_user")
    if not pending:
        return RedirectResponse(url="/login", status_code=303)
    return render_page(
        request,
        "login_token.html",
        current_page="token",
        error=None,
        username=pending,
        token_saved=False,
        git_error=None,
        git_credentials_configured=None,
    )


@app.post("/login/token")
async def login_token_post(request: Request, token: str = Form(...), username: str = Form(...)):
    pending = request.session.get("pending_user")
    if not pending:
        return RedirectResponse(url="/login", status_code=303)
    if username != pending:
        return render_page(
            request,
            "login_token.html",
            current_page="token",
            error="Username mismatch",
            username=pending,
            token_saved=False,
            git_error=None,
            git_credentials_configured=None,
            status_code=400,
        )
    if not auth.username_auth(username):
        return render_page(
            request,
            "login_token.html",
            current_page="token",
            error="Unauthorized user (check Linux account/group)",
            username=username,
            token_saved=False,
            git_error=None,
            git_credentials_configured=None,
            status_code=401,
        )
    git_credentials_ok = False
    git_error = None
    try:
        auth.write_gitlab_token(username, token)
    except ValueError as exc:
        return render_page(
            request,
            "login_token.html",
            current_page="token",
            error=str(exc),
            username=username,
            token_saved=False,
            git_error=None,
            git_credentials_configured=None,
            status_code=400,
        )
    except Exception as exc:
        return render_page(
            request,
            "login_token.html",
            current_page="token",
            error="Failed to save token",
            username=username,
            token_saved=False,
            git_error=None,
            git_credentials_configured=None,
            status_code=500,
        )
    git_credentials_ok, git_error = auth.try_setup_user_git_credentials(username, token, git_host=get_git_host())
    error_msg = git_error or "unknown error"
    if not git_credentials_ok:
        return render_page(
            request,
            "login_token.html",
            current_page="token",
            error=None,
            username=username,
            token_saved=True,
            git_error=f"Token saved but failed to configure git credentials: {error_msg}",
            git_credentials_configured=False,
            status_code=200,
        )
    request.session.pop("pending_user", None)
    request.session["user"] = username
    return RedirectResponse(url="/new", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    return render_page(
        request,
        "settings.html",
        current_page="settings",
        saved=False,
        error=None,
        git_credentials_configured=None,
        git_credentials_error=None,
    )


@app.post("/settings", response_class=HTMLResponse)
async def settings_post(request: Request, token: str = Form(...)):
    redirect = require_login(request)
    if redirect:
        return redirect
    username = get_current_user(request)
    err = auth.save_gitlab_token(username, token)
    if err:
        return templates.TemplateResponse("settings.html", {"request": request, "saved": False, "error": err})
    git_credentials_ok, git_error = auth.try_setup_user_git_credentials(username, token, git_host=get_git_host())
    error_msg = git_error or "unknown error"
    context = {
        "saved": True,
        "error": None if git_credentials_ok else f"Token saved but failed to configure git credentials: {error_msg}",
        "git_credentials_configured": git_credentials_ok,
        "git_credentials_error": git_error,
    }
    return render_page(request, "settings.html", current_page="settings", status_code=200, **context)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: int):
    redirect = require_login(request)
    if redirect:
        return redirect
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    artifact_list = jobs.list_artifacts(job_id)
    return render_page(request, "job.html", current_page="jobs", job=job, artifacts=artifact_list)


@app.get("/api/jobs/{job_id}/artifacts")
async def api_artifacts(request: Request, job_id: int):
    redirect = require_login(request)
    if redirect:
        return redirect
    if not db.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return list(jobs.list_artifacts(job_id).values())


@app.get("/api/jobs/{job_id}/artifacts/{name}")
async def api_artifact_download(request: Request, job_id: int, name: str):
    redirect = require_login(request)
    if redirect:
        return redirect
    if not db.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    if Path(name).name != name:
        raise HTTPException(status_code=400, detail="Invalid name")
    path = jobs.job_dir(job_id) / "artifacts" / name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return StreamingResponse(path.open("rb"), headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/jobs/{job_id}/log/stream")
async def stream_log(request: Request, job_id: int):
    redirect = require_login(request)
    if redirect:
        return redirect
    if not db.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_stream():
        path = jobs.log_file(job_id)
        last_pos = 0
        while True:
            if await request.is_disconnected():
                break
            if path.exists():
                with path.open("r", encoding="utf-8", errors="ignore") as f:
                    f.seek(last_pos)
                    data = f.read()
                    if data:
                        last_pos = f.tell()
                        for line in data.splitlines():
                            yield f"data: {line}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/jobs/{job_id}/refresh")
async def refresh_job(request: Request, job_id: int):
    redirect = require_login(request)
    if redirect:
        return redirect
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.on_event("startup")
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
