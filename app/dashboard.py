import json
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .app_settings import app_settings
from .config import get_workspace_root
from .crud_jobs import get_live_jobs as _orm_get_live_jobs, get_jobs_today as _orm_get_jobs_today, get_recent_jobs as _orm_get_recent_jobs
from .database import SessionLocal
from .system import get_disk_usage


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


def _time_ago(ts: Optional[str]) -> str:
    dt = _parse_iso(ts)
    if not dt:
        return "-"
    now = datetime.now(timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _format_ts_local(ts: Optional[str]) -> str:
    dt = _parse_iso(ts)
    if not dt:
        return "-"
    try:
        return dt.astimezone(app_settings.tz).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts) or "-"


def get_live_jobs(limit: int = 100) -> List[Dict[str, object]]:
    with SessionLocal() as session:
        rows = _orm_get_live_jobs(session, limit=limit)
    live: List[Dict[str, object]] = []
    for item in rows:
        item["started_ago"] = _time_ago(item.get("started_at") or item.get("created_at"))
        live.append(item)
    return live


def get_jobs_today() -> int:
    with SessionLocal() as session:
        return _orm_get_jobs_today(session)


def get_recent_jobs(limit: int = 5) -> List[Dict[str, object]]:
    with SessionLocal() as session:
        rows = _orm_get_recent_jobs(session, limit=limit)
    recent: List[Dict[str, object]] = []
    for item in rows:
        ts = item.get("finished_at") or item.get("started_at") or item.get("created_at")
        item["timestamp"] = _format_ts_local(ts)
        item["time_ago"] = _time_ago(ts)
        recent.append(item)
    return recent


def get_sensors_data() -> List[Dict[str, object]]:
    try:
        proc = subprocess.run(
            ["sensors", "-j"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode != 0:
            return []
        data = json.loads(proc.stdout)
    except Exception:
        return []

    results: List[Dict[str, object]] = []

    def add_item(label: str, value: float, unit: str) -> None:
        results.append({"label": label, "value": value, "unit": unit})

    for chip_data in data.values():
        if not isinstance(chip_data, dict):
            continue
        for sensor_name, sensor_vals in chip_data.items():
            if not isinstance(sensor_vals, dict):
                continue
            label = sensor_vals.get("temp1_label") or sensor_vals.get("temp2_label") or sensor_vals.get("fan1_label") or sensor_vals.get("fan2_label") or sensor_name
            for key, val in sensor_vals.items():
                if not key.endswith("_input"):
                    continue
                if not isinstance(val, (int, float)):
                    continue
                unit = ""
                if key.startswith("temp"):
                    unit = "°C"
                elif key.startswith("fan"):
                    unit = "RPM"
                add_item(label or key, float(val), unit)

    # Deduplicate by label keeping first occurrence
    seen = set()
    unique: List[Dict[str, object]] = []
    for item in results:
        if item["label"] in seen:
            continue
        seen.add(item["label"])
        unique.append(item)
    return unique


def get_dashboard_context() -> Dict[str, object]:
    jobs_live = get_live_jobs()
    jobs_today = get_jobs_today()
    recent_jobs = get_recent_jobs()
    disk_usage = get_disk_usage(str(get_workspace_root()))
    sensors = get_sensors_data()
    return {
        "live_jobs": jobs_live,
        "jobs_today": jobs_today,
        "recent_jobs": recent_jobs,
        "disk_usage": disk_usage,
        "sensors": sensors,
    }
