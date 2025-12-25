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
from urllib.parse import urlparse

from .auth import normalize_token_perms
from .config import get_git_host, get_jobs_root, get_token_root
from .crud_settings import get_system_settings
from .database import SessionLocal
from .db import get_connection, update_job_status

logger = logging.getLogger(__name__)


STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def setup_job_git_env(job_dir: Path, settings, fallback_token: Optional[str] = None, fallback_username: Optional[str] = None) -> Dict[str, str]:
    """Write per-job git credentials/config and return env overrides."""
    entries = []

    def add_entry(host_raw: Optional[str], username: Optional[str], token: Optional[str]) -> None:
        if not (host_raw and username and token):
            return
        host = str(host_raw).strip()
        parsed = urlparse(host)
        if parsed.scheme and parsed.netloc:
            host = parsed.netloc
        host = host.strip().rstrip("/")
        if not host:
            return
        entries.append(f"https://{username.strip()}:{token.strip()}@{host}")

    if settings:
        add_entry("https://git.ami.com", getattr(settings, "gitlab_username_primary", None), getattr(settings, "gitlab_token_primary", None))
        add_entry("https://git.ami.com.tw", getattr(settings, "gitlab_username_secondary", None), getattr(settings, "gitlab_token_secondary", None))
        add_entry(getattr(settings, "gitlab_host", None), getattr(settings, "gitlab_username", None), getattr(settings, "gitlab_token", None))

    # Fallback using service env/token if nothing else
    if not entries and fallback_token:
        add_entry(get_git_host(), fallback_username or "oauth2", fallback_token)

    if not entries:
        return {}

    creds_path = job_dir / ".git-credentials"
    creds_path.parent.mkdir(parents=True, exist_ok=True)
    creds_path.write_text("\n".join(entries) + "\n", encoding="utf-8")

    config_path = job_dir / ".config" / "git" / "config"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_body = (
        "[credential]\n"
        f"    helper = store --file={creds_path}\n"
        "    useHttpPath = true\n"
    )
    config_path.write_text(config_body, encoding="utf-8")
    return {"XDG_CONFIG_HOME": str(job_dir / ".config")}


def create_job(created_by: str, recipe_id: str, raw_recipe_yaml: str, note: str, created_at: Optional[str] = None) -> int:
    created_at = created_at or now_iso()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs (owner, repo_url, ref, machine, target, status, created_at, recipe_id, raw_recipe_yaml, note, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (created_by, "", "", "", "", STATUS_PENDING, created_at, recipe_id, raw_recipe_yaml, note or "", created_by),
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


def load_job_spec(job_id: int) -> Optional[Dict[str, object]]:
    path = job_dir(job_id) / "job.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def start_job_runner(job_id: int, owner: Optional[str] = None) -> None:
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
    spec = load_job_spec(job_id) or {}
    spec_owner = spec.get("created_by") if isinstance(spec, dict) else None
    if owner is None:
        owner = spec_owner
        if owner is None:
            try:
                with get_connection() as conn:
                    cur = conn.execute("SELECT created_by, owner FROM jobs WHERE id = ?", (job_id,))
                    row = cur.fetchone()
                    if row:
                        owner = row[0] or row[1]
            except Exception:
                owner = None
    owner = owner or os.environ.get("USER") or "autobuild"
    token_root = str(get_token_root())
    token_path = Path(token_root) / f"{owner}.token"
    token_value = None
    if token_path.exists():
        try:
            normalize_token_perms(Path(token_root), token_path, create_root=False)
            token_value = token_path.read_text(encoding="utf-8").strip()
        except Exception:
            logger.warning("Failed to normalize token perms for %s", token_path)
    else:
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write(f"GitLab token missing for user {owner} at {token_path}\n")
        try:
            os.chmod(log_path, 0o664)
        except PermissionError:
            pass
        update_job_status(job_id, STATUS_FAILED, finished_at=now_iso(), exit_code=2)
        return
    if not os.access(token_path, os.R_OK):
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write(f"GitLab token not readable by user {owner} at {token_path} (check perms/group)\n")
        try:
            os.chmod(log_path, 0o664)
        except PermissionError:
            pass
        update_job_status(job_id, STATUS_FAILED, finished_at=now_iso(), exit_code=2)
        return
    log_fp = None
    cmd = [
        "/opt/autobuild/runner/run_job.sh",
        str(job_id),
    ]
    env = os.environ.copy()
    env["JOB_DIR"] = str(job_root)
    env["AUTOBUILD_JOBS_ROOT"] = str(get_jobs_root())
    env["AUTO_BUILD_JOB_ID"] = str(job_id)
    env["AUTOBUILD_JOB_OWNER"] = owner
    env["AUTOBUILD_TOKEN_ROOT"] = str(token_root)
    try:
        run_clone = bool(spec.get("run_clone", True))
        run_edit = bool(spec.get("run_edit", False))
        run_init = bool(spec.get("run_init", True))
        run_build = bool(spec.get("run_build", True))
    except Exception:
        run_clone = True
        run_edit = False
        run_init = True
        run_build = True
    if not run_clone:
        env["SKIP_CLONE"] = "1"
    if run_edit:
        env["RUN_EDIT"] = "1"
    if not run_init:
        env["SKIP_INIT"] = "1"
    if not run_build:
        env["SKIP_BUILD"] = "1"
    try:
        with SessionLocal() as session:
            settings = get_system_settings(session)
        env.update(setup_job_git_env(job_root, settings, fallback_token=token_value, fallback_username=owner))
    except Exception:
        logger.warning("Failed to inject per-job git credentials", exc_info=True)
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
