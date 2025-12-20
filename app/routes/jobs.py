import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import jobs, db
from ..auth import username_auth
from ..config import get_presets_root
from ..recipes_catalog import list_recipes, load_recipe_yaml, recipe_path_from_id
from ..web import render_page

router = APIRouter()
logger = logging.getLogger(__name__)


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
    presets_root = get_presets_root()
    recipes = list_recipes(presets_root)
    return render_page(
        request,
        "new_job.html",
        current_page="new",
        recipes=recipes,
        presets_root=str(presets_root),
        recipes_count=len(recipes),
        status_code=200,
        user=user,
        token_ok=None,
    )


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
):
    redirect = _require_login(request)
    if redirect:
        return redirect
    user = _current_user(request)
    if not username_auth(user):
        return render_page(request, "new_job.html", current_page="new", status_code=403, error="Unauthorized user", recipes=list_recipes(get_presets_root()), user=user, token_ok=None)
    presets_root = get_presets_root()
    recipes = list_recipes(presets_root)
    try:
        form = await request.form()
    except Exception:
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=400,
            error="Invalid form submission",
            recipes=recipes,
            user=user,
            token_ok=None,
        )
    recipe_id_val = str(form.get("recipe_id") or "").strip()
    note = str(form.get("note") or "").strip()
    allowed_fields = {"recipe_id", "note"}
    extras = [k for k in form.keys() if k not in allowed_fields]
    if extras:
        extras_sorted = ", ".join(sorted(extras))
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=400,
            error=f"Unexpected fields: {extras_sorted}",
            recipes=recipes,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
        )
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
            status_code=404,
            error="Recipe not found",
            recipes=recipes,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
        )
    try:
        recipe_yaml = load_recipe_yaml(presets_root, recipe_id_val)
    except Exception as exc:
        logger.warning("Failed to load recipe %s: %s", recipe_id_val, exc)
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=500,
            error="Failed to load recipe contents",
            recipes=recipes,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
        )
    if not isinstance(recipe_yaml, str):
        logger.error("Recipe YAML for %s is not a string (type=%s)", recipe_id_val, type(recipe_yaml))
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=500,
            error="Recipe content invalid",
            recipes=recipes,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
        )
    created_at = jobs.now_iso()
    try:
        job_id = jobs.create_job(user, recipe_id_val, recipe_yaml, note, created_at=created_at)
    except Exception as exc:
        logger.error("Failed to create job for user %s recipe %s: %s", user, recipe_id_val, exc)
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=500,
            error="Failed to create job",
            recipes=recipes,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
        )

    snapshot = {
        "recipe_id": recipe_id_val,
        "raw_recipe_yaml": recipe_yaml,
        "note": note,
        "created_by": user,
        "created_at": created_at,
        "status": jobs.STATUS_PENDING,
    }
    spec = {
        "schema_version": 2,
        "job_id": job_id,
        "created_by": user,
        "created_at": created_at,
        "status": jobs.STATUS_PENDING,
        "recipe_id": recipe_id_val,
        "raw_recipe_yaml": recipe_yaml,
        "note": note,
        "snapshot": snapshot,
    }
    try:
        jobs.write_job_spec(job_id, spec)
    except Exception as exc:
        logger.error("Failed to write job spec for %s: %s", job_id, exc)
        try:
            db.update_job_status(job_id, jobs.STATUS_FAILED, finished_at=jobs.now_iso(), exit_code=-1)
        except Exception:
            logger.warning("Failed to mark job %s as failed after spec write error", job_id)
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=500,
            error="Failed to persist job snapshot",
            recipes=recipes,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
        )
    background_tasks.add_task(jobs.start_job_runner, job_id)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)
