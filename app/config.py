import os
from pathlib import Path


def get_secret_key() -> str:
    return os.getenv("AUTO_BUILD_SECRET_KEY", "change-me-please")


def get_db_path() -> Path:
    return Path(os.getenv("AUTO_BUILD_DB", "/srv/autobuild/data/jobs.db"))


def get_jobs_root() -> Path:
    return Path(os.getenv("AUTO_BUILD_JOBS_ROOT", "/srv/autobuild/jobs"))

