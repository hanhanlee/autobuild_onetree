from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import jobs, db
from ..auth import username_auth
from ..config import get_presets_root
from ..recipes_catalog import list_recipes, load_recipe_yaml, recipe_path_from_id
from ..web import render_page

router = APIRouter()


def _current_user(request: Request) -> Optional[str]:
    return request.session.get("user")


def _require_login(request: Request) -> Optional[RedirectResponse]:
    if not _current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


@router.get("/new", response_class=HTMLResponse)
async def new_job_page(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    user = _current_user(request)
    recipes = list_recipes(get_presets_root())
    return render_page(request, "new_job.html", current_page="new", recipes=recipes, status_code=200, user=user, token_ok=None)


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    user = _current_user(request)
    recent = db.list_recent_jobs(limit=50)
    return render_page(request, "jobs.html", current_page="jobs", jobs=recent, status_code=200, user=user, token_ok=None)


@router.post("/new")
async def create_job(
    request: Request,
    background_tasks: BackgroundTasks,
    recipe_id: str = Form(...),
    note: str = Form(""),
):
    redirect = _require_login(request)
    if redirect:
        return redirect
    user = _current_user(request)
    if not username_auth(user):
        return render_page(request, "new_job.html", current_page="new", status_code=403, error="Unauthorized user", recipes=list_recipes(get_presets_root()), user=user, token_ok=None)
    presets_root = get_presets_root()
    recipes = list_recipes(presets_root)
    recipe_id_val = str(recipe_id).strip()
    if not recipe_id_val:
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=400,
            error="recipe_id is required",
            recipes=recipes,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
        )
    recipe_snapshot = None
    if recipe_id_val:
        try:
            path = recipe_path_from_id(presets_root, recipe_id_val)
        except ValueError:
            return render_page(
                request,
                "new_job.html",
                current_page="new",
                status_code=400,
                error="Invalid recipe id",
                recipes=recipes,
                user=user,
                token_ok=None,
                recipe_id=recipe_id_val,
                note=note,
            )
        if not path.exists():
            return render_page(
                request,
                "new_job.html",
                current_page="new",
                status_code=400,
                error="Recipe not found",
                recipes=recipes,
                user=user,
                token_ok=None,
                recipe_id=recipe_id_val,
                note=note,
            )
        try:
            recipe_yaml = load_recipe_yaml(presets_root, recipe_id_val)
        except Exception:
            return render_page(
                request,
                "new_job.html",
                current_page="new",
                status_code=400,
                error="Failed to load recipe",
                recipes=recipes,
                user=user,
                token_ok=None,
                recipe_id=recipe_id_val,
                note=note,
            )
        recipe_snapshot = {
            "id": recipe_id_val,
            "yaml": recipe_yaml,
        }
    created_at = jobs.now_iso()
    job_id = jobs.create_job(user, "", "", "", "", created_at=created_at)
    effective_block = {}
    if recipe_snapshot:
        effective_block["recipe"] = recipe_snapshot

    spec = {
        "schema_version": 1,
        "job_id": job_id,
        "created_by": user,
        "created_at": created_at,
        "preset_name": "__manual__",
        "overrides": {
            "recipe_id": recipe_id_val,
            "note": note,
        },
        "effective": effective_block,
        "resolved_preset": None,
    }
    if recipe_snapshot:
        spec["recipe"] = recipe_snapshot
    if note:
        spec["note"] = note
    jobs.write_job_spec(job_id, spec)
    background_tasks.add_task(jobs.start_job_runner, job_id)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)
