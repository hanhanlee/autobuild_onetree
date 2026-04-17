import re
import sqlite3
from typing import Any, Dict, List, Optional

from .config import get_db_path

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DDL_RE = re.compile(r"^[A-Za-z0-9_ '()]+$")


def _validate_identifier(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name


def _enable_foreign_keys(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("PRAGMA foreign_keys = ON")
    except Exception:
        pass


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    table = _validate_identifier(table)
    cur = conn.execute(f"PRAGMA table_info({table})")
    for row in cur.fetchall():
        # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
        if len(row) >= 2 and row[1] == column:
            return True
    return False


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    table = _validate_identifier(table)
    column = _validate_identifier(column)
    if not _DDL_RE.match(ddl):
        raise ValueError(f"Invalid DDL fragment: {ddl!r}")
    if not _has_column(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _migrate_jobs_table(conn: sqlite3.Connection) -> None:
    _add_column(conn, "jobs", "recipe_id", "TEXT DEFAULT ''")
    _add_column(conn, "jobs", "raw_recipe_yaml", "TEXT DEFAULT ''")
    _add_column(conn, "jobs", "note", "TEXT DEFAULT ''")
    _add_column(conn, "jobs", "created_by", "TEXT DEFAULT ''")
    _add_column(conn, "jobs", "pinned", "INTEGER DEFAULT 0")
    _add_column(conn, "jobs", "cc_emails", "TEXT DEFAULT ''")
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


# ── Job CRUD functions below are DEPRECATED ──
# All runtime queries now go through crud_jobs.py (SQLAlchemy ORM).
# These remain only for backward compatibility with projects.py migration code.
# Do NOT add new callers.
