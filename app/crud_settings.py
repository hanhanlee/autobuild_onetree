from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import SystemSettings

SETTINGS_SINGLETON_ID = 1

def _ensure_settings_columns(db: Session) -> None:
    """Ensure new columns exist on the system_settings table for backward compatibility."""
    try:
        cols = {row[1] for row in db.execute(text("PRAGMA table_info(system_settings)"))}
        if "gitlab_username" not in cols:
            db.execute(text("ALTER TABLE system_settings ADD COLUMN gitlab_username TEXT"))
            db.commit()
    except Exception:
        # Best-effort; caller will fail if the column truly cannot be added.
        pass


def get_system_settings(db: Session) -> SystemSettings:
    """Fetch the singleton settings row, creating it with defaults if needed."""
    _ensure_settings_columns(db)
    settings = db.query(SystemSettings).filter(SystemSettings.id == SETTINGS_SINGLETON_ID).first()
    if settings:
        return settings
    settings = SystemSettings(id=SETTINGS_SINGLETON_ID)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def update_system_settings(
    db: Session,
    *,
    prune_days_age: int,
    delete_days_age: int,
    gitlab_token: Optional[str],
    gitlab_username: Optional[str],
    disk_min_free_gb: int,
    gitlab_host: Optional[str] = None,
) -> SystemSettings:
    _ensure_settings_columns(db)
    settings = get_system_settings(db)
    settings.prune_days_age = prune_days_age
    settings.delete_days_age = delete_days_age
    settings.disk_min_free_gb = disk_min_free_gb
    if gitlab_host is not None and gitlab_host.strip():
        settings.gitlab_host = gitlab_host.strip()
    settings.gitlab_token = (gitlab_token or "").strip() or None
    settings.gitlab_username = (gitlab_username or "").strip() or None
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings
