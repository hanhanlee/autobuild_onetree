import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .db import get_connection, row_to_dict
from .jobs import now_iso


def migrate_projects(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_templates (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            visibility TEXT NOT NULL CHECK(visibility IN ('shared','private')),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_template_versions (
            id INTEGER PRIMARY KEY,
            template_id INTEGER NOT NULL REFERENCES project_templates(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            clone_script TEXT NOT NULL,
            build_script TEXT NOT NULL,
            notes TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(template_id, version)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS project_templates_visibility ON project_templates(visibility)")
    conn.execute("CREATE INDEX IF NOT EXISTS project_template_versions_template_id ON project_template_versions(template_id)")
    conn.commit()


def _normalize_visibility(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in ("shared", "private"):
        raise ValueError("visibility must be 'shared' or 'private'")
    return value


def can_read_template(username: str, template: Dict[str, Any]) -> bool:
    if not username:
        return False
    if template.get("visibility") == "shared":
        return True
    return template.get("created_by") == username


def _can_view(template: Dict[str, Any], user: str) -> bool:
    return can_read_template(user, template)


def _require_owner(template: Dict[str, Any], user: str) -> None:
    if template["created_by"] != user:
        raise PermissionError("Forbidden")


def create_template(
    owner: str,
    name: str,
    visibility: str,
    description: Optional[str],
    clone_script: str,
    build_script: str,
    notes: Optional[str] = None,
) -> int:
    visibility = _normalize_visibility(visibility)
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    now = now_iso()
    with get_connection() as conn:
        migrate_projects(conn)
        cur = conn.execute(
            """
            INSERT INTO project_templates (name, description, visibility, created_by, created_at, updated_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, description, visibility, owner, now, owner, now),
        )
        template_id = cur.lastrowid
        conn.execute(
            """
            INSERT INTO project_template_versions (template_id, version, clone_script, build_script, notes, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (template_id, 1, clone_script, build_script, notes, owner, now),
        )
        conn.commit()
        return template_id


def list_templates_for_user(user: str, visibility_filter: str = "all", query: Optional[str] = None) -> List[Dict[str, Any]]:
    visibility_filter = (visibility_filter or "all").lower()
    clauses = []
    params: List[Any] = []
    if visibility_filter == "shared":
        clauses.append("t.visibility = 'shared'")
    elif visibility_filter == "private":
        clauses.append("t.visibility = 'private' AND t.created_by = ?")
        params.append(user)
    else:
        clauses.append("(t.visibility = 'shared' OR (t.visibility = 'private' AND t.created_by = ?))")
        params.append(user)

    if query:
        clauses.append("(LOWER(t.name) LIKE ? OR LOWER(t.description) LIKE ?)")
        q = f"%{query.lower()}%"
        params.extend([q, q])

    where = " AND ".join(clauses) if clauses else "1=1"
    sql = f"""
        SELECT
            t.id, t.name, t.description, t.visibility,
            t.created_by, t.created_at, t.updated_by, t.updated_at,
            (SELECT MAX(version) FROM project_template_versions v WHERE v.template_id = t.id) AS latest_version
        FROM project_templates t
        WHERE {where}
        ORDER BY t.updated_at DESC
    """
    with get_connection() as conn:
        migrate_projects(conn)
        cur = conn.execute(sql, tuple(params))
        rows = cur.fetchall()
        return [row_to_dict(r) for r in rows]


def get_template(template_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        migrate_projects(conn)
        cur = conn.execute(
            """
            SELECT
                t.id, t.name, t.description, t.visibility,
                t.created_by, t.created_at, t.updated_by, t.updated_at,
                (SELECT MAX(version) FROM project_template_versions v WHERE v.template_id = t.id) AS latest_version
            FROM project_templates t WHERE t.id = ?
            """,
            (template_id,),
        )
        row = cur.fetchone()
        return row_to_dict(row) if row else None


def list_versions(template_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        migrate_projects(conn)
        cur = conn.execute(
            """
            SELECT id, template_id, version, notes, created_by, created_at
            FROM project_template_versions
            WHERE template_id = ?
            ORDER BY version DESC
            """,
            (template_id,),
        )
        rows = cur.fetchall()
        return [row_to_dict(r) for r in rows]


def get_version(template_id: int, version: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        migrate_projects(conn)
        cur = conn.execute(
            """
            SELECT id, template_id, version, clone_script, build_script, notes, created_by, created_at
            FROM project_template_versions
            WHERE template_id = ? AND version = ?
            """,
            (template_id, version),
        )
        row = cur.fetchone()
        return row_to_dict(row) if row else None


def create_version(template_id: int, owner: str, clone_script: str, build_script: str, notes: Optional[str] = None) -> int:
    with get_connection() as conn:
        migrate_projects(conn)
        cur = conn.execute("SELECT * FROM project_templates WHERE id = ?", (template_id,))
        tmpl = cur.fetchone()
        if not tmpl:
            raise ValueError("Template not found")
        template = row_to_dict(tmpl)
        _require_owner(template, owner)
        cur = conn.execute("SELECT MAX(version) FROM project_template_versions WHERE template_id = ?", (template_id,))
        row = cur.fetchone()
        next_ver = (row[0] or 0) + 1
        now = now_iso()
        conn.execute(
            """
            INSERT INTO project_template_versions (template_id, version, clone_script, build_script, notes, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (template_id, next_ver, clone_script, build_script, notes, owner, now),
        )
        conn.execute(
            "UPDATE project_templates SET updated_at = ?, updated_by = ? WHERE id = ?",
            (now, owner, template_id),
        )
        conn.commit()
        return next_ver


def update_template(template_id: int, owner: str, description: Optional[str], visibility: Optional[str]) -> None:
    with get_connection() as conn:
        migrate_projects(conn)
        cur = conn.execute("SELECT * FROM project_templates WHERE id = ?", (template_id,))
        tmpl = cur.fetchone()
        if not tmpl:
            raise ValueError("Template not found")
        template = row_to_dict(tmpl)
        _require_owner(template, owner)
        new_visibility = template["visibility"]
        if visibility is not None:
            new_visibility = _normalize_visibility(visibility)
        now = now_iso()
        conn.execute(
            """
            UPDATE project_templates
               SET description = COALESCE(?, description),
                   visibility = ?,
                   updated_by = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (description, new_visibility, owner, now, template_id),
        )
        conn.commit()


def fork_template(template_id: int, user: str, name: str, visibility: str, description: Optional[str] = None) -> Tuple[int, int]:
    visibility = _normalize_visibility(visibility or "private")
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    with get_connection() as conn:
        migrate_projects(conn)
        cur = conn.execute("SELECT * FROM project_templates WHERE id = ?", (template_id,))
        tmpl = cur.fetchone()
        if not tmpl:
            raise ValueError("Template not found")
        template = row_to_dict(tmpl)
        if not _can_view(template, user):
            raise PermissionError("Forbidden")
        cur = conn.execute(
            """
            SELECT version, clone_script, build_script
            FROM project_template_versions
            WHERE template_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (template_id,),
        )
        src_ver = cur.fetchone()
        if not src_ver:
            raise ValueError("Template has no versions")
        src_version = src_ver["version"]
        clone_script = src_ver["clone_script"]
        build_script = src_ver["build_script"]
        now = now_iso()
        cur = conn.execute(
            """
            INSERT INTO project_templates (name, description, visibility, created_by, created_at, updated_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, description, visibility, user, now, user, now),
        )
        new_id = cur.lastrowid
        conn.execute(
            """
            INSERT INTO project_template_versions (template_id, version, clone_script, build_script, notes, created_by, created_at)
            VALUES (?, 1, ?, ?, ?, ?, ?)
            """,
            (
                new_id,
                clone_script,
                build_script,
                f"fork from {template['name']} v{src_version}",
                user,
                now,
            ),
        )
        conn.commit()
        return new_id, 1


def ensure_migrations() -> None:
    with get_connection() as conn:
        migrate_projects(conn)
