"""
ORM-based CRUD for the jobs table.

Replaces raw sqlite3 queries from db.py with SQLAlchemy ORM operations.
All public functions accept a Session parameter for consistent transaction
management. Convenience wrappers without a session parameter auto-create
one via SessionLocal() for backward-compatible call sites.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from .models import Job


def _job_to_dict(job: Job) -> Dict[str, Any]:
    return {
        "id": job.id,
        "owner": job.owner,
        "repo_url": job.repo_url,
        "ref": job.ref,
        "machine": job.machine,
        "target": job.target,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "exit_code": job.exit_code,
        "recipe_id": job.recipe_id,
        "raw_recipe_yaml": job.raw_recipe_yaml,
        "note": job.note,
        "created_by": job.created_by,
        "pinned": job.pinned,
        "cc_emails": job.cc_emails,
    }


def get_job(db: Session, job_id: int) -> Optional[Dict[str, Any]]:
    job = db.query(Job).filter(Job.id == job_id).first()
    return _job_to_dict(job) if job else None


def list_jobs(db: Session, limit: int = 100) -> Dict[int, Dict[str, Any]]:
    jobs = db.query(Job).order_by(Job.created_at.desc()).limit(limit).all()
    return {j.id: _job_to_dict(j) for j in jobs}


def list_recent_jobs(
    db: Session,
    limit: int = 50,
    status: Optional[str] = None,
    days: Optional[int] = None,
) -> List[Dict[str, Any]]:
    q = db.query(
        Job.id, Job.owner, Job.created_by, Job.recipe_id, Job.note,
        Job.status, Job.created_at, Job.started_at, Job.finished_at,
        Job.exit_code, Job.pinned,
    )
    if status:
        q = q.filter(func.lower(Job.status) == status.lower())
    if days is not None and days > 0:
        q = q.filter(
            func.datetime(func.substr(Job.created_at, 1, 19))
            >= func.datetime("now", f"-{int(days)} days")
        )
    q = q.order_by(Job.pinned.desc(), Job.created_at.desc(), Job.id.desc()).limit(limit)
    rows = q.all()
    columns = ["id", "owner", "created_by", "recipe_id", "note",
               "status", "created_at", "started_at", "finished_at",
               "exit_code", "pinned"]
    return [dict(zip(columns, row)) for row in rows]


def set_job_pin(db: Session, job_id: int, pinned: bool) -> None:
    db.query(Job).filter(Job.id == job_id).update({"pinned": 1 if pinned else 0})
    db.commit()


def update_job_status(
    db: Session,
    job_id: int,
    status: str,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    exit_code: Optional[int] = None,
) -> None:
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return
    job.status = status
    if started_at is not None:
        job.started_at = started_at
    if finished_at is not None:
        job.finished_at = finished_at
    if exit_code is not None:
        job.exit_code = exit_code
    db.commit()


def delete_job(db: Session, job_id: int) -> None:
    db.query(Job).filter(Job.id == job_id).delete()
    db.commit()


def create_job(
    db: Session,
    created_by: str,
    recipe_id: str,
    raw_recipe_yaml: str,
    note: str,
    created_at: str,
    cc_emails: str = "",
) -> int:
    job = Job(
        owner=created_by,
        repo_url="",
        ref="",
        machine="",
        target="",
        status="pending",
        created_at=created_at,
        recipe_id=recipe_id,
        raw_recipe_yaml=raw_recipe_yaml,
        note=note or "",
        created_by=created_by,
        cc_emails=cc_emails or "",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job.id


def reset_job_for_retry(
    db: Session,
    job_id: int,
    created_at: str,
) -> bool:
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return False
    job.status = "pending"
    job.started_at = None
    job.finished_at = None
    job.exit_code = None
    job.created_at = created_at
    db.commit()
    return True


def get_job_owner(db: Session, job_id: int) -> Optional[str]:
    """Return created_by or owner for a job — used during runner startup."""
    row = db.query(Job.created_by, Job.owner).filter(Job.id == job_id).first()
    if not row:
        return None
    return row.created_by or row.owner


# ── Dashboard queries ──

def get_live_jobs(db: Session, limit: int = 100) -> List[Dict[str, Any]]:
    q = db.query(
        Job.id, Job.owner, Job.machine, Job.target,
        Job.status, Job.started_at, Job.created_at,
    ).filter(
        func.lower(Job.status).in_(["running", "pending"])
    ).order_by(
        Job.created_at.desc(), Job.id.desc()
    ).limit(limit)
    rows = q.all()
    columns = ["id", "owner", "machine", "target", "status", "started_at", "created_at"]
    return [dict(zip(columns, row)) for row in rows]


def get_jobs_today(db: Session) -> int:
    count = db.query(func.count(Job.id)).filter(
        func.date(func.substr(Job.created_at, 1, 10)) == func.date("now")
    ).scalar()
    return count or 0


def get_recent_jobs(db: Session, limit: int = 5) -> List[Dict[str, Any]]:
    q = db.query(
        Job.id, Job.recipe_id, Job.target, Job.status,
        Job.created_at, Job.started_at, Job.finished_at,
    ).order_by(
        func.coalesce(Job.finished_at, Job.started_at, Job.created_at, "").desc(),
        Job.id.desc(),
    ).limit(limit)
    rows = q.all()
    columns = ["id", "recipe_id", "target", "status", "created_at", "started_at", "finished_at"]
    return [dict(zip(columns, row)) for row in rows]


# ── Housekeeping queries ──

def list_all_jobs_for_housekeeping(db: Session) -> List[Dict[str, Any]]:
    rows = db.query(Job.id, Job.created_at, Job.pinned, Job.status).all()
    return [{"id": r.id, "created_at": r.created_at, "pinned": r.pinned, "status": r.status} for r in rows]


# ── Auto-session convenience wrappers ──
# These mirror the old db.py API (no session parameter) for easy migration.

from .database import SessionLocal  # noqa: E402


@contextmanager
def _auto_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def auto_get_job(job_id: int) -> Optional[Dict[str, Any]]:
    with _auto_session() as s:
        return get_job(s, job_id)


def auto_list_recent_jobs(limit: int = 50, status: Optional[str] = None, days: Optional[int] = None) -> List[Dict[str, Any]]:
    with _auto_session() as s:
        return list_recent_jobs(s, limit=limit, status=status, days=days)


def auto_set_job_pin(job_id: int, pinned: bool) -> None:
    with _auto_session() as s:
        set_job_pin(s, job_id, pinned)


def auto_update_job_status(job_id: int, status: str, started_at: Optional[str] = None, finished_at: Optional[str] = None, exit_code: Optional[int] = None) -> None:
    with _auto_session() as s:
        update_job_status(s, job_id, status, started_at=started_at, finished_at=finished_at, exit_code=exit_code)


def auto_delete_job(job_id: int) -> None:
    with _auto_session() as s:
        delete_job(s, job_id)


def auto_create_job(created_by: str, recipe_id: str, raw_recipe_yaml: str, note: str, created_at: str, cc_emails: str = "") -> int:
    with _auto_session() as s:
        return create_job(s, created_by, recipe_id, raw_recipe_yaml, note, created_at, cc_emails)


def auto_reset_job_for_retry(job_id: int, created_at: str) -> bool:
    with _auto_session() as s:
        return reset_job_for_retry(s, job_id, created_at)


def auto_get_job_owner(job_id: int) -> Optional[str]:
    with _auto_session() as s:
        return get_job_owner(s, job_id)
