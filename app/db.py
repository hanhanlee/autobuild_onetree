import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from .config import get_db_path


def ensure_db() -> None:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner TEXT NOT NULL,
                repo_url TEXT NOT NULL,
                ref TEXT NOT NULL,
                machine TEXT NOT NULL,
                target TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                exit_code INTEGER
            )
            """
        )
        conn.commit()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        return row_to_dict(row) if row else None


def list_jobs(limit: int = 100) -> Dict[int, Dict[str, Any]]:
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        return {row["id"]: row_to_dict(row) for row in rows}


def update_job_status(job_id: int, status: str, started_at: Optional[str] = None, finished_at: Optional[str] = None, exit_code: Optional[int] = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE jobs
               SET status = COALESCE(?, status),
                   started_at = COALESCE(?, started_at),
                   finished_at = COALESCE(?, finished_at),
                   exit_code = COALESCE(?, exit_code)
             WHERE id = ?
            """,
            (status, started_at, finished_at, exit_code, job_id),
        )
        conn.commit()

