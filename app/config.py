import os
from pathlib import Path
from typing import List, Union


def _env_path(keys: List[str], default: Union[Path, str]) -> Path:
    """
    Return the first set environment variable from `keys` as a Path; otherwise return `default` as a Path.
    """
    for key in keys:
        val = os.getenv(key)
        if val:
            return Path(val)
    return Path(default)


def get_root() -> Path:
    """
    Application root (parent of the app package), unless overridden by AUTOBUILD_ROOT/AUTO_BUILD_ROOT.
    """
    app_root = Path(__file__).resolve().parent.parent
    return _env_path(["AUTOBUILD_ROOT", "AUTO_BUILD_ROOT"], app_root)


def get_workspace_root() -> Path:
    """
    Workspace root for jobs/artifacts/workspaces.
    """
    return _env_path(
        ["AUTOBUILD_WORKSPACE_ROOT", "AUTO_BUILD_WORKSPACE_ROOT"],
        get_root() / "workspace",
    )


def get_jobs_root() -> Path:
    """
    Root for jobs/artifacts/logs.
    """
    return _env_path(
        ["AUTOBUILD_JOBS_ROOT", "AUTO_BUILD_JOBS_ROOT"],
        get_workspace_root() / "jobs",
    )


def get_token_root() -> Path:
    """
    Root for Git tokens.
    """
    return _env_path(
        ["AUTOBUILD_TOKEN_ROOT", "AUTO_BUILD_TOKEN_ROOT"],
        get_workspace_root() / "secrets" / "gitlab",
    )


def get_presets_root() -> Path:
    """
    Root for recipe presets.
    """
    return _env_path(
        ["AUTOBUILD_PRESETS_ROOT"],
        get_workspace_root() / "presets",
    )


def get_db_path() -> Path:
    """
    SQLite DB path (kept for existing callers), relative to the app root by default.
    """
    return _env_path(
        ["AUTOBUILD_DB", "AUTO_BUILD_DB"],
        get_root() / "data" / "jobs.db",
    )


def get_secret_key() -> str:
    return os.getenv("AUTOBUILD_SECRET_KEY", os.getenv("AUTO_BUILD_SECRET_KEY", "change-me-please"))


def get_git_host() -> str:
    return os.getenv("AUTOBUILD_GIT_HOST", "gitlab.example.com")
