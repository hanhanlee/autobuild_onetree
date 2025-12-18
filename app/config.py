import logging
import os
from pathlib import Path
from typing import List, Optional

_logger = logging.getLogger(__name__)


def _env_path(key: str, default: str, legacy_keys: Optional[List[str]] = None) -> Path:
    legacy_keys = legacy_keys or []
    value = os.getenv(key)
    for legacy in legacy_keys:
        if value:
            break
        value = os.getenv(legacy)
    return Path(value) if value else Path(default)


def get_root() -> Path:
    preferred_root = _env_path("AUTOBUILD_ROOT", "/opt/autobuild", ["AUTO_BUILD_ROOT"])
    if preferred_root.exists():
        return preferred_root
    fallback = Path("/srv/autobuild")
    if fallback.exists():
        _logger.warning("AUTOBUILD_ROOT not found at %s, falling back to %s", preferred_root, fallback)
        return fallback
    return preferred_root


def get_workspace_root() -> Path:
    return _env_path(
        "AUTOBUILD_WORKSPACE_ROOT",
        str(get_root() / "workspace"),
        ["AUTO_BUILD_WORKSPACE_ROOT"],
    )


def get_jobs_root() -> Path:
    return _env_path(
        "AUTOBUILD_JOBS_ROOT",
        str(get_workspace_root() / "jobs"),
        ["AUTO_BUILD_JOBS_ROOT"],
    )


def get_presets_root() -> Path:
    return _env_path(
        "AUTOBUILD_PRESETS_ROOT",
        str(get_workspace_root() / "presets"),
        ["AUTO_BUILD_PRESETS_ROOT"],
    )


def get_db_path() -> Path:
    return _env_path("AUTOBUILD_DB", str(get_root() / "data" / "jobs.db"), ["AUTO_BUILD_DB"])


def get_secret_key() -> str:
    return os.getenv("AUTOBUILD_SECRET_KEY", os.getenv("AUTO_BUILD_SECRET_KEY", "change-me-please"))
