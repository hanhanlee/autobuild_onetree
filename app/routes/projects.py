import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..recipes.generator import generate_recipe_yaml
from ..web import render_page

router = APIRouter()
logger = logging.getLogger(__name__)
PRESETS_ROOT = Path("/opt/autobuild/workspace/presets")


def _current_user(request: Request) -> Optional[str]:
    return request.session.get("user")


def sanitize_segment(raw: str) -> Tuple[Optional[str], Optional[str]]:
    value = raw if raw is not None else ""
    if value != value.strip():
        return None, "Value must not contain leading or trailing whitespace"
    value = value.strip()
    if not value:
        return None, "Value is required"
    if ".." in value or "/" in value or "\\" in value:
        return None, "Value must not contain path separators or '..'"
    if not re.match(r"^[A-Za-z0-9._-]+$", value):
        return None, "Only [A-Za-z0-9._-] characters are allowed"
    return value, None


def _split_lines(value: str):
    return (value or "").splitlines()


def _debug_context(presets_root: Path, last_error: Optional[str] = None, ignored_fields: Optional[object] = None) -> Dict[str, object]:
    debug: Dict[str, object] = {
        "presets_root": str(presets_root),
        "recipes_count": 0,
        "last_error": last_error,
    }
    if ignored_fields:
        debug["ignored_fields"] = ignored_fields
    try:
        if not presets_root.exists():
            debug["last_error"] = debug["last_error"] or f"presets_root missing: {presets_root}"
            return debug
        if not presets_root.is_dir():
            debug["last_error"] = debug["last_error"] or f"presets_root is not a directory: {presets_root}"
            return debug
        if not os.access(presets_root, os.R_OK):
            debug["last_error"] = debug["last_error"] or f"presets_root not readable: {presets_root}"
        if not os.access(presets_root, os.W_OK):
            debug["last_error"] = debug["last_error"] or f"presets_root not writable: {presets_root}"
        try:
            count = 0
            for path in presets_root.rglob("*"):
                if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}:
                    count += 1
            debug["recipes_count"] = count
        except Exception as exc:
            logger.warning("Failed to count recipes in %s: %s", presets_root, exc)
            debug["last_error"] = debug["last_error"] or f"Failed to enumerate recipes: {exc}"
    except Exception as exc:
        logger.warning("Failed to inspect presets_root %s: %s", presets_root, exc)
        debug["last_error"] = debug["last_error"] or f"Failed to inspect presets_root: {exc}"
    return debug


def _parse_recipe_id(recipe_id: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    recipe_id = (recipe_id or "").strip()
    if not recipe_id:
        return None, None, "recipe_id is required"
    parts = recipe_id.split("/")
    if len(parts) != 2:
        return None, None, "recipe_id must look like <platform>/<project>"
    platform, project = parts
    platform, err_p = sanitize_segment(platform)
    project, err_j = sanitize_segment(project)
    if err_p:
        return None, None, f"Invalid platform: {err_p}"
    if err_j:
        return None, None, f"Invalid project: {err_j}"
    return platform, project, None


def recipe_path_for(platform: str, project: str) -> Path:
    return PRESETS_ROOT / platform / f"{project}.yaml"


def write_recipe_file(target_path: Path, content: str) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    text = content if content.endswith("\n") else f"{content}\n"
    tmp_path = target_path.with_suffix(f"{target_path.suffix}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(target_path)


@router.get("/api/projects")
async def api_list_projects(request: Request, visibility: str = "all", query: Optional[str] = None):
    user = _current_user(request)
    debug_ctx = _debug_context(PRESETS_ROOT, last_error=None)
    return render_page(
        request,
        "projects.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=410,
        error="Deprecated endpoint: /api/projects (use /projects filesystem recipes)",
        presets_root=debug_ctx["presets_root"],
        recipes_count=debug_ctx["recipes_count"],
        last_error=debug_ctx["last_error"],
        debug_context=debug_ctx,
        templates=[],
        selected_template=None,
        versions=[],
        visibility="all",
        query="",
    )


@router.get("/api/projects/{template_id}")
async def api_get_project(request: Request, template_id: int):
    user = _current_user(request)
    message = "Legacy Project Template API is deprecated. Use /projects/new to create recipe templates."
    return render_page(
        request,
        "projects_new.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=410,
        error=message,
    )


@router.get("/api/projects/{template_id}/versions/{version}")
async def api_get_project_version(request: Request, template_id: int, version: int):
    user = _current_user(request)
    message = "Legacy Project Template API is deprecated. Use /projects/new to create recipe templates."
    return render_page(
        request,
        "projects_new.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=410,
        error=message,
    )


@router.post("/api/projects")
async def api_create_project(request: Request):
    user = _current_user(request)
    message = "Legacy Project Template API is deprecated. Use /projects/new to create recipe templates."
    return render_page(
        request,
        "projects_new.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=410,
        error=message,
    )


@router.post("/api/projects/{template_id}/versions")
async def api_create_version(request: Request, template_id: int):
    user = _current_user(request)
    message = "Legacy Project Template API is deprecated. Use /projects/new to create recipe templates."
    return render_page(
        request,
        "projects_new.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=410,
        error=message,
    )


@router.patch("/api/projects/{template_id}")
async def api_update_project(request: Request, template_id: int):
    user = _current_user(request)
    message = "Legacy Project Template API is deprecated. Use /projects/new to create recipe templates."
    return render_page(
        request,
        "projects_new.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=410,
        error=message,
    )


@router.post("/api/projects/{template_id}/fork")
async def api_fork_project(request: Request, template_id: int):
    user = _current_user(request)
    message = "Legacy Project Template API is deprecated. Use /projects/new to create recipe templates."
    return render_page(
        request,
        "projects_new.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=410,
        error=message,
    )


@router.get("/projects")
async def projects_page(
    request: Request,
    saved: Optional[str] = None,
    recipe_id: Optional[str] = None,
):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    saved_flag = saved or request.query_params.get("saved")
    recipe_id_raw = recipe_id or request.query_params.get("recipe_id") or ""
    success_msg = None
    error_msg = None
    platform = ""
    project = ""

    if recipe_id_raw:
        platform, project, rid_error = _parse_recipe_id(recipe_id_raw)
        if rid_error:
            error_msg = rid_error
            platform = ""
            project = ""
        else:
            recipe_id_raw = f"{platform}/{project}"

    if saved_flag and not error_msg:
        if recipe_id_raw:
            success_msg = f"Saved recipe to {recipe_id_raw}"
        else:
            error_msg = "Missing recipe_id for saved recipe"

    debug_ctx = _debug_context(PRESETS_ROOT, last_error=error_msg)
    visible_error = error_msg
    if not visible_error and debug_ctx["last_error"] and not success_msg:
        visible_error = debug_ctx["last_error"]
    status = 400 if error_msg else 200

    return render_page(
        request,
        "projects.html",
        current_page="projects",
        status_code=status,
        success=success_msg,
        error=visible_error,
        presets_root=debug_ctx["presets_root"],
        recipes_count=debug_ctx["recipes_count"],
        last_error=debug_ctx["last_error"],
        debug_context=debug_ctx,
        user=user,
        token_ok=None,
        platform=platform,
        project=project,
        display_name="",
        workdir="",
        clone_lines="",
        init_lines="",
        build_lines="",
        generated_yaml=None,
        templates=[],
        selected_template=None,
        versions=[],
        visibility="all",
        query="",
    )


@router.post("/projects")
async def save_project_recipe(request: Request):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    try:
        form = await request.form()
    except Exception:
        debug_ctx = _debug_context(PRESETS_ROOT, last_error="Invalid form submission")
        return render_page(
            request,
            "projects.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error="Invalid form submission",
            presets_root=debug_ctx["presets_root"],
            recipes_count=debug_ctx["recipes_count"],
            last_error=debug_ctx["last_error"],
            debug_context=debug_ctx,
            templates=[],
            selected_template=None,
            versions=[],
            visibility="all",
            query="",
        )

    allowed_fields = {"platform", "project", "display_name", "workdir", "clone_lines", "init_lines", "build_lines", "save"}
    extras = [k for k in form.keys() if k not in allowed_fields]
    ignored_fields = sorted(extras) if extras else []

    platform_raw = str(form.get("platform") or "")
    project_raw = str(form.get("project") or "")
    display_name = str(form.get("display_name") or "")
    workdir = str(form.get("workdir") or "")
    clone_lines = str(form.get("clone_lines") or "")
    init_lines = str(form.get("init_lines") or "")
    build_lines = str(form.get("build_lines") or "")
    save_requested_raw = str(form.get("save") or "").lower()
    save_requested = save_requested_raw in {"1", "true", "yes", "on", "save"}

    platform_val, platform_error = sanitize_segment(platform_raw)
    project_val, project_error = sanitize_segment(project_raw)

    validation_error = platform_error or project_error
    error_msg = validation_error
    if validation_error:
        if platform_error and not project_error:
            error_msg = f"Invalid platform: {platform_error}"
        elif project_error and not platform_error:
            error_msg = f"Invalid project: {project_error}"
        elif platform_error and project_error:
            error_msg = f"Invalid platform: {platform_error}; invalid project: {project_error}"

    template_model = {
        "display_name": display_name,
        "workdir": workdir,
        "clone_block": {"lines": _split_lines(clone_lines)},
        "init_block": {"lines": _split_lines(init_lines)},
        "build_block": {"lines": _split_lines(build_lines)},
    }
    generated_yaml = generate_recipe_yaml(template_model)

    if error_msg:
        debug_ctx = _debug_context(PRESETS_ROOT, last_error=error_msg, ignored_fields=ignored_fields or None)
        return render_page(
            request,
            "projects.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error=error_msg,
            presets_root=debug_ctx["presets_root"],
            recipes_count=debug_ctx["recipes_count"],
            last_error=debug_ctx["last_error"],
            debug_context=debug_ctx,
            platform=platform_raw,
            project=project_raw,
            display_name=display_name,
            workdir=workdir,
            clone_lines=clone_lines,
            init_lines=init_lines,
            build_lines=build_lines,
            generated_yaml=generated_yaml,
            templates=[],
            selected_template=None,
            versions=[],
            visibility="all",
            query="",
        )

    recipe_id_value = f"{platform_val}/{project_val}"
    if save_requested:
        target_path = recipe_path_for(platform_val, project_val)
        try:
            write_recipe_file(target_path, generated_yaml)
        except (IOError, OSError, PermissionError) as exc:
            err_text = f"Failed to save recipe: {exc}"
            debug_ctx = _debug_context(PRESETS_ROOT, last_error=err_text, ignored_fields=ignored_fields or None)
            return render_page(
                request,
                "projects.html",
                user=user,
                token_ok=None,
                current_page="projects",
                status_code=500,
                error=err_text,
                presets_root=debug_ctx["presets_root"],
                recipes_count=debug_ctx["recipes_count"],
                last_error=debug_ctx["last_error"],
                debug_context=debug_ctx,
                platform=platform_raw,
                project=project_raw,
                display_name=display_name,
                workdir=workdir,
                clone_lines=clone_lines,
                init_lines=init_lines,
                build_lines=build_lines,
                generated_yaml=generated_yaml,
                templates=[],
                selected_template=None,
                versions=[],
                visibility="all",
                query="",
            )
        return RedirectResponse(url=f"/projects?saved=1&recipe_id={recipe_id_value}", status_code=303)

    debug_ctx = _debug_context(PRESETS_ROOT, last_error=None, ignored_fields=ignored_fields or None)
    return render_page(
        request,
        "projects.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=200,
        presets_root=debug_ctx["presets_root"],
        recipes_count=debug_ctx["recipes_count"],
        last_error=debug_ctx["last_error"],
        debug_context=debug_ctx,
        platform=platform_raw,
        project=project_raw,
        display_name=display_name,
        workdir=workdir,
        clone_lines=clone_lines,
        init_lines=init_lines,
        build_lines=build_lines,
        generated_yaml=generated_yaml,
        templates=[],
        selected_template=None,
        versions=[],
        visibility="all",
        query="",
    )


@router.post("/projects/create")
async def create_project_form(
    request: Request,
):
    user = _current_user(request)
    return render_page(
        request,
        "projects.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=410,
        error="Deprecated endpoint: /projects/create (use /projects for filesystem recipes)",
    )


@router.post("/projects/{template_id}/versions/create")
async def create_version_form(
    request: Request,
):
    user = _current_user(request)
    return render_page(
        request,
        "projects.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=410,
        error="Deprecated endpoint: /projects/{id}/versions/create (use /projects for filesystem recipes)",
    )


@router.post("/projects/{template_id}/fork")
async def fork_project_form(
    request: Request,
):
    user = _current_user(request)
    return render_page(
        request,
        "projects.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=410,
        error="Deprecated endpoint: /projects/{id}/fork (use /projects for filesystem recipes)",
    )


@router.get("/projects/new")
async def new_project_template(request: Request):
    user = _current_user(request)
    return render_page(
        request,
        "projects.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=410,
        error="Deprecated endpoint: /projects/new (use /projects for filesystem recipes)",
    )


@router.post("/projects/new")
async def new_project_template_post(
    request: Request,
):
    user = _current_user(request)
    return render_page(
        request,
        "projects.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=410,
        error="Deprecated endpoint: POST /projects/new (use /projects for filesystem recipes)",
    )
