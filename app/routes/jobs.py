import asyncio
import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse

from .. import db, jobs
from ..auth import username_auth
from ..crud_settings import get_system_settings
from ..database import SessionLocal
from ..config import get_jobs_root, get_presets_root, get_workspace_root
from ..recipes_catalog import list_recipes as catalog_list_recipes
from ..system import get_disk_usage
from ..web import render_page

router = APIRouter()
logger = logging.getLogger(__name__)
PRESETS_ROOT = get_presets_root()
WORKSPACES_ROOT = Path(get_workspace_root())


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


def _debug_context(
    last_error: Optional[str] = None,
    ignored_fields: Optional[List[str]] = None,
) -> Dict[str, object]:
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


def _list_recipes_from_presets() -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    debug = _debug_context()
    if debug["last_error"]:
        return [], debug
    try:
        recipes = catalog_list_recipes(PRESETS_ROOT)
    except Exception as exc:
        logger.warning("Failed to list recipes: %s", exc)
        debug["last_error"] = debug.get("last_error") or f"Failed to list recipes: {exc}"
        recipes = []
    debug["recipes_count"] = len(recipes)
    return recipes, debug


def _list_codebases() -> Tuple[List[Dict[str, Optional[str]]], Optional[str]]:
    codebases: List[Dict[str, Optional[str]]] = []
    err: Optional[str] = None
    try:
        if not WORKSPACES_ROOT.exists():
            err = f"workspaces_root missing: {WORKSPACES_ROOT}"
            return codebases, err
        if not WORKSPACES_ROOT.is_dir():
            err = f"workspaces_root is not a directory: {WORKSPACES_ROOT}"
            return codebases, err
        for child in WORKSPACES_ROOT.iterdir():
            if not child.is_dir():
                continue
            codebase_json = child / "codebase.json"
            if not codebase_json.exists():
                continue
            item = {"id": child.name, "label": child.name, "owner": None, "created_at": None, "last_used_at": None}
            try:
                data = json.loads(codebase_json.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("label"):
                    item["label"] = str(data.get("label"))
                if isinstance(data, dict):
                    item["owner"] = data.get("owner")
                    item["created_at"] = data.get("created_at")
                    item["last_used_at"] = data.get("last_used_at")
            except Exception:
                pass
            codebases.append(item)
        codebases.sort(key=lambda c: c.get("id") or "")
    except Exception as exc:
        logger.warning("Failed to list codebases: %s", exc)
        err = err or f"Failed to list codebases: {exc}"
    return codebases, err


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


def _safe_job_dir(job_id: int) -> Optional[Path]:
    try:
        jobs_root_path = get_jobs_root()
        root = jobs_root_path.resolve()
        target = (jobs_root_path / str(job_id)).resolve()
        print(f"--- DEBUG PATH CHECK ---", flush=True)
        print(f"Config Root: {jobs_root_path}", flush=True)
        print(f"Resolved Root: {root}", flush=True)
        print(f"Target Job: {target}", flush=True)
        if str(root) not in str(target):
            print(f"[ERROR] Path Mismatch! Root '{root}' not in '{target}'", flush=True)
            return None
        return target
    except Exception as exc:
        print(f"[safe_job_dir] exception: {exc}", flush=True)
        return None


def _parse_iso_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _format_duration(start: datetime, end: datetime) -> Optional[str]:
    delta = end - start
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return None
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    parts: List[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or hours:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def _job_duration_text(job: Dict[str, object]) -> Optional[str]:
    status = (job.get("status") or "").lower()
    if status in {"running", "pending"}:
        return "Running..."
    start = _parse_iso_dt(job.get("created_at"))
    end = _parse_iso_dt(job.get("finished_at") or job.get("updated_at") or job.get("started_at"))
    if not start or not end:
        return None
    return _format_duration(start, end)


def _clean_disk_usage(raw: Optional[object]) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    parts = re.split(r"recorded:\s*", text, flags=re.IGNORECASE)
    if len(parts) >= 2:
        text = parts[-1].strip()
    tokens = text.split()
    if tokens:
        return tokens[-1]
    return text or None


def _load_job_state(job_id: int) -> Dict[str, object]:
    try:
        data = jobs.load_job_spec(job_id) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _update_job_json(job_id: int, mutate_fn) -> bool:
    job_path = jobs.job_dir(job_id) / "job.json"
    try:
        existing = {}
        if job_path.exists():
            existing = json.loads(job_path.read_text(encoding="utf-8")) or {}
            if not isinstance(existing, dict):
                existing = {}
        data = existing
        mutate_fn(data)
        job_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = job_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(job_path)
        return True
    except Exception as exc:
        logger.warning("Failed to update job.json for %s: %s", job_id, exc)
        return False


@router.get("/new")
async def new_job_page(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    user = _current_user(request)
    recipes, debug_ctx = _list_recipes_from_presets()
    codebases, cb_error = _list_codebases()
    debug_ctx["workspaces_root"] = str(WORKSPACES_ROOT)
    debug_ctx["codebases_count"] = len(codebases)
    if cb_error and not debug_ctx.get("last_error"):
        debug_ctx["last_error"] = cb_error
    error_msg = debug_ctx.get("last_error")
    recent_jobs = db.list_recent_jobs(limit=20)
    return render_page(
        request,
        "new_job.html",
        current_page="new",
        recipes=recipes,
        presets_root=str(PRESETS_ROOT),
        recipes_count=debug_ctx.get("recipes_count", len(recipes)),
        last_error=error_msg,
        debug_context=debug_ctx,
        codebases=codebases,
        codebases_count=len(codebases),
        workspaces_root=str(WORKSPACES_ROOT),
        recent_jobs=recent_jobs,
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
    raw_status = (request.query_params.get("filter_status") or "all").lower()
    raw_time = (request.query_params.get("filter_time") or "all").lower()
    status_filter = None if raw_status in {"", "all"} else raw_status
    days_filter: Optional[int] = None
    if raw_time not in {"", "all"}:
        try:
            days_filter = int(raw_time)
        except Exception:
            days_filter = None
    taipei_tz = timezone(timedelta(hours=8))
    user = _current_user(request)
    recent = db.list_recent_jobs(limit=50, status=status_filter, days=days_filter)
    disk_usage = None
    try:
        disk_usage = get_disk_usage(str(WORKSPACES_ROOT))
    except Exception as exc:
        logger.warning("Failed to read disk usage: %s", exc)
    job_states: Dict[int, Dict[str, object]] = {}
    for job in recent:
        jid = job.get("id")
        if jid is None:
            continue
        raw_created = _parse_iso_dt(job.get("created_at"))
        if raw_created and raw_created.tzinfo is None:
            raw_created = raw_created.replace(tzinfo=timezone.utc)
        if raw_created:
            local_created = raw_created.astimezone(taipei_tz)
            job["local_created_at"] = local_created.strftime("%Y-%m-%d %H:%M:%S")
        else:
            job["local_created_at"] = "-"
        state = _load_job_state(int(jid))
        job_states[int(jid)] = {
            "disk_usage": state.get("disk_usage"),
            "disk_usage_clean": _clean_disk_usage(state.get("disk_usage")),
            "is_pruned": state.get("is_pruned"),
            "workspace_path": str(jobs.job_dir(int(jid)) / "workspace"),
        }
        job["duration"] = _job_duration_text(job)
    return render_page(
        request,
        "jobs.html",
        current_page="jobs",
        jobs=recent,
        disk_usage=disk_usage,
        job_states=job_states,
        filter_status=raw_status,
        filter_time=raw_time,
        status_code=200,
        user=user,
        token_ok=None,
    )


@router.get("/jobs/{job_id}")
async def job_detail(request: Request, job_id: int):
    redirect = _require_login(request)
    if redirect:
        return redirect
    job = db.get_job(job_id)
    if not job:
        return render_page(
            request,
            "error.html",
            current_page="jobs",
            status_code=404,
            title="Job not found",
            message="Job not found",
            user=_current_user(request),
            token_ok=None,
        )
    artifact_list = jobs.list_artifacts(job_id)
    return render_page(request, "job.html", current_page="jobs", job=job, artifacts=artifact_list)


@router.post("/jobs/{job_id}/pin")
async def pin_job(request: Request, job_id: int):
    redirect = _require_login(request)
    if redirect:
        return redirect
    job = db.get_job(job_id)
    if not job:
        return RedirectResponse(url="/jobs?error=not_found", status_code=303)
    try:
        form = await request.form()
    except Exception:
        form = {}
    pinned_raw = form.get("pinned") if hasattr(form, "get") else None
    if pinned_raw is None or str(pinned_raw).strip() == "":
        desired = not bool(job.get("pinned"))
    else:
        desired = str(pinned_raw).strip().lower() in {"1", "true", "yes", "on", "pin", "pinned"}
    db.set_job_pin(job_id, desired)
    referer = request.headers.get("referer") or "/jobs"
    return RedirectResponse(url=referer, status_code=303)


@router.post("/jobs/{job_id}/prune")
async def prune_job(request: Request, job_id: int):
    redirect = _require_login(request)
    if redirect:
        return redirect
    job = db.get_job(job_id)
    if not job:
        return RedirectResponse(url="/jobs?error=not_found", status_code=303)
    if job.get("pinned"):
        return RedirectResponse(url=f"/jobs/{job_id}?error=job_is_pinned", status_code=303)
    status_val = (job.get("status") or "").lower()
    if status_val not in {"success", "failed"}:
        return RedirectResponse(url=f"/jobs/{job_id}?error=not_finished", status_code=303)
    base_dir = _safe_job_dir(job_id)
    if base_dir is None:
        return RedirectResponse(url=f"/jobs/{job_id}?error=invalid_path", status_code=303)
    workspace_dir = (base_dir / "workspace").resolve()
    if workspace_dir.exists():
        try:
            shutil.rmtree(workspace_dir)
        except Exception as exc:
            logger.warning("Failed to prune workspace for job %s: %s", job_id, exc)
            return RedirectResponse(url=f"/jobs/{job_id}?error=prune_failed", status_code=303)
    def _mutate(data: Dict[str, object]) -> None:
        data["disk_usage"] = "Pruned"
        data["is_pruned"] = True
        snap = data.get("snapshot")
        if isinstance(snap, dict):
            snap["disk_usage"] = "Pruned"
            snap["is_pruned"] = True
            data["snapshot"] = snap

    _update_job_json(job_id, _mutate)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/stop")
async def stop_job(request: Request, job_id: int):
    redirect = _require_login(request)
    if redirect:
        return redirect
    job = db.get_job(job_id)
    if not job:
        return RedirectResponse(url="/jobs?error=not_found", status_code=303)
    status_val = (job.get("status") or "").lower()
    if status_val not in {"running", "pending"}:
        return RedirectResponse(url=f"/jobs/{job_id}?error=not_running", status_code=303)
    if not jobs.stop_job(job_id):
        return RedirectResponse(url=f"/jobs/{job_id}?error=stop_failed", status_code=303)
    return RedirectResponse(url=f"/jobs/{job_id}?success=stopped", status_code=303)


@router.post("/jobs/{job_id}/retry")
async def retry_job(request: Request, job_id: int):
    redirect = _require_login(request)
    if redirect:
        return redirect
    job = db.get_job(job_id)
    if not job:
        return RedirectResponse(url="/jobs?error=not_found", status_code=303)
    status_val = (job.get("status") or "").lower()
    if status_val in {"running", "pending"}:
        return RedirectResponse(url=f"/jobs/{job_id}?error=job_running", status_code=303)
    if not jobs.retry_job(job_id, owner=job.get("created_by") or job.get("owner")):
        return RedirectResponse(url=f"/jobs/{job_id}?error=retry_failed", status_code=303)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)



@router.post("/jobs/{job_id}/delete")
async def delete_job(request: Request, job_id: int):
    redirect = _require_login(request)
    if redirect:
        return redirect
    job = db.get_job(job_id)
    if not job:
        return RedirectResponse(url="/jobs?error=not_found", status_code=303)
    if job.get("pinned"):
        return RedirectResponse(url=f"/jobs/{job_id}?error=job_is_pinned", status_code=303)

    job_dir = _safe_job_dir(job_id)
    if job_dir and job_dir.exists():
        try:
            shutil.rmtree(job_dir)
        except Exception as exc:
            logger.warning("Failed to delete job dir %s: %s", job_dir, exc)
            return RedirectResponse(url=f"/jobs/{job_id}?error=delete_failed", status_code=303)

    try:
        if hasattr(db, "delete_job"):
            db.delete_job(job_id)
    except Exception as exc:
        logger.warning("Failed to delete DB record for job %s: %s", job_id, exc)
        return RedirectResponse(url=f"/jobs/{job_id}?error=delete_failed", status_code=303)

    return RedirectResponse(url="/jobs", status_code=303)


@router.post("/jobs/batch")
async def jobs_batch_action(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    try:
        form = await request.form()
    except Exception:
        return RedirectResponse(url="/jobs?error=invalid_form", status_code=303)
    action = (str(form.get("action") or "")).lower()
    if action not in {"prune", "delete"}:
        return RedirectResponse(url="/jobs?error=invalid_action", status_code=303)
    raw_ids = form.getlist("job_ids")
    job_ids: List[int] = []
    for raw in raw_ids:
        try:
            job_ids.append(int(raw))
        except Exception:
            continue
    if not job_ids:
        return RedirectResponse(url="/jobs?error=no_selection", status_code=303)

    processed = 0
    skipped_pinned = 0

    for job_id in job_ids:
        job = db.get_job(job_id)
        if not job:
            continue
        if job.get("pinned"):
            skipped_pinned += 1
            continue
        job_dir = _safe_job_dir(job_id)
        if action == "prune":
            status_val = (job.get("status") or "").lower()
            if status_val not in {"success", "failed"}:
                continue
            if job_dir:
                workspace_dir = job_dir / "workspace"
                if workspace_dir.exists():
                    try:
                        shutil.rmtree(workspace_dir)
                    except Exception:
                        continue

            def _mutate(data: Dict[str, object]) -> None:
                data["disk_usage"] = "Pruned"
                data["is_pruned"] = True
                snap = data.get("snapshot")
                if isinstance(snap, dict):
                    snap["disk_usage"] = "Pruned"
                    snap["is_pruned"] = True
                    data["snapshot"] = snap

            _update_job_json(job_id, _mutate)
            processed += 1
        else:
            if job_dir and job_dir.exists():
                try:
                    shutil.rmtree(job_dir)
                except Exception:
                    continue
            try:
                if hasattr(db, "delete_job"):
                    db.delete_job(job_id)
            except Exception:
                continue
            processed += 1

    msg = f"{action.title()}d {processed} jobs"
    if skipped_pinned:
        msg += f" ({skipped_pinned} pinned jobs skipped)"
    return RedirectResponse(url=f"/jobs?success={quote_plus(msg)}", status_code=303)


@router.post("/new")
async def create_job(
    request: Request,
    background_tasks: BackgroundTasks,
):
    redirect = _require_login(request)
    if redirect:
        return redirect
    user = _current_user(request)
    recipes, debug_ctx_raw = _list_recipes_from_presets()
    codebases, cb_error = _list_codebases()
    debug_ctx = debug_ctx_raw
    debug_ctx["workspaces_root"] = str(WORKSPACES_ROOT)
    debug_ctx["codebases_count"] = len(codebases)
    settings = None
    missing_creds = False
    try:
        with SessionLocal() as session:
            settings = get_system_settings(session)
        missing_creds = not (settings and settings.gitlab_token and settings.gitlab_username)
    except Exception:
        missing_creds = True
    if cb_error and not debug_ctx.get("last_error"):
        debug_ctx["last_error"] = cb_error
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
            recipes_count=debug_ctx.get("recipes_count", len(recipes)),
            codebases=codebases,
            codebases_count=len(codebases),
            workspaces_root=str(WORKSPACES_ROOT),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
            missing_creds=missing_creds,
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
            recipes_count=debug_ctx.get("recipes_count", len(recipes)),
            codebases=codebases,
            codebases_count=len(codebases),
            workspaces_root=str(WORKSPACES_ROOT),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
        )
    recipe_id_val = str(form.get("recipe_id") or "").strip()
    note = str(form.get("note") or "").strip()
    codebase_id = str(form.get("codebase_id") or "").strip()
    repo_url = str(form.get("repo_url") or "").strip()
    branch = str(form.get("branch") or "").strip()
    build_cmd = str(form.get("build_cmd") or "").strip()

    def _attr(obj, key: str):
        if obj is None:
            return None
        if hasattr(obj, key):
            try:
                return getattr(obj, key)
            except Exception:
                pass
        if isinstance(obj, dict):
            return obj.get(key)
        return None

    selected_recipe = None
    if recipe_id_val:
        try:
            r_id_int = int(recipe_id_val)
            selected_recipe = next((r for r in recipes if _attr(r, "id") == r_id_int), None)
        except ValueError:
            selected_recipe = None
    if not selected_recipe and recipe_id_val:
        selected_recipe = next((r for r in recipes if str(_attr(r, "id")) == recipe_id_val), None)
    print(f"DEBUG: Form Recipe ID: {recipe_id_val}, Found Object: {selected_recipe}")
    def _flag(name: str, default: bool) -> bool:
        raw = form.get(name)
        if raw is None:
            return default
        raw_str = str(raw).strip().lower()
        if raw_str in {"", "0", "false", "off", "no"}:
            return False
        return True

    do_clone = _flag("do_clone", True)
    do_edit = _flag("do_edit", False)
    do_init = _flag("do_init", True)
    do_build = _flag("do_build", True)

    patch_paths = form.getlist("patch_paths") if hasattr(form, "getlist") else []
    patch_actions = form.getlist("patch_actions") if hasattr(form, "getlist") else []
    patch_finds = form.getlist("patch_finds") if hasattr(form, "getlist") else []
    patch_contents = form.getlist("patch_contents") if hasattr(form, "getlist") else []

    if selected_recipe:
        recipe_repo = _attr(selected_recipe, "repo_url")
        recipe_branch = _attr(selected_recipe, "branch")
        recipe_build = _attr(selected_recipe, "build_cmd")
        repo_url = str(recipe_repo or "").strip()
        branch = str(recipe_branch or "").strip()
        build_cmd = str(recipe_build or "").strip()

    allowed_fields = {
        "recipe_id",
        "note",
        "codebase_id",
        "do_clone",
        "do_edit",
        "do_init",
        "do_build",
        "repo_url",
        "branch",
        "build_cmd",
        "patch_paths",
        "patch_actions",
        "patch_finds",
        "patch_contents",
    }
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
            recipes_count=debug_ctx.get("recipes_count", len(recipes)),
            codebases=codebases,
            codebases_count=len(codebases),
            workspaces_root=str(WORKSPACES_ROOT),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
            codebase_id=codebase_id,
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
            recipes_count=debug_ctx.get("recipes_count", len(recipes)),
            codebases=codebases,
            codebases_count=len(codebases),
            workspaces_root=str(WORKSPACES_ROOT),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
            codebase_id=codebase_id,
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
            recipes_count=debug_ctx.get("recipes_count", len(recipes)),
            codebases=codebases,
            codebases_count=len(codebases),
            workspaces_root=str(WORKSPACES_ROOT),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
            codebase_id=codebase_id,
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
            recipes_count=debug_ctx.get("recipes_count", len(recipes)),
            codebases=codebases,
            codebases_count=len(codebases),
            workspaces_root=str(WORKSPACES_ROOT),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
            codebase_id=codebase_id,
        )

    settings = None
    try:
        with SessionLocal() as session:
            settings = get_system_settings(session)
        usage = shutil.disk_usage(get_jobs_root())
        free_gb = usage.free / (1024 ** 3)
        required_gb = settings.disk_min_free_gb if settings else 0
        if settings and free_gb < required_gb:
            msg = f"Server disk is too full (Free: {free_gb:.1f} GB, Required: {required_gb} GB). Please prune old jobs."
            debug_ctx["last_error"] = debug_ctx.get("last_error") or msg
            return render_page(
                request,
                "new_job.html",
                current_page="new",
                status_code=400,
                error=msg,
                recipes=recipes,
                presets_root=str(PRESETS_ROOT),
                recipes_count=debug_ctx.get("recipes_count", len(recipes)),
                codebases=codebases,
                codebases_count=len(codebases),
                workspaces_root=str(WORKSPACES_ROOT),
                last_error=debug_ctx.get("last_error"),
                debug_context=debug_ctx,
                user=user,
                token_ok=None,
                recipe_id=recipe_id_val,
                note=note,
                codebase_id=codebase_id,
            )
    except Exception as exc:
        logger.warning("Disk safety check failed; proceeding without guard: %s", exc)

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
            recipes_count=debug_ctx.get("recipes_count", len(recipes)),
            codebases=codebases,
            codebases_count=len(codebases),
            workspaces_root=str(WORKSPACES_ROOT),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
            codebase_id=codebase_id,
        )
    have_primary = settings and settings.gitlab_username_primary and settings.gitlab_token_primary
    have_secondary = settings and settings.gitlab_username_secondary and settings.gitlab_token_secondary
    have_legacy = settings and settings.gitlab_username and settings.gitlab_token
    if not (have_primary or have_secondary or have_legacy):
        debug_ctx["last_error"] = debug_ctx.get("last_error") or "GitLab credentials for ami.com or ami.com.tw are required. Please set them in Settings."
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=400,
            error="GitLab credentials for ami.com or ami.com.tw are required. Please set them in Settings.",
            recipes=recipes,
            presets_root=str(PRESETS_ROOT),
            recipes_count=debug_ctx.get("recipes_count", len(recipes)),
            codebases=codebases,
            codebases_count=len(codebases),
            workspaces_root=str(WORKSPACES_ROOT),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
            codebase_id=codebase_id,
        )

    snapshot = {
        "recipe_id": recipe_id_val,
        "raw_recipe_yaml": recipe_yaml,
        "note": note,
        "created_by": user,
        "created_at": created_at,
        "status": jobs.STATUS_PENDING,
        "mode": "full",
        "codebase_id": codebase_id,
        "run_clone": do_clone,
        "run_edit": do_edit,
        "run_init": do_init,
        "run_build": do_build,
        "repo_url": repo_url,
        "branch": branch,
        "build_cmd": build_cmd,
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
        "mode": "full",
        "codebase_id": codebase_id,
        "run_clone": do_clone,
        "run_edit": do_edit,
        "run_init": do_init,
        "run_build": do_build,
        "repo_url": repo_url,
        "branch": branch,
        "build_cmd": build_cmd,
        "snapshot": snapshot,
    }
    patches = []
    if do_edit:
        for idx, path_val in enumerate(patch_paths):
            if not path_val:
                continue
            action_val = patch_actions[idx] if idx < len(patch_actions) else ""
            content_val = patch_contents[idx] if idx < len(patch_contents) else ""
            find_val = patch_finds[idx] if idx < len(patch_finds) else ""
            patches.append(
                {
                    "path": path_val,
                    "action": action_val,
                    "content": content_val,
                    "find": find_val,
                }
            )
    spec["file_patches"] = patches
    try:
        jobs.write_job_spec(job_id, spec)
        try:
            job_dir = jobs.job_dir(job_id)
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "raw_recipe.yaml").write_text(recipe_yaml if recipe_yaml.endswith("\n") else f"{recipe_yaml}\n", encoding="utf-8")
            (job_dir / "patches.json").write_text(json.dumps(patches), encoding="utf-8")
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
            recipes_count=debug_ctx.get("recipes_count", len(recipes)),
            codebases=codebases,
            codebases_count=len(codebases),
            workspaces_root=str(WORKSPACES_ROOT),
            last_error=debug_ctx.get("last_error"),
            debug_context=debug_ctx,
            user=user,
            token_ok=None,
            recipe_id=recipe_id_val,
            note=note,
            codebase_id=codebase_id,
        )
    background_tasks.add_task(jobs.start_job_runner, job_id)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.get("/jobs/{job_id}/log/stream")
async def job_log_stream(request: Request, job_id: int, offset: int = 0):
    redirect = _require_login(request)
    if redirect:
        return redirect
    path = jobs.log_file(job_id)
    if not path.exists():
        return JSONResponse({"content": "", "next_offset": 0, "eof": False})
    start = offset if offset >= 0 else 0
    chunk_size = 1024 * 1024
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fp:
            fp.seek(start)
            data = fp.read(chunk_size)
            next_offset = fp.tell()
            eof = next_offset >= os.path.getsize(path)
            return JSONResponse({"content": data, "next_offset": next_offset, "eof": eof})
    except Exception:
        return JSONResponse({"content": "", "next_offset": start, "eof": False})


@router.get("/jobs/{job_id}/log/download")
async def job_log_download(request: Request, job_id: int):
    redirect = _require_login(request)
    if redirect:
        return redirect
    path = jobs.log_file(job_id)
    if not path.exists():
        return render_page(
            request,
            "error.html",
            current_page="jobs",
            status_code=404,
            title="Log not found",
            message="Job log file not found",
            user=_current_user(request),
            token_ok=None,
        )
    return FileResponse(path, media_type="text/plain", filename=f"job_{job_id}_log.txt")


@router.get("/api/jobs/{job_id}/artifacts")
async def api_artifacts(request: Request, job_id: int):
    redirect = _require_login(request)
    if redirect:
        return redirect
    if not db.get_job(job_id):
        return JSONResponse({"detail": "Job not found"}, status_code=404)
    return list(jobs.list_artifacts(job_id).values())


@router.get("/api/jobs/{job_id}/artifacts/{name}")
async def api_artifact_download(request: Request, job_id: int, name: str):
    redirect = _require_login(request)
    if redirect:
        return redirect
    job = db.get_job(job_id)
    if not job:
        return render_page(
            request,
            "error.html",
            current_page="jobs",
            status_code=404,
            title="Job not found",
            message="Job not found",
            user=_current_user(request),
            token_ok=None,
        )
    if Path(name).name != name:
        return render_page(
            request,
            "error.html",
            current_page="jobs",
            status_code=400,
            title="Invalid name",
            message="Invalid artifact name",
            user=_current_user(request),
            token_ok=None,
        )
    path = jobs.job_dir(job_id) / "artifacts" / name
    if not path.exists() or not path.is_file():
        return render_page(
            request,
            "error.html",
            current_page="jobs",
            status_code=404,
            title="Artifact not found",
            message="Artifact not found",
            user=_current_user(request),
            token_ok=None,
        )
    return FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/jobs/{job_id}/log/stream")
async def stream_log(request: Request, job_id: int):
    redirect = _require_login(request)
    if redirect:
        return redirect
    if not db.get_job(job_id):
        return JSONResponse({"detail": "Job not found"}, status_code=404)

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


@router.get("/jobs/{job_id}/refresh")
async def refresh_job(request: Request, job_id: int):
    redirect = _require_login(request)
    if redirect:
        return redirect
    job = db.get_job(job_id)
    if not job:
        return JSONResponse({"detail": "Job not found"}, status_code=404)
    return JSONResponse(job)
