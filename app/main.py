import asyncio
from contextlib import suppress
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from dotenv import load_dotenv

from . import db, models, projects
from .config import get_secret_key
from .database import engine
from .housekeeping import run_periodic_housekeeping
from .routers import settings as settings_routes
from .routes import profile as profile_routes
from .routes import auth as auth_routes
from .routes import codebases as codebases_routes
from .routes import jobs as jobs_routes
from .routes import presets as presets_routes
from .routes import projects as projects_routes
from .routes import recipes as recipes_routes
from .routes import token as token_routes
from .routes import help as help_routes


app = FastAPI()
# Load environment variables from .env at startup
load_dotenv()
app.add_middleware(SessionMiddleware, secret_key=get_secret_key(), session_cookie="autobuild_session")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(auth_routes.router)
app.include_router(profile_routes.router)
app.include_router(presets_routes.router)
app.include_router(projects_routes.router)
app.include_router(token_routes.router)
app.include_router(recipes_routes.router)
app.include_router(jobs_routes.router)
app.include_router(codebases_routes.router)
app.include_router(settings_routes.router)
app.include_router(help_routes.router, prefix="/help")

housekeeping_task: Optional[asyncio.Task] = None


@app.on_event("startup")
async def _start_housekeeping() -> None:
    global housekeeping_task
    if housekeeping_task is None:
        housekeeping_task = asyncio.create_task(run_periodic_housekeeping())


@app.on_event("shutdown")
async def _stop_housekeeping() -> None:
    global housekeeping_task
    if housekeeping_task:
        housekeeping_task.cancel()
        with suppress(asyncio.CancelledError):
            await housekeeping_task
        housekeeping_task = None


db.ensure_db()
models.Base.metadata.create_all(bind=engine)
projects.ensure_migrations()
