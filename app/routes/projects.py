import sqlite3
from typing import Any, Dict, Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import projects
from ..web import render_page

router = APIRouter()


def _current_user(request: Request) -> Optional[str]:
    return request.session.get("user")


def _ensure_user(request: Request) -> str:
    user = _current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


def _check_accessible(tmpl: Dict[str, Any], user: str) -> None:
    if projects.can_read_template(user, tmpl):
        return
    raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/api/projects")
async def api_list_projects(request: Request, visibility: str = "all", query: Optional[str] = None):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    items = projects.list_templates_for_user(user, visibility_filter=visibility, query=query)
    return items


@router.get("/api/projects/{template_id}")
async def api_get_project(request: Request, template_id: int):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    tmpl = projects.get_template(template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Not found")
    _check_accessible(tmpl, user)
    versions = projects.list_versions(template_id)
    tmpl["versions"] = versions
    return tmpl


@router.get("/api/projects/{template_id}/versions/{version}")
async def api_get_project_version(request: Request, template_id: int, version: int):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    tmpl = projects.get_template(template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Not found")
    _check_accessible(tmpl, user)
    ver = projects.get_version(template_id, version)
    if not ver:
        raise HTTPException(status_code=404, detail="Version not found")
    return ver


async def _get_body(request: Request) -> Dict[str, Any]:
    # helper to accept JSON or form
    if request.headers.get("content-type", "").startswith("application/json"):
        return await request.json()  # type: ignore
    return dict(await request.form())


@router.post("/api/projects")
async def api_create_project(request: Request):
    user = _ensure_user(request)
    data = await _get_body(request)
    name = data.get("name")
    visibility = data.get("visibility", "private")
    description = data.get("description")
    clone_script = data.get("clone_script") or ""
    build_script = data.get("build_script") or ""
    notes = data.get("notes")
    if not clone_script or not build_script:
        raise HTTPException(status_code=400, detail="clone_script and build_script are required")
    try:
        template_id = projects.create_template(user, name, visibility, description, clone_script, build_script, notes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create template")
    return {"id": template_id}


@router.post("/api/projects/{template_id}/versions")
async def api_create_version(request: Request, template_id: int):
    user = _ensure_user(request)
    data = await _get_body(request)
    clone_script = data.get("clone_script") or ""
    build_script = data.get("build_script") or ""
    notes = data.get("notes")
    if not clone_script or not build_script:
        raise HTTPException(status_code=400, detail="clone_script and build_script are required")
    try:
        version = projects.create_version(template_id, user, clone_script, build_script, notes)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create version")
    return {"version": version}


@router.patch("/api/projects/{template_id}")
async def api_update_project(request: Request, template_id: int):
    user = _ensure_user(request)
    data = await request.json()
    description = data.get("description")
    visibility = data.get("visibility")
    try:
        projects.update_template(template_id, user, description, visibility)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to update template")
    return {"ok": True}


@router.post("/api/projects/{template_id}/fork")
async def api_fork_project(request: Request, template_id: int):
    user = _ensure_user(request)
    data = await _get_body(request)
    name = data.get("name")
    visibility = data.get("visibility", "private")
    description = data.get("description")
    try:
        new_id, version = projects.fork_template(template_id, user, name, visibility, description)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fork template")
    return {"id": new_id, "version": version}


@router.get("/projects")
async def projects_page(request: Request, visibility: str = "all", query: Optional[str] = None, selected: Optional[int] = None):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    items = projects.list_templates_for_user(user, visibility_filter=visibility, query=query)
    selected_template = None
    versions = []
    if selected:
        selected_template = projects.get_template(selected)
        if selected_template and selected_template and (
            selected_template["visibility"] == "shared" or selected_template["created_by"] == user
        ):
            versions = projects.list_versions(selected)
        else:
            selected_template = None
            versions = []
    return render_page(
        request,
        "projects.html",
        current_page="projects",
        templates=items,
        selected_template=selected_template,
        versions=versions,
        visibility=visibility,
        query=query or "",
    )


@router.post("/projects/create")
async def create_project_form(
    request: Request,
    name: str = Form(...),
    visibility: str = Form("private"),
    description: str = Form(""),
    clone_script: str = Form(...),
    build_script: str = Form(...),
    notes: str = Form(""),
):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    try:
        template_id = projects.create_template(user, name, visibility, description, clone_script, build_script, notes or None)
    except sqlite3.IntegrityError as exc:
        msg = str(exc)
        err_text = "Template name already exists" if "project_templates.name" in msg else "Failed to create template"
        return render_page(
            request,
            "projects.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error=err_text,
            templates=projects.list_templates_for_user(user),
            selected_template=None,
            versions=[],
            visibility="all",
            query="",
            name=name,
            visibility_input=visibility,
            description=description,
            clone_script=clone_script,
            build_script=build_script,
            notes=notes,
        )
    except Exception:
        return render_page(
            request,
            "projects.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error="Failed to create template (check name uniqueness/inputs)",
            templates=projects.list_templates_for_user(user),
            selected_template=None,
            versions=[],
            visibility="all",
            query="",
            name=name,
            visibility_input=visibility,
            description=description,
            clone_script=clone_script,
            build_script=build_script,
            notes=notes,
        )
    return RedirectResponse(url=f"/projects?selected={template_id}", status_code=303)


@router.post("/projects/{template_id}/versions/create")
async def create_version_form(
    request: Request,
    template_id: int,
    clone_script: str = Form(...),
    build_script: str = Form(...),
    notes: str = Form(""),
):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    try:
        projects.create_version(template_id, user, clone_script, build_script, notes or None)
    except Exception:
        return RedirectResponse(url=f"/projects?selected={template_id}&error=version", status_code=303)
    return RedirectResponse(url=f"/projects?selected={template_id}", status_code=303)


@router.post("/projects/{template_id}/fork")
async def fork_project_form(
    request: Request,
    template_id: int,
    name: str = Form(...),
    visibility: str = Form("private"),
    description: str = Form(""),
):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    try:
        new_id, _ = projects.fork_template(template_id, user, name, visibility, description or None)
    except Exception:
        return RedirectResponse(url=f"/projects?selected={template_id}&error=fork", status_code=303)
    return RedirectResponse(url=f"/projects?selected={new_id}", status_code=303)
