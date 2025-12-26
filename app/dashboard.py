import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import get_jobs_root, get_workspace_root
from .db import get_connection
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


def get_live_jobs(limit: int = 100) -> List[Dict[str, object]]:
    query = """
        SELECT id, owner, machine, target, status, started_at, created_at
          FROM jobs
         WHERE LOWER(status) IN ('running', 'pending')
         ORDER BY COALESCE(created_at, '') DESC, id DESC
         LIMIT ?
    """
    with get_connection() as conn:
        rows = conn.execute(query, (limit,)).fetchall()
    live: List[Dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["started_ago"] = _time_ago(item.get("started_at") or item.get("created_at"))
        live.append(item)
    return live


def _date_key(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return (value or "")[:10]


def get_jobs_today() -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT COUNT(*) FROM jobs
             WHERE date(substr(created_at,1,10)) = date('now')
            """
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def get_volume_last_7_days() -> List[Tuple[str, int]]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT date(substr(created_at,1,10)) AS day, COUNT(*) AS total
              FROM jobs
             WHERE date(substr(created_at,1,10)) >= date('now','-6 day')
             GROUP BY day
             ORDER BY day
            """
        )
        rows = {row["day"]: row["total"] for row in cur.fetchall()}
    today = datetime.utcnow().date()
    series: List[Tuple[str, int]] = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        key = day.isoformat()
        series.append((key, int(rows.get(key, 0))))
    return series


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

    targets = {
        "tctl": "CPU Tctl",
        "composite": "NVMe Composite",
        "vrm": "VRM",
        "soc": "SoC",
        "dimm": "DIMM",
    }
    results: List[Dict[str, object]] = []

    def add_item(label: str, value: float, unit: str) -> None:
        results.append({"label": label, "value": value, "unit": unit})

    for chip_data in data.values():
        if not isinstance(chip_data, dict):
            continue
        for sensor_name, sensor_vals in chip_data.items():
            if not isinstance(sensor_vals, dict):
                continue
            label = sensor_vals.get("temp1_label") or sensor_vals.get("temp2_label") or sensor_vals.get("fan1_label") or sensor_name
            label_lower = str(label or "").lower()
            # Temperatures
            for key, display in targets.items():
                if key in label_lower:
                    val = sensor_vals.get("temp1_input") or sensor_vals.get("temp2_input") or sensor_vals.get("temp_input")
                    if isinstance(val, (int, float)):
                        add_item(display, float(val), "°C")
            # Fan RPM
            if "fan" in label_lower or "rpm" in label_lower:
                val = sensor_vals.get("fan1_input") or sensor_vals.get("fan2_input") or sensor_vals.get("fan_input")
                if isinstance(val, (int, float)):
                    add_item(label or "Fan", float(val), "RPM")
            # NVMe composite sometimes has temp1_label == "Composite"
            if "composite" in label_lower:
                val = sensor_vals.get("temp1_input") or sensor_vals.get("temp_input")
                if isinstance(val, (int, float)):
                    add_item("NVMe Composite", float(val), "°C")

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
    volume = get_volume_last_7_days()
    disk_usage = get_disk_usage(str(get_workspace_root()))
    sensors = get_sensors_data()
    labels = [d for d, _ in volume]
    counts = [c for _, c in volume]
    return {
        "live_jobs": jobs_live,
        "jobs_today": jobs_today,
        "volume_labels": labels,
        "volume_counts": counts,
        "disk_usage": disk_usage,
        "sensors": sensors,
    }
