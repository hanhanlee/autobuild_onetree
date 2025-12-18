import asyncio
import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from . import auth
from .config import get_jobs_root
from .db import get_connection, update_job_status

logger = logging.getLogger(__name__)


STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(owner: str, repo_url: str, ref: str, machine: str, target: str, created_at: Optional[str] = None) -> int:
    created_at = created_at or now_iso()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs (owner, repo_url, ref, machine, target, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (owner, repo_url, ref, machine, target, STATUS_PENDING, created_at),
        )
        conn.commit()
        job_id = cur.lastrowid
    prepare_job_dirs(job_id)
    return job_id


def prepare_job_dirs(job_id: int) -> Path:
    root = get_jobs_root() / str(job_id)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    return root


def job_dir(job_id: int) -> Path:
    return get_jobs_root() / str(job_id)


def status_file(job_id: int) -> Path:
    return job_dir(job_id) / "status.json"


def exit_code_file(job_id: int) -> Path:
    return job_dir(job_id) / "exit_code"


def log_file(job_id: int) -> Path:
    return job_dir(job_id) / "logs" / "build.log"


def _schedule_poll_job(job_id: int) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(poll_job(job_id))
    except RuntimeError:
        threading.Thread(target=lambda: asyncio.run(poll_job(job_id)), daemon=True).start()


def start_job_runner(job_id: int, owner: str, repo_url: str, ref: str, machine: str, target: str) -> None:
    started_at = now_iso()
    update_job_status(job_id, STATUS_RUNNING, started_at=started_at)
    job_root = job_dir(job_id)
    prepare_job_dirs(job_id)
    log_path = log_file(job_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(log_path.parent, 0o2775)
    except PermissionError:
        pass
    token_root = os.environ.get("AUTOBUILD_TOKEN_ROOT") or os.environ.get("AUTO_BUILD_TOKEN_ROOT") or "/opt/autobuild/workspace/secrets/gitlab"
    token_path = Path(token_root) / f"{owner}.token"
    if token_path.exists():
        try:
            auth.normalize_token_perms(Path(token_root), token_path)
        except Exception:
            logger.warning("Failed to normalize token perms for %s", token_path)
    else:
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write(f"GitLab token missing for user {owner} at {token_path}\n")
        update_job_status(job_id, STATUS_FAILED, finished_at=now_iso(), exit_code=2)
        return
    readable = True
    try:
        result = subprocess.run(
            ["sudo", "-n", "-u", owner, "head", "-c", "1", str(token_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        readable = result.returncode == 0
    except Exception:
        readable = False
    if not readable:
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write(f"GitLab token not readable by user {owner} at {token_path} (check perms/group)\n")
        update_job_status(job_id, STATUS_FAILED, finished_at=now_iso(), exit_code=2)
        return
    log_fp = None
    cmd = [
        "sudo",
        "-u",
        owner,
        "env",
        f"JOB_DIR={job_root}",
        f"AUTOBUILD_JOBS_ROOT={get_jobs_root()}",
        f"AUTOBUILD_JOB_OWNER={owner}",
        f"AUTOBUILD_TOKEN_ROOT={token_root}",
        "/opt/autobuild/runner/run_job.sh",
        str(job_id),
        repo_url,
        ref,
        machine,
        target,
    ]
    env = os.environ.copy()
    env["JOB_DIR"] = str(job_root)
    env["AUTOBUILD_JOBS_ROOT"] = str(get_jobs_root())
    env["AUTO_BUILD_JOB_ID"] = str(job_id)
    env["AUTOBUILD_JOB_OWNER"] = owner
    logger.info("Starting runner for job %s (owner=%s) log=%s cmd=%s", job_id, owner, log_path, cmd)
    try:
        log_fp = log_path.open("ab", buffering=0)
        subprocess.Popen(cmd, env=env, stdout=log_fp, stderr=subprocess.STDOUT, cwd="/opt/autobuild")
    except Exception:
        logger.exception("Failed to start runner for job %s", job_id)
        update_job_status(job_id, STATUS_FAILED, finished_at=now_iso(), exit_code=-1)
        return
    finally:
        if log_fp is not None:
            try:
                log_fp.close()
            except Exception:
                pass
    _schedule_poll_job(job_id)


async def poll_job(job_id: int, interval: float = 2.0) -> None:
    while True:
        if status_file(job_id).exists():
            try:
                data = json.loads(status_file(job_id).read_text(encoding="utf-8"))
                status = data.get("status")
                exit_code = data.get("exit_code")
                finished_at = data.get("finished_at")
                if status in (STATUS_SUCCESS, STATUS_FAILED):
                    update_job_status(job_id, status, finished_at=finished_at, exit_code=exit_code)
                    return
            except Exception:
                logger.debug("Failed to read status for job %s", job_id, exc_info=True)
        elif exit_code_file(job_id).exists():
            try:
                exit_code = int(exit_code_file(job_id).read_text(encoding="utf-8").strip())
            except ValueError:
                exit_code = -1
            status = STATUS_SUCCESS if exit_code == 0 else STATUS_FAILED
            update_job_status(job_id, status, finished_at=now_iso(), exit_code=exit_code)
            return
        await asyncio.sleep(interval)


def list_artifacts(job_id: int) -> Dict[str, Dict[str, Optional[str]]]:
    artifacts_dir = job_dir(job_id) / "artifacts"
    artifacts: Dict[str, Dict[str, Optional[str]]] = {}
    if not artifacts_dir.exists():
        return artifacts
    for item in artifacts_dir.iterdir():
        if item.is_file():
            stat = item.stat()
            artifacts[item.name] = {
                "name": item.name,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
    return artifacts


def write_job_spec(job_id: int, spec: Dict[str, object]) -> None:
    path = job_dir(job_id) / "job.json"
    tmp = path.with_suffix(".json.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2, sort_keys=True)
    tmp.replace(path)
