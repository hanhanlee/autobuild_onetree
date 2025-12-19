from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import jobs, db
from ..auth import username_auth
from ..config import get_presets_root
from ..presets import load_presets_for_user
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
    repo_url: str = Form(...),
    ref: str = Form(...),
    machine: str = Form(""),
    target: str = Form(""),
    preset: str = Form("__manual__"),
    recipe_id: str = Form(""),
):
    redirect = _require_login(request)
    if redirect:
        return redirect
    user = _current_user(request)
    if not username_auth(user):
        return render_page(request, "new_job.html", current_page="new", status_code=403, error="Unauthorized user", recipes=list_recipes(get_presets_root()), user=user, token_ok=None)
    presets_root = get_presets_root()
    recipes = list_recipes(presets_root)
    preset_name = preset or "__manual__"
    resolved_preset = None
    recipe_id_val = str(recipe_id).strip()
    effective_machine = machine.strip()
    effective_target = target.strip()
    if preset_name != "__manual__":
        preset_map = load_presets_for_user(user)
        if preset_name not in preset_map:
            return render_page(
                request,
                "new_job.html",
                current_page="new",
                status_code=400,
                error=f"Preset '{preset_name}' not found",
                recipes=recipes,
                user=user,
                token_ok=None,
                repo_url=repo_url,
                ref=ref,
                machine=machine,
                target=target,
                preset=preset_name,
                recipe_id=recipe_id,
            )
        resolved_preset = preset_map[preset_name]
        if not effective_machine and resolved_preset.default_machine:
            effective_machine = resolved_preset.default_machine
        if not effective_target:
            effective_target = resolved_preset.default_bitbake_target
    # machine/target required only when no recipe provided
    if not recipe_id_val:
        if not effective_machine:
            return render_page(
                request,
                "new_job.html",
                current_page="new",
                status_code=400,
                error="machine is required (either fill it or use a preset with default_machine)",
                recipes=recipes,
                user=user,
                token_ok=None,
                repo_url=repo_url,
                ref=ref,
                machine=machine,
                target=target,
                preset=preset_name,
                recipe_id=recipe_id,
            )
        if not effective_target:
            return render_page(
                request,
                "new_job.html",
                current_page="new",
                status_code=400,
                error="target is required (either fill it or use a preset with default_bitbake_target)",
                recipes=recipes,
                user=user,
                token_ok=None,
                repo_url=repo_url,
                ref=ref,
                machine=machine,
                target=target,
                preset=preset_name,
                recipe_id=recipe_id,
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
                repo_url=repo_url,
                ref=ref,
                machine=machine,
                target=target,
                preset=preset_name,
                recipe_id=recipe_id_val,
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
                repo_url=repo_url,
                ref=ref,
                machine=machine,
                target=target,
                preset=preset_name,
                recipe_id=recipe_id_val,
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
                repo_url=repo_url,
                ref=ref,
                machine=machine,
                target=target,
                preset=preset_name,
                recipe_id=recipe_id_val,
            )
        recipe_snapshot = {
            "id": recipe_id_val,
            "yaml": recipe_yaml,
        }
    created_at = jobs.now_iso()
    job_id = jobs.create_job(user, repo_url, ref, effective_machine, effective_target, created_at=created_at)
    effective_block = {
        "repo_url": repo_url,
        "ref": ref,
        "machine": effective_machine,
        "target": effective_target,
    }
    if recipe_snapshot:
        effective_block["recipe"] = recipe_snapshot

    spec = {
        "schema_version": 1,
        "job_id": job_id,
        "created_by": user,
        "created_at": created_at,
        "preset_name": preset_name,
        "overrides": {
            "repo_url": repo_url,
            "ref": ref,
            "machine": machine,
            "target": target,
            "recipe_id": recipe_id_val,
        },
        "effective": effective_block,
        "resolved_preset": resolved_preset.dict() if resolved_preset else None,
    }
    if recipe_snapshot:
        spec["recipe"] = recipe_snapshot
    jobs.write_job_spec(job_id, spec)
    background_tasks.add_task(jobs.start_job_runner, job_id, user, repo_url, ref, effective_machine, effective_target)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)
