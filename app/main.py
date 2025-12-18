import asyncio
import os
from pathlib import Path
from typing import Dict, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import auth, db, jobs
from .config import get_jobs_root, get_secret_key


app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=get_secret_key(), session_cookie="autobuild_session")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

db.ensure_db()


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
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login_post(request: Request, username: str = Form(...)):
    if auth.username_auth(username):
        request.session["user"] = username
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Unauthorized user (check Linux account/group)"},
        status_code=401,
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    return templates.TemplateResponse("settings.html", {"request": request, "saved": False, "error": None})


@app.post("/settings", response_class=HTMLResponse)
async def settings_post(request: Request, token: str = Form(...)):
    redirect = require_login(request)
    if redirect:
        return redirect
    username = get_current_user(request)
    err = auth.save_gitlab_token(username, token)
    if err:
        return templates.TemplateResponse("settings.html", {"request": request, "saved": False, "error": err})
    return templates.TemplateResponse("settings.html", {"request": request, "saved": True, "error": None})


@app.get("/new", response_class=HTMLResponse)
async def new_job_page(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    return templates.TemplateResponse("new_job.html", {"request": request})


@app.post("/new")
async def create_job(
    request: Request,
    background_tasks: BackgroundTasks,
    repo_url: str = Form(...),
    ref: str = Form(...),
    machine: str = Form(...),
    target: str = Form(...),
):
    redirect = require_login(request)
    if redirect:
        return redirect
    username = get_current_user(request)
    job_id = jobs.create_job(username, repo_url, ref, machine, target)
    background_tasks.add_task(jobs.start_job_runner, job_id, username, repo_url, ref, machine, target)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: int):
    redirect = require_login(request)
    if redirect:
        return redirect
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    artifact_list = jobs.list_artifacts(job_id)
    return templates.TemplateResponse(
        "job.html",
        {"request": request, "job": job, "artifacts": artifact_list},
    )


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
