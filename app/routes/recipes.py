import re
from typing import List

from fastapi import APIRouter, Form, Request, Response
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
