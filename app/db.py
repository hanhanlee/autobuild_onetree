import sqlite3
from typing import Any, Dict, List, Optional

from .config import get_db_path


def _enable_foreign_keys(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("PRAGMA foreign_keys = ON")
    except Exception:
        pass


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    for row in cur.fetchall():
        # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
        if len(row) >= 2 and row[1] == column:
            return True
    return False


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if not _has_column(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _migrate_jobs_table(conn: sqlite3.Connection) -> None:
    _add_column(conn, "jobs", "recipe_id", "TEXT DEFAULT ''")
    _add_column(conn, "jobs", "raw_recipe_yaml", "TEXT DEFAULT ''")
    _add_column(conn, "jobs", "note", "TEXT DEFAULT ''")
    _add_column(conn, "jobs", "created_by", "TEXT DEFAULT ''")
    _add_column(conn, "jobs", "pinned", "INTEGER DEFAULT 0")
    conn.commit()
    # Backfill created_by from owner when possible for legacy rows.
    if _has_column(conn, "jobs", "created_by") and _has_column(conn, "jobs", "owner"):
        conn.execute(
            """
            UPDATE jobs
               SET created_by = COALESCE(NULLIF(created_by, ''), owner)
             WHERE created_by IS NULL OR created_by = ''
            """
        )
        conn.commit()


def ensure_db() -> None:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        _enable_foreign_keys(conn)
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
                exit_code INTEGER,
                recipe_id TEXT DEFAULT '',
                raw_recipe_yaml TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_by TEXT DEFAULT '',
                pinned INTEGER DEFAULT 0
            )
            """
        )
        _migrate_jobs_table(conn)
        conn.commit()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    _enable_foreign_keys(conn)
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


def list_recent_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT id,
                   owner,
                   created_by,
                   recipe_id,
                   note,
                   status,
                   created_at,
                   started_at,
                   finished_at,
                   exit_code,
                   pinned
              FROM jobs
             ORDER BY COALESCE(created_at, '') DESC, id DESC
             LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return [row_to_dict(row) for row in rows]


def set_job_pin(job_id: int, pinned: bool) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE jobs SET pinned = ? WHERE id = ?", (1 if pinned else 0, job_id))
        conn.commit()


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


def delete_job(job_id):
    # Fix: use get_connection() instead of get_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
