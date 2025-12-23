import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse

from .. import db, jobs
from ..auth import username_auth
from ..config import get_jobs_root
from ..system import get_disk_usage
from ..web import render_page

router = APIRouter()
logger = logging.getLogger(__name__)
PRESETS_ROOT = Path("/opt/autobuild/workspace/presets")
WORKSPACES_ROOT = Path("/srv/autobuild/workspaces")


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


# 找到原本的 _safe_job_dir 函數，整段換掉
def _safe_job_dir(job_id: int) -> Optional[Path]:
    try:
        # 1. 取得設定檔定義的根目錄
        root = Path(jobs.JOBS_DIR).resolve()
        
        # 2. 組合目標路徑
        target = (Path(jobs.JOBS_DIR) / str(job_id)).resolve()
        
        # --- [DEBUG] 強制寫入 Log ---
        print(f"--- DEBUG PATH CHECK ---", flush=True)
        print(f"Config Root: {jobs.JOBS_DIR}", flush=True)
        print(f"Resolved Root: {root}", flush=True)
        print(f"Target Job: {target}", flush=True)
        # ---------------------------

        # 3. 檢查：目標路徑是否真的在根目錄底下？
        # 使用 str() 比對可以避免某些特殊的 Path 物件比對問題
        if str(root) not in str(target):
            print(f"[ERROR] Path Mismatch! Root '{root}' not in '{target}'", flush=True)
            return None
            
        return target
    except Exception as e:
        print(f"[ERROR] _safe_job_dir exception: {e}", flush=True)
        return None


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
    disk_usage = None
    try:
        disk_usage = get_disk_usage(str(get_jobs_root()))
    except Exception as exc:
        logger.warning("Failed to read disk usage: %s", exc)
    job_states: Dict[int, Dict[str, object]] = {}
    for job in recent:
        jid = job.get("id")
        if jid is None:
            continue
        state = _load_job_state(int(jid))
        job_states[int(jid)] = {
            "disk_usage": state.get("disk_usage"),
            "is_pruned": state.get("is_pruned"),
        }
    return render_page(
        request,
        "jobs.html",
        current_page="jobs",
        jobs=recent,
        disk_usage=disk_usage,
        job_states=job_states,
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


@router.post("/jobs/{job_id}/prune")
async def prune_job(request: Request, job_id: int):
    redirect = _require_login(request)
    if redirect:
        return redirect
    job = db.get_job(job_id)
    if not job:
        return RedirectResponse(url="/jobs?error=not_found", status_code=303)
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


@router.post("/jobs/{job_id}/delete")
async def delete_job(request: Request, job_id: int):
    redirect = _require_login(request)
    if redirect:
        return redirect
    job = db.get_job(job_id)
    if not job:
        return RedirectResponse(url="/jobs?error=not_found", status_code=303)
    job_dir = _safe_job_dir(job_id)
    if job_dir is None or not job_dir.exists():
        return RedirectResponse(url="/jobs?error=invalid_path", status_code=303)
    try:
        shutil.rmtree(job_dir)
    except Exception as exc:
        logger.warning("Failed to delete job dir %s: %s", job_dir, exc)
        return RedirectResponse(url=f"/jobs/{job_id}?error=delete_failed", status_code=303)
    logger.info("Deleted job directory for job_id=%s path=%s", job_id, job_dir)
    return RedirectResponse(url="/jobs?deleted=1", status_code=303)


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

    for job_id in job_ids:
        job = db.get_job(job_id)
        if not job:
            print(f"[ERROR] Batch skip {job_id}: Invalid path or security check failed.")
            continue
        job_dir = _safe_job_dir(job_id)
        if job_dir is None:
            continue
        if action == "prune":
            status_val = (job.get("status") or "").lower()
            if status_val not in {"success", "failed"}:
                continue
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
        else:
            if job_dir.exists():
                try:
                    shutil.rmtree(job_dir)
                    print(f"[INFO] Deleted {job_id}")
                except Exception:
                    print(f"[ERROR] Failed to delete {job_id}: {e}")
                    continue

    return RedirectResponse(url="/jobs?batch=1", status_code=303)


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
    mode_raw = str(form.get("mode") or "full").strip().lower() or "full"
    codebase_id = str(form.get("codebase_id") or "").strip()
    allowed_fields = {"recipe_id", "note", "mode", "codebase_id"}
    extras = [k for k in form.keys() if k not in allowed_fields]
    if extras:
        ignored_fields = sorted(extras)
        debug_ctx["ignored_fields"] = ignored_fields
    if mode_raw not in {"full", "clone_only", "build_only", "edit_only"}:
        debug_ctx["last_error"] = debug_ctx.get("last_error") or "mode must be one of full, clone_only, build_only, edit_only"
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=400,
            error="mode must be one of full, clone_only, build_only, edit_only",
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
            mode=mode_raw,
            codebase_id=codebase_id,
        )
    if mode_raw in {"build_only", "edit_only"} and not codebase_id:
        debug_ctx["last_error"] = debug_ctx.get("last_error") or "codebase_id is required for build_only/edit_only"
        return render_page(
            request,
            "new_job.html",
            current_page="new",
            status_code=400,
            error="codebase_id is required for build_only/edit_only",
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
            mode=mode_raw,
            codebase_id=codebase_id,
        )
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
            mode=mode_raw,
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
            mode=mode_raw,
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
            mode=mode_raw,
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
            mode=mode_raw,
            codebase_id=codebase_id,
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
            mode=mode_raw,
            codebase_id=codebase_id,
        )

    snapshot = {
        "recipe_id": recipe_id_val,
        "raw_recipe_yaml": recipe_yaml,
        "note": note,
        "created_by": user,
        "created_at": created_at,
        "status": jobs.STATUS_PENDING,
        "mode": mode_raw,
        "codebase_id": codebase_id,
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
        "mode": mode_raw,
        "codebase_id": codebase_id,
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
            mode=mode_raw,
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
