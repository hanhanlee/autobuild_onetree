import shutil
from pathlib import Path
from typing import Dict


def get_disk_usage(path: str) -> Dict[str, float]:
    """
    Return disk usage for the filesystem containing `path` in gigabytes.
    Values are rounded to one decimal place.
    """
    target = Path(path)
    usage = shutil.disk_usage(str(target))
    gb = 1024 ** 3
    total_gb = round(usage.total / gb, 1)
    used_gb = round(usage.used / gb, 1)
    free_gb = round(usage.free / gb, 1)
    return {"total_gb": total_gb, "used_gb": used_gb, "free_gb": free_gb}
