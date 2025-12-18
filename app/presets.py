import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, ValidationError, validator

from .config import get_presets_root, get_root

_logger = logging.getLogger(__name__)

POST_STEP_WHITELIST = {"github-gitlab-url"}


def _is_safe_rel_path(value: str) -> bool:
    return not value.startswith("/") and ".." not in Path(value).parts


def _is_safe_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", value))


def _is_safe_url(value: str) -> bool:
    return bool(
        re.match(
            r"^(https?://|git@|ssh://|git://)",
            value,
        )
    )


class PresetRepo(BaseModel):
    name: str
    url: str
    dest: str
    default_branch: Optional[str] = None

    @validator("name")
    def validate_name(cls, v: str) -> str:
        if not _is_safe_name(v):
            raise ValueError("invalid repo name")
        return v

    @validator("url")
    def validate_url(cls, v: str) -> str:
        if not _is_safe_url(v):
            raise ValueError("invalid repo url")
        return v

    @validator("dest")
    def validate_dest(cls, v: str) -> str:
        if not _is_safe_rel_path(v):
            raise ValueError("dest must be relative and must not contain '..'")
        return v


class Preset(BaseModel):
    name: str
    description: str
    repos: List[PresetRepo]
    templateconf: str
    default_bitbake_target: str
    default_machine: Optional[str] = None
    env_builddir: Optional[str] = None
    post_steps: Optional[List[str]] = None

    @validator("name")
    def validate_name(cls, v: str) -> str:
        if not _is_safe_name(v):
            raise ValueError("invalid preset name")
        return v

    @validator("templateconf")
    def validate_templateconf(cls, v: str) -> str:
        if not _is_safe_rel_path(v):
            raise ValueError("templateconf must be relative and must not contain '..'")
        return v

    @validator("default_bitbake_target")
    def validate_target(cls, v: str) -> str:
        if not v or not re.match(r"^[A-Za-z0-9_.-]+$", v):
            raise ValueError("invalid bitbake target")
        return v

    @validator("env_builddir")
    def validate_env_builddir(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if not _is_safe_rel_path(v):
            raise ValueError("env_builddir must be relative and must not contain '..'")
        return v

    @validator("post_steps", each_item=True)
    def validate_post_steps(cls, v: str) -> str:
        if v not in POST_STEP_WHITELIST:
            raise ValueError(f"post_step '{v}' not allowed")
        return v


class PresetFile(BaseModel):
    version: int
    presets: List[Preset]


def _load_yaml(path: Path) -> Optional[dict]:
    try:
        import yaml  # type: ignore
    except ImportError:
        _logger.error("PyYAML not installed; cannot load presets from %s", path)
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data
    except Exception as exc:
        _logger.error("Failed to read preset file %s: %s", path, exc)
        return None


def _parse_presets(data: dict, source: Path) -> Dict[str, Preset]:
    presets: Dict[str, Preset] = {}
    if data is None:
        return presets
    try:
        preset_file = PresetFile(**data)
    except ValidationError as exc:
        _logger.error("Preset file validation failed for %s: %s", source, exc)
        return presets
    if preset_file.version != 1:
        _logger.error("Unsupported preset version %s in %s", preset_file.version, source)
        return presets
    for preset in preset_file.presets:
        presets[preset.name] = preset
    return presets


def load_builtin_presets() -> Dict[str, Preset]:
    builtin_path = Path(__file__).resolve().parent.parent / "runner" / "presets" / "builtin.yml"
    if not builtin_path.exists():
        _logger.error("Built-in preset file missing at %s", builtin_path)
        return {}
    data = _load_yaml(builtin_path)
    return _parse_presets(data, builtin_path)


def _collect_user_preset_files(username: str) -> List[Path]:
    root = get_presets_root() / "user" / username
    if not root.exists():
        return []
    files = list(root.glob("*.yml"))
    merged_file = root / "presets.yml"
    if merged_file.exists() and merged_file not in files:
        files.append(merged_file)
    return files


def load_user_presets(username: str) -> Dict[str, Preset]:
    presets: Dict[str, Preset] = {}
    for path in _collect_user_preset_files(username):
        data = _load_yaml(path)
        user_presets = _parse_presets(data, path)
        presets.update(user_presets)
    return presets


def merge_presets(builtin: Dict[str, Preset], user_presets: Dict[str, Preset]) -> Dict[str, Preset]:
    merged = dict(builtin)
    merged.update(user_presets)
    return merged


def load_presets_for_user(username: str) -> Dict[str, Preset]:
    builtin = load_builtin_presets()
    user_defined = load_user_presets(username)
    return merge_presets(builtin, user_defined)


def summarize_presets(presets: Dict[str, Preset]) -> List[Dict[str, object]]:
    items = []
    for preset in sorted(presets.values(), key=lambda p: p.name.lower()):
        items.append(
            {
                "name": preset.name,
                "description": preset.description,
                "default_machine": preset.default_machine,
                "default_bitbake_target": preset.default_bitbake_target,
                "templateconf": preset.templateconf,
                "repos_count": len(preset.repos),
            }
        )
    return items


def _self_check() -> int:
    presets = load_builtin_presets()
    if not presets:
        print("No built-in presets found")
        return 1
    print(f"Loaded {len(presets)} built-in presets from {get_root()}/runner/presets/builtin.yml")
    for name in sorted(presets.keys()):
        print(f"- {name}")
    return 0


def validate_builtin_presets() -> int:
    presets = load_builtin_presets()
    if not presets:
        raise RuntimeError("No built-in presets loaded")
    return len(presets)


if __name__ == "__main__":  # Simple self-check without pytest
    raise SystemExit(_self_check())

# TODO(Stage 2-4): Wire presets into job creation persistence (job.json) and rewrite runner
#                  to consume resolved presets and user overrides; add CRUD for user presets.
