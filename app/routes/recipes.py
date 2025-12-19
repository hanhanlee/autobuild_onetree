import re
from pathlib import Path
from typing import List

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import auth
from ..config import get_presets_root
from ..web import render_page

router = APIRouter()


def _current_user(request: Request):
    return request.session.get("user")


def _require_user(request: Request) -> str:
    user = _current_user(request)
    if not user:
        raise HTTPException(status_code=303, detail="redirect")
    return user


def _validate_identifier(value: str, field: str) -> str:
    value = (value or "").strip()
    if not value or not re.match(r"^[A-Za-z0-9._-]+$", value):
        raise HTTPException(status_code=400, detail=f"{field} is required and must match [A-Za-z0-9._-]+")
    return value


def _validate_relpath(value: str, field: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("/") or ".." in value:
        raise HTTPException(status_code=400, detail=f"{field} must be a relative path without '..'")
    return value


def _parse_clone_lines(lines: List[str]) -> List[str]:
    cleaned: List[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not line.startswith("git clone "):
            raise HTTPException(status_code=400, detail="clone_lines must start with 'git clone '")
        if ";" in line:
            parts = line.split(";", 1)
            clone_part = parts[0].strip()
            rest = parts[1].strip()
            tokens = clone_part.split()
            if len(tokens) < 4:
                raise HTTPException(status_code=400, detail="clone_lines with ';' must include destination")
            dest = tokens[-1]
            if rest != f"cd {dest}":
                raise HTTPException(status_code=400, detail="Only '; cd <DEST>' is allowed and must match clone destination")
        cleaned.append(line)
    if not cleaned:
        raise HTTPException(status_code=400, detail="At least one clone line is required")
    return cleaned


def _to_yaml_list(name: str, values: List[str], indent: int = 0) -> List[str]:
    prefix = " " * indent
    lines = [f"{prefix}{name}:"]
    for v in values:
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{prefix}  - "{escaped}"')
    return lines


def _build_recipe_yaml(data: dict) -> str:
    lines: List[str] = []
    lines.append(f"schema_version: {data['schema_version']}")
    lines.append(f'id: "{data["id"]}"')
    lines.append(f'platform: "{data["platform"]}"')
    lines.append(f'project: "{data["project"]}"')
    lines.append(f'display_name: "{data["display_name"]}"')
    lines.append("clone_block:")
    lines.extend(_to_yaml_list("lines", data["clone_block"]["lines"], indent=2))
    if data.get("workdir"):
        lines.append(f'workdir: "{data["workdir"]}"')
    lines.append("init_block:")
    lines.extend(_to_yaml_list("lines", data["init_block"]["lines"], indent=2))
    lines.append("file_appends:")
    for item in data["file_appends"]:
        path_escaped = item["path"].replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'  - path: "{path_escaped}"')
        lines.append("    append: |")
        for l in item["append"].splitlines():
            lines.append(f"      {l}")
    lines.append("build_block:")
    lines.extend(_to_yaml_list("lines", data["build_block"]["lines"], indent=2))
    artifacts = data.get("artifacts") or []
    lines.extend(_to_yaml_list("artifacts", artifacts))
    return "\n".join(lines) + "\n"


@router.get("/recipes/new")
async def recipes_new(request: Request):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not auth.username_auth(user):
        return render_page(request, "recipes_new.html", user=user, token_ok=None, current_page="projects", status_code=403, error="Forbidden")
    return render_page(request, "recipes_new.html", user=user, token_ok=None, current_page="projects", status_code=200)


@router.post("/recipes/new")
async def recipes_new_post(
    request: Request,
    platform: str = Form(...),
    project: str = Form(...),
    display_name: str = Form(""),
    clone_lines: str = Form(""),
    workdir: str = Form(""),
    init_lines: str = Form(""),
    append_path: str = Form("build/conf/local.conf"),
    append_block: str = Form(""),
    build_lines: str = Form(""),
):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not auth.username_auth(user):
        return render_page(request, "recipes_new.html", user=user, token_ok=None, current_page="projects", status_code=403, error="Forbidden")

    try:
        platform_val = _validate_identifier(platform, "platform")
        project_val = _validate_identifier(project, "project")
        display_name_val = (display_name or "").strip() or f"{platform_val}/{project_val}"
        workdir_val = _validate_relpath(workdir, "workdir")
        append_path_val = _validate_relpath(append_path, "append_path") or "build/conf/local.conf"
        clone_list = _parse_clone_lines(clone_lines.splitlines())
        init_list = [l.strip() for l in init_lines.splitlines() if l.strip()]
        build_list = [l.strip() for l in build_lines.splitlines() if l.strip()]
    except HTTPException as exc:
        status = exc.status_code if exc.status_code else 400
        detail = exc.detail if isinstance(exc.detail, str) else "Invalid input"
        return render_page(
            request,
            "recipes_new.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=status,
            error=detail,
            platform=platform,
            project=project,
            display_name=display_name,
            clone_lines=clone_lines,
            workdir=workdir,
            init_lines=init_lines,
            append_path=append_path,
            append_block=append_block,
            build_lines=build_lines,
        )

    data = {
        "schema_version": 1,
        "id": f"{platform_val}/{project_val}",
        "platform": platform_val,
        "project": project_val,
        "display_name": display_name_val,
        "clone_block": {"lines": clone_list},
        "workdir": workdir_val,
        "init_block": {"lines": init_list},
        "file_appends": [{"path": append_path_val, "append": append_block}],
        "build_block": {"lines": build_list},
        "artifacts": ["build/tmp/deploy/images/**"],
    }

    presets_root = get_presets_root()
    target_dir = presets_root / platform_val
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{project_val}.yaml"
    yaml_content = _build_recipe_yaml(data)
    target_path.write_text(yaml_content, encoding="utf-8")

    return RedirectResponse(url="/projects", status_code=303)
