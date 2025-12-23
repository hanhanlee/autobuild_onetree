from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import db, projects
from .config import get_secret_key
from .routes import auth as auth_routes
from .routes import codebases as codebases_routes
from .routes import jobs as jobs_routes
from .routes import presets as presets_routes
from .routes import projects as projects_routes
from .routes import recipes as recipes_routes
from .routes import token as token_routes


app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=get_secret_key(), session_cookie="autobuild_session")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(auth_routes.router)
app.include_router(presets_routes.router)
app.include_router(projects_routes.router)
app.include_router(token_routes.router)
app.include_router(recipes_routes.router)
app.include_router(jobs_routes.router)
app.include_router(codebases_routes.router)

db.ensure_db()
projects.ensure_migrations()
