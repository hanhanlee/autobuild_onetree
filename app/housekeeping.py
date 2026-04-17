import asyncio
import logging
import shutil
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Optional

from .app_settings import app_settings
from .crud_settings import get_system_settings
from .database import SessionLocal
from .db import delete_job, get_connection
from .config import get_job_work_dir, get_job_dir
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
    """
    清理任務的工作目錄及 HOME 殘留
    保留 artifacts/ 和 logs/ 供使用者下載
    """
    job_path = get_job_dir(job_id)
    work_dir = get_job_work_dir(job_id)

    # 1. 清理 build workspace（最大宗）
    if work_dir.exists():
        try:
            shutil.rmtree(work_dir)
            logger.info("[housekeeping] 已清理任務 %s 的工作目錄", job_id)
        except Exception:
            logger.warning("[housekeeping] 清理任務 %s 工作目錄失敗", job_id, exc_info=True)

    # 2. 清理 runner 設 HOME=JOB_DIR 產生的殘留目錄
    home_cruft = [".cache", ".config", ".local", ".npm"]
    for name in home_cruft:
        cruft_path = job_path / name
        if cruft_path.exists():
            with suppress(Exception):
                shutil.rmtree(cruft_path)

    _mark_pruned(job_id)


def _delete_job(job_id: int) -> None:
    """
    徹底刪除任務及其所有資料
    """
    job_path = get_job_dir(job_id)
    with suppress(Exception):
        shutil.rmtree(job_path, ignore_errors=True)
    delete_job(job_id)
    logger.info("[housekeeping] 已刪除任務 %s", job_id)


def run_housekeeping_once() -> None:
    """
    執行一次後臺清理工作
    根據設定清理舊任務和刪除已清理任務
    """
    try:
        with SessionLocal() as session:
            settings = get_system_settings(session)
    except Exception:
        logger.warning("[housekeeping] 無法加載系統設定", exc_info=True)
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
        logger.warning("[housekeeping] 無法查詢任務列表", exc_info=True)
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


async def run_periodic_housekeeping(interval_seconds: int = None) -> None:
    """
    定期執行後臺清理工作
    """
    interval = interval_seconds or app_settings.housekeeping_interval_seconds
    logger.info("[housekeeping] 已啟動定期清理任務（間隔：%d秒）", interval)
    while True:
        try:
            run_housekeeping_once()
        except Exception:
            logger.warning("[housekeeping] 執行失敗", exc_info=True)
        await asyncio.sleep(interval)
