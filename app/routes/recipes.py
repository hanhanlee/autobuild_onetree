import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import RedirectResponse

from .. import auth
from ..config import get_presets_root
from ..web import render_page

router = APIRouter()
RID_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\.yaml$")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.yaml$")


def _current_user(request: Request):
    return request.session.get("user")


def _require_user(request: Request) -> str:
    user = _current_user(request)
    if not user:
        raise ValueError("redirect")
    return user


def _validate_identifier(value: str, field: str) -> str:
    value = (value or "").strip()
    if not value or not re.match(r"^[A-Za-z0-9._-]+$", value):
        raise ValueError(f"{field} is required and must match [A-Za-z0-9._-]+")
    return value


def _validate_relpath(value: str, field: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("/") or ".." in value:
        raise ValueError(f"{field} must be a relative path without '..'")
    return value


def _parse_clone_lines(lines: List[str]) -> List[str]:
    cleaned: List[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not line.startswith("git clone "):
            raise ValueError("clone_lines must start with 'git clone '")
        if ";" in line:
            parts = line.split(";", 1)
            clone_part = parts[0].strip()
            rest = parts[1].strip()
            tokens = clone_part.split()
            if len(tokens) < 4:
                raise ValueError("clone_lines with ';' must include destination")
            dest = tokens[-1]
            if rest != f"cd {dest}":
                raise ValueError("Only '; cd <DEST>' is allowed and must match clone destination")
        cleaned.append(line)
    if not cleaned:
        raise ValueError("At least one clone line is required")
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


def _validate_rid(raw: str, *, require_exists: bool = False) -> Tuple[str, str, str, Path]:
    rid = (raw or "").strip()
    if not RID_RE.match(rid):
        raise ValueError("rid must be <platform>/<name>.yaml using [A-Za-z0-9._-]")
    platform, filename = rid.split("/", 1)
    if platform.startswith(".") or filename.startswith("."):
        raise ValueError("rid must not use hidden platform or filename")
    presets_root = get_presets_root()
    platform_path = presets_root / platform
    if not platform_path.is_dir():
        raise ValueError(f"platform not found: {platform}")
    target_path = platform_path / filename
    if require_exists and not target_path.exists():
        raise ValueError(f"recipe not found: {rid}")
    return rid, platform, filename, target_path


def _list_recipes(presets_root: Path, platform_filter: str = "") -> Tuple[List[str], List[dict]]:
    platforms: List[str] = []
    entries: List[dict] = []
    if presets_root.exists() and presets_root.is_dir():
        for platform_path in sorted(presets_root.iterdir(), key=lambda p: p.name):
            if not platform_path.is_dir() or platform_path.name.startswith("."):
                continue
            platforms.append(platform_path.name)
        if platform_filter and platform_filter not in platforms:
            raise ValueError(f"Unknown platform: {platform_filter}")
        for platform in platforms:
            if platform_filter and platform != platform_filter:
                continue
            platform_dir = presets_root / platform
            files = []
            for path in sorted(platform_dir.glob("*.yaml"), key=lambda p: p.name):
                if path.name.startswith("."):
                    continue
                try:
                    stat = path.stat()
                    mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    size = stat.st_size
                except OSError:
                    mtime = "?"
                    size = None
                files.append({"name": path.name, "rid": f"{platform}/{path.name}", "mtime": mtime, "size": size})
            entries.append({"name": platform, "files": files})
    return platforms, entries


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    text = content if content.endswith("\n") else f"{content}\n"
    tmp = target.with_suffix(f"{target.suffix}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


@router.get("/recipes")
async def recipes_list(request: Request, platform: Optional[str] = None):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    presets_root = get_presets_root()
    platform_filter = (platform or request.query_params.get("platform") or "").strip()
    try:
        platform_names, platform_entries = _list_recipes(presets_root, platform_filter)
    except ValueError as exc:
        return render_page(
            request,
            "recipes.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=404,
            error=str(exc),
            presets_root=str(presets_root),
            platform_filter=platform_filter,
            platforms=[],
            platform_names=[],
        )
    except Exception as exc:
        return render_page(
            request,
            "recipes.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=500,
            error=f"Failed to list recipes: {exc}",
            presets_root=str(presets_root),
            platform_filter=platform_filter,
            platforms=[],
            platform_names=[],
        )
    return render_page(
        request,
        "recipes.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=200,
        error=None,
        presets_root=str(presets_root),
        platform_filter=platform_filter,
        platforms=platform_entries,
        platform_names=platform_names,
    )


@router.get("/recipes/edit")
async def recipe_edit(request: Request, rid: str):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    try:
        rid_value, platform, filename, target_path = _validate_rid(rid, require_exists=True)
    except ValueError as exc:
        return render_page(
            request,
            "recipe_edit.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error=str(exc),
            rid=rid,
            content="",
            platform="",
            filename="",
        )
    try:
        content = target_path.read_text(encoding="utf-8")
    except Exception as exc:
        return render_page(
            request,
            "recipe_edit.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=500,
            error=f"Failed to read recipe: {exc}",
            rid=rid_value,
            content="",
            platform=platform,
            filename=filename,
        )
    return render_page(
        request,
        "recipe_edit.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=200,
        error=None,
        rid=rid_value,
        content=content,
        platform=platform,
        filename=filename,
    )


@router.post("/recipes/save")
async def recipe_save(request: Request):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    try:
        form = await request.form()
    except Exception:
        return render_page(
            request,
            "recipe_edit.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error="Invalid form submission",
            rid="",
            content="",
            platform="",
            filename="",
        )
    rid_raw = str(form.get("rid") or "")
    content = str(form.get("content") or "")
    try:
        rid_value, platform, filename, target_path = _validate_rid(rid_raw, require_exists=True)
    except ValueError as exc:
        return render_page(
            request,
            "recipe_edit.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error=str(exc),
            rid=rid_raw,
            content=content,
            platform="",
            filename="",
        )
    try:
        import yaml  # type: ignore
    except Exception:
        yaml = None
    if yaml is not None:
        try:
            yaml.safe_load(content)
        except Exception as exc:
            return render_page(
                request,
                "recipe_edit.html",
                user=user,
                token_ok=None,
                current_page="projects",
                status_code=400,
                error=f"Invalid YAML: {exc}",
                rid=rid_value,
                content=content,
                platform=platform,
                filename=filename,
            )
    try:
        _atomic_write(target_path, content)
    except Exception as exc:
        return render_page(
            request,
            "recipe_edit.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=500,
            error=f"Failed to save recipe: {exc}",
            rid=rid_value,
            content=content,
            platform=platform,
            filename=filename,
        )
    return RedirectResponse(url=f"/recipes/edit?rid={rid_value}", status_code=303)


@router.post("/recipes/archive")
async def recipe_archive(request: Request):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    presets_root = get_presets_root()
    try:
        form = await request.form()
    except Exception:
        platform_names, platform_entries = [], []
        try:
            platform_names, platform_entries = _list_recipes(presets_root)
        except Exception:
            pass
        return render_page(
            request,
            "recipes.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error="Invalid form submission",
            presets_root=str(presets_root),
            platform_filter="",
            platforms=platform_entries,
            platform_names=platform_names,
        )
    rid_raw = str(form.get("rid") or "")
    try:
        rid_value, platform, filename, target_path = _validate_rid(rid_raw, require_exists=True)
    except ValueError as exc:
        platform_names, platform_entries = [], []
        try:
            platform_names, platform_entries = _list_recipes(presets_root)
        except Exception:
            pass
        return render_page(
            request,
            "recipes.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error=str(exc),
            presets_root=str(presets_root),
            platform_filter="",
            platforms=platform_entries,
            platform_names=platform_names,
        )
    try:
        archive_dir = target_path.parent / ".archived"
        archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        archived_path = archive_dir / f"{target_path.stem}.{timestamp}.yaml"
        shutil.move(str(target_path), str(archived_path))
    except Exception as exc:
        return render_page(
            request,
            "recipe_edit.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=500,
            error=f"Failed to archive recipe: {exc}",
            rid=rid_value,
            content="",
            platform=platform,
            filename=filename,
        )
    return RedirectResponse(url=f"/recipes?platform={platform}", status_code=303)


@router.post("/recipes/delete")
async def recipe_delete(request: Request):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    presets_root = get_presets_root()
    try:
        form = await request.form()
    except Exception:
        return RedirectResponse(url="/recipes?error=invalid_form", status_code=303)
    rid_raw = str(form.get("rid") or "")
    try:
        rid_value, platform, filename, target_path = _validate_rid(rid_raw, require_exists=True)
    except ValueError as exc:
        return RedirectResponse(url=f"/recipes?error={quote_plus(str(exc))}", status_code=303)
    try:
        target_path.unlink()
    except FileNotFoundError:
        return RedirectResponse(url="/recipes?error=not_found", status_code=303)
    except Exception as exc:
        return RedirectResponse(url=f"/recipes?error={quote_plus(f'Failed to delete recipe: {exc}')}", status_code=303)
    return RedirectResponse(url=f"/recipes?platform={platform}", status_code=303)


@router.post("/recipes/copy")
async def recipe_copy(request: Request):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    try:
        form = await request.form()
    except Exception:
        return render_page(
            request,
            "recipe_edit.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error="Invalid form submission",
            rid="",
            content="",
            platform="",
            filename="",
        )
    rid_raw = str(form.get("rid") or "")
    new_name_raw = str(form.get("new_name") or "")
    try:
        rid_value, platform, filename, source_path = _validate_rid(rid_raw, require_exists=True)
    except ValueError as exc:
        return render_page(
            request,
            "recipe_edit.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error=str(exc),
            rid=rid_raw,
            content="",
            platform="",
            filename="",
        )
    new_name = new_name_raw.strip()
    if not NAME_RE.match(new_name) or new_name.startswith("."):
        return render_page(
            request,
            "recipe_edit.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error="new_name must end with .yaml and use [A-Za-z0-9._-]",
            rid=rid_value,
            content="",
            platform=platform,
            filename=filename,
        )
    target_path = source_path.parent / new_name
    if target_path.exists():
        return render_page(
            request,
            "recipe_edit.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=409,
            error=f"Destination already exists: {platform}/{new_name}",
            rid=rid_value,
            content="",
            platform=platform,
            filename=filename,
        )
    try:
        content = source_path.read_text(encoding="utf-8")
        _atomic_write(target_path, content)
    except Exception as exc:
        return render_page(
            request,
            "recipe_edit.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=500,
            error=f"Failed to copy recipe: {exc}",
            rid=rid_value,
            content="",
            platform=platform,
            filename=filename,
        )
    new_rid = f"{platform}/{new_name}"
    return RedirectResponse(url=f"/recipes/edit?rid={new_rid}", status_code=303)


@router.get("/recipes/new")
async def recipes_new(request: Request):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not auth.username_auth(user):
        return render_page(request, "recipes_new.html", user=user, token_ok=None, current_page="projects", status_code=403, error="Forbidden")
    sample_yaml = "\n".join(
        [
            "schema_version: 1",
            'id: "platform/project"',
            'display_name: "Platform Project"',
            "clone_block:",
            '  lines:',
            '    - "git clone https://git.ami.com/core/ami-bmc/base-tech/openbmc onetree"',
            '    - "cd onetree"',
            '    - "git clone https://git.ami.com/core/ami-bmc/one-tree/core/meta-core meta-core"',
            "init_block:",
            '  lines:',
            '    - "./meta-ami/github-gitlab-url.sh"',
            '    - "TEMPLATECONF=meta-ami/meta-evb/meta-evb-aspeed/meta-evb-ast2600/conf/templates/default . openbmc-env"',
            '    - "# Edit conf/local.conf here while init_block runs (runner does not yet support file_appends)"',
            "build_block:",
            '  lines:',
            '    - "bitbake obmc-phosphor-image"',
            "# runner supports clone_block/init_block/build_block/workdir; artifacts/file_appends not yet supported",
        ]
    )
    return render_page(
        request,
        "recipes_new.html",
        user=user,
        token_ok=None,
        current_page="projects",
        status_code=200,
        recipe_yaml=sample_yaml,
    )


@router.post("/recipes/new")
async def recipes_new_post(
    request: Request,
    platform: str = Form(...),
    project: str = Form(...),
    recipe_yaml: str = Form(...),
):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not auth.username_auth(user):
        return render_page(request, "recipes_new.html", user=user, token_ok=None, current_page="projects", status_code=403, error="Forbidden")

    recipe_yaml_text = (recipe_yaml or "").strip()
    platform_val = (platform or "").strip()
    if not platform_val or not re.match(r"^[A-Za-z0-9._-]+$", platform_val):
        return render_page(
            request,
            "recipes_new.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error="platform is required and must match [A-Za-z0-9._-]+",
            platform=platform,
            project=project,
            recipe_yaml=recipe_yaml_text,
        )

    project_val = (project or "").strip()
    if not project_val or not re.match(r"^[A-Za-z0-9._-]+$", project_val):
        return render_page(
            request,
            "recipes_new.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error="project is required and must match [A-Za-z0-9._-]+",
            platform=platform,
            project=project,
            recipe_yaml=recipe_yaml_text,
        )

    if not recipe_yaml_text:
        return render_page(
            request,
            "recipes_new.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error="recipe_yaml is required",
            platform=platform,
            project=project,
            recipe_yaml=recipe_yaml_text,
        )

    try:
        import yaml  # type: ignore
    except Exception:  # pragma: no cover - runtime guard
        return render_page(
            request,
            "recipes_new.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=500,
            error="PyYAML missing",
            platform=platform,
            project=project,
            recipe_yaml=recipe_yaml_text,
        )

    try:
        parsed = yaml.safe_load(recipe_yaml_text) or {}
    except Exception:
        return render_page(
            request,
            "recipes_new.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error="Invalid YAML; please fix and retry",
            platform=platform,
            project=project,
            recipe_yaml=recipe_yaml_text,
        )

    if not isinstance(parsed, dict):
        return render_page(
            request,
            "recipes_new.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error="Recipe YAML must be a mapping",
            platform=platform,
            project=project,
            recipe_yaml=recipe_yaml_text,
        )

    def _render_validation_error(message: str) -> Response:
        return render_page(
            request,
            "recipes_new.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error=message,
            platform=platform,
            project=project,
            recipe_yaml=recipe_yaml_text,
        )

    schema_version = parsed.get("schema_version")
    if schema_version is not None:
        if not isinstance(schema_version, int) or schema_version != 1:
            return _render_validation_error("schema_version must be integer 1")

    for block_name in ("clone_block", "init_block", "build_block"):
        block = parsed.get(block_name)
        if block is None:
            continue
        if not isinstance(block, dict):
            return _render_validation_error(f"{block_name} must be a mapping")
        lines = block.get("lines")
        if lines is None or lines == []:
            continue
        if not isinstance(lines, list) or not all(isinstance(item, str) for item in lines):
            return _render_validation_error(f"{block_name}.lines must be a list of strings")

    # Filesystem path (<platform>/<project>.yaml) is the recipe ID source of truth; YAML id is optional.
    recipe_id = parsed.get("id")
    expected_id = f"{platform_val}/{project_val}"
    if recipe_id and recipe_id != expected_id:
        return render_page(
            request,
            "recipes_new.html",
            user=user,
            token_ok=None,
            current_page="projects",
            status_code=400,
            error=f"Recipe id must be {expected_id} to match path",
            platform=platform,
            project=project,
            recipe_yaml=recipe_yaml_text,
        )

    presets_root = get_presets_root()
    target_dir = presets_root / platform_val
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{project_val}.yaml"
    target_path.write_text(recipe_yaml_text + ("\n" if not recipe_yaml_text.endswith("\n") else ""), encoding="utf-8")

    return RedirectResponse(url="/projects", status_code=303)
