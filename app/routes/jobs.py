import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import RedirectResponse

from .. import jobs, db
from ..auth import username_auth
from ..web import render_page

router = APIRouter()
logger = logging.getLogger(__name__)
PRESETS_ROOT = Path("/opt/autobuild/workspace/presets")


def _current_user(request: Request) -> Optional[str]:
    return request.session.get("user")


def _require_login(request: Request) -> Optional[RedirectResponse]:
    if not _current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


def _sanitize_segment(value: str) -> Tuple[Optional[str], Optional[str]]:
    raw = value if value is not None else ""
    if raw != raw.strip():
        return None, "must not contain leading/trailing whitespace"
    raw = raw.strip()
    if not raw:
        return None, "is required"
    if ".." in raw or "/" in raw or "\\" in raw:
        return None, "must not contain path separators or '..'"
    if not re.match(r"^[A-Za-z0-9._-]+$", raw):
        return None, "Only [A-Za-z0-9._-] characters are allowed"
    return raw, None


def _parse_recipe_id(recipe_id: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    recipe_id = (recipe_id or "").strip()
    if not recipe_id:
        return None, None, "recipe_id is required"
    parts = recipe_id.split("/")
    if len(parts) != 2:
        return None, None, "recipe_id must look like <platform>/<project>"
    platform, project = parts
    platform, p_err = _sanitize_segment(platform)
    project, j_err = _sanitize_segment(project)
    if p_err:
        return None, None, f"Invalid platform: {p_err}"
    if j_err:
        return None, None, f"Invalid project: {j_err}"
    return platform, project, None


def _debug_context(last_error: Optional[str] = None, ignored_fields: Optional[List[str]] = None) -> Dict[str, object]:
    debug: Dict[str, object] = {
        "presets_root": str(PRESETS_ROOT),
        "recipes_count": 0,
        "last_error": last_error,
    }
    debug["ignored_fields"] = ignored_fields if ignored_fields is not None else []
    try:
        if not PRESETS_ROOT.exists():
            debug["last_error"] = debug["last_error"] or f"presets_root missing: {PRESETS_ROOT}"
            return debug
        if not PRESETS_ROOT.is_dir():
            debug["last_error"] = debug["last_error"] or f"presets_root is not a directory: {PRESETS_ROOT}"
            return debug
        count = 0
        for path in PRESETS_ROOT.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}:
                count += 1
        debug["recipes_count"] = count
    except Exception as exc:
        logger.warning("Failed to inspect presets_root: %s", exc)
        debug["last_error"] = debug["last_error"] or f"Failed to inspect presets_root: {exc}"
    return debug


def _list_recipes_from_presets() -> Tuple[List[Dict[str, str]], Dict[str, object]]:
    recipes: List[Dict[str, str]] = []
    debug = _debug_context()
    if debug["last_error"]:
        return recipes, debug
    try:
        for path in PRESETS_ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"}:
                continue
            rel = path.relative_to(PRESETS_ROOT)
            if len(rel.parts) != 2:
                continue
            platform = rel.parts[0]
            project = path.stem
            rid = f"{platform}/{project}"
            label = rid
            recipes.append({"id": rid, "label": label})
        recipes.sort(key=lambda r: r["id"])
    except Exception as exc:
        logger.warning("Failed to list recipes: %s", exc)
        debug["last_error"] = debug["last_error"] or f"Failed to list recipes: {exc}"
    debug["recipes_count"] = len(recipes)
    return recipes, debug


def _load_recipe_yaml(platform: str, project: str) -> Tuple[Optional[str], Optional[str]]:
    primary = PRESETS_ROOT / platform / f"{project}.yaml"
    fallback = PRESETS_ROOT / platform / f"{project}.yml"
    path = primary if primary.exists() else fallback
    if not path.exists():
        return None, f"Recipe not found at {primary} or {fallback}"
    try:
        return path.read_text(encoding="utf-8"), None
    except Exception as exc:
        return None, f"Failed to read recipe: {exc}"


@router.get("/new")
async def new_job_page(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    user = _current_user(request)
    recipes, debug_ctx = _list_recipes_from_presets()
    error_msg = debug_ctx.get("last_error")
    return render_page(
        request,
        "new_job.html",
        current_page="new",
        recipes=recipes,
        presets_root=str(PRESETS_ROOT),
        recipes_count=debug_ctx.get("recipes_count", 0),
        last_error=error_msg,
        debug_context=debug_ctx,
        status_code=200,
        user=user,
        token_ok=None,
        error=error_msg,
    )


@router.get("/jobs")
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
    recipes, debug_ctx = _list_recipes_from_presets()
    ignored_fields: List[str] = []
    if not username_auth(user):
        debug_ctx["last_error"] = debug_ctx.get("last_error")
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=403,
            error="Unauthorized user",
            recipes=recipes,
            presets_root=str(PRESETS_ROOT),
            recipes_count=debug_ctx.get("recipes_count", 0),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
        )
    try:
        form = await request.form()
    except Exception:
        debug_ctx["last_error"] = debug_ctx.get("last_error") or "Invalid form submission"
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=400,
            error="Invalid form submission",
            recipes=recipes,
            presets_root=str(PRESETS_ROOT),
            recipes_count=debug_ctx.get("recipes_count", 0),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
        )
    recipe_id_val = str(form.get("recipe_id") or "").strip()
    note = str(form.get("note") or "").strip()
    allowed_fields = {"recipe_id", "note"}
    extras = [k for k in form.keys() if k not in allowed_fields]
    if extras:
        ignored_fields = sorted(extras)
        debug_ctx["ignored_fields"] = ignored_fields
    if not recipe_id_val:
        debug_ctx["last_error"] = debug_ctx.get("last_error") or "recipe_id is required"
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=400,
            error="recipe_id is required",
            recipes=recipes,
            presets_root=str(PRESETS_ROOT),
            recipes_count=debug_ctx.get("recipes_count", 0),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
        )

    platform, project, rid_error = _parse_recipe_id(recipe_id_val)
    if rid_error:
        debug_ctx["last_error"] = debug_ctx.get("last_error") or rid_error
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=400,
            error=rid_error,
            recipes=recipes,
            presets_root=str(PRESETS_ROOT),
            recipes_count=debug_ctx.get("recipes_count", 0),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
        )

    recipe_yaml, load_err = _load_recipe_yaml(platform, project)
    if load_err:
        debug_ctx["last_error"] = debug_ctx.get("last_error") or load_err
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=400,
            error=load_err,
            recipes=recipes,
            presets_root=str(PRESETS_ROOT),
            recipes_count=debug_ctx.get("recipes_count", 0),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
        )

    if not isinstance(recipe_yaml, str):
        debug_ctx["last_error"] = debug_ctx.get("last_error") or "Recipe content invalid"
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=500,
            error="Recipe content invalid",
            recipes=recipes,
            presets_root=str(PRESETS_ROOT),
            recipes_count=debug_ctx.get("recipes_count", 0),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
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
        debug_ctx["last_error"] = debug_ctx.get("last_error") or "Failed to create job"
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=500,
            error="Failed to create job",
            recipes=recipes,
            presets_root=str(PRESETS_ROOT),
            recipes_count=debug_ctx.get("recipes_count", 0),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
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
        try:
            job_dir = jobs.job_dir(job_id)
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "raw_recipe.yaml").write_text(recipe_yaml if recipe_yaml.endswith("\n") else f"{recipe_yaml}\n", encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to persist raw_recipe.yaml for job %s: %s", job_id, exc)
    except Exception as exc:
        logger.error("Failed to write job spec for %s: %s", job_id, exc)
        try:
            db.update_job_status(job_id, jobs.STATUS_FAILED, finished_at=jobs.now_iso(), exit_code=-1)
        except Exception:
            logger.warning("Failed to mark job %s as failed after spec write error", job_id)
        debug_ctx["last_error"] = debug_ctx.get("last_error") or "Failed to persist job snapshot"
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=500,
            error="Failed to persist job snapshot",
            recipes=recipes,
            presets_root=str(PRESETS_ROOT),
            recipes_count=debug_ctx.get("recipes_count", 0),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
        )
    background_tasks.add_task(jobs.start_job_runner, job_id)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)
