import asyncio
import logging
import shutil
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Optional

from .crud_settings import get_system_settings
from .database import SessionLocal
from .db import delete_job, get_connection
from . import jobs

logger = logging.getLogger(__name__)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _job_is_pruned(job_id: int) -> bool:
    spec = jobs.load_job_spec(job_id) or {}
    if not isinstance(spec, dict):
        return False
    if spec.get("is_pruned"):
        return True
    snap = spec.get("snapshot")
    if isinstance(snap, dict) and snap.get("is_pruned"):
        return True
    return False


def _mark_pruned(job_id: int) -> None:
    def _flag(data: dict) -> None:
        data["is_pruned"] = True
        snap = data.get("snapshot")
        if isinstance(snap, dict):
            snap["is_pruned"] = True
            data["snapshot"] = snap

    jobs._update_job_spec(job_id, _flag)


def _prune_workspace(job_id: int) -> None:
    job_path = jobs.job_dir(job_id)
    workspace_dir = job_path / "workspace"
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir, ignore_errors=True)
    _mark_pruned(job_id)
    logger.info("[housekeeping] pruned workspace for job %s", job_id)


def _delete_job(job_id: int) -> None:
    job_path = jobs.job_dir(job_id)
    with suppress(Exception):
        shutil.rmtree(job_path, ignore_errors=True)
    delete_job(job_id)
    logger.info("[housekeeping] deleted job %s", job_id)


def run_housekeeping_once() -> None:
    try:
        with SessionLocal() as session:
            settings = get_system_settings(session)
    except Exception:
        logger.warning("[housekeeping] failed to load settings", exc_info=True)
        return
    prune_days = max(int(settings.prune_days_age or 0), 0)
    delete_days = max(int(settings.delete_days_age or 0), 0)
    now = datetime.now(timezone.utc)
    prune_cutoff = now - timedelta(days=prune_days) if prune_days > 0 else None
    delete_cutoff = now - timedelta(days=delete_days) if delete_days > 0 else None
    if not prune_cutoff and not delete_cutoff:
        return
    try:
        with get_connection() as conn:
            rows = conn.execute("SELECT id, created_at, pinned, status FROM jobs").fetchall()
    except Exception:
        logger.warning("[housekeeping] failed to query jobs", exc_info=True)
        return
    for row in rows:
        job_id = row["id"]
        created = _parse_iso(row["created_at"])
        if not created:
            continue
        status = (row["status"] or "").lower()
        if status in {"running", "pending"}:
            continue
        if delete_cutoff and created <= delete_cutoff and not row["pinned"]:
            _delete_job(job_id)
            continue
        if prune_cutoff and created <= prune_cutoff and not row["pinned"] and not _job_is_pruned(job_id):
            _prune_workspace(job_id)


async def run_periodic_housekeeping(interval_seconds: int = 3600) -> None:
    while True:
        try:
            run_housekeeping_once()
        except Exception:
            logger.warning("[housekeeping] run failed", exc_info=True)
        await asyncio.sleep(interval_seconds)
