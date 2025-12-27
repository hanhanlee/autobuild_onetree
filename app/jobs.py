import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional, Set
from urllib.parse import urlparse

from .auth import load_user_tokens
from .config import get_git_host, get_jobs_root, get_token_root
from .db import get_connection, update_job_status, get_job
from .email import send_job_notification

logger = logging.getLogger(__name__)

_spec_locks_lock = threading.Lock()
_spec_locks: Dict[int, threading.Lock] = {}


def _job_lock(job_id: int) -> threading.Lock:
    with _spec_locks_lock:
        lock = _spec_locks.get(job_id)
        if lock is None:
            lock = threading.Lock()
            _spec_locks[job_id] = lock
        return lock


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
        "    useHttpPath = false\n"
    )
    config_path.write_text(config_body, encoding="utf-8")
    return {"XDG_CONFIG_HOME": str(job_dir / ".config")}


def create_job(created_by: str, recipe_id: str, raw_recipe_yaml: str, note: str, created_at: Optional[str] = None, cc_emails: str = "") -> int:
    created_at = created_at or now_iso()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs (owner, repo_url, ref, machine, target, status, created_at, recipe_id, raw_recipe_yaml, note, created_by, cc_emails)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (created_by, "", "", "", "", STATUS_PENDING, created_at, recipe_id, raw_recipe_yaml, note or "", created_by, cc_emails or ""),
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


def _schedule_poll_job(job_id: int, proc: Optional[subprocess.Popen] = None) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(poll_job(job_id, proc=proc))
    except RuntimeError:
        threading.Thread(target=lambda: asyncio.run(poll_job(job_id, proc=proc)), daemon=True).start()


def load_job_spec(job_id: int) -> Optional[Dict[str, object]]:
    path = job_dir(job_id) / "job.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _update_job_spec(job_id: int, mutate_fn: Callable[[Dict[str, object]], None]) -> bool:
    path = job_dir(job_id) / "job.json"
    lock = _job_lock(job_id)
    with lock:
        try:
            existing: Dict[str, object] = {}
            if path.exists():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8")) or {}
                    if isinstance(loaded, dict):
                        existing = loaded
                except Exception:
                    logger.warning("Failed to load job spec for mutation (job=%s)", job_id, exc_info=True)
            mutate_fn(existing)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
            return True
        except Exception:
            logger.warning("Failed to update job spec for job %s", job_id, exc_info=True)
            return False


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
    token_data = load_user_tokens(owner)
    primary_token = (token_data.get("primary") or "").strip() if isinstance(token_data, dict) else ""
    secondary_token = (token_data.get("secondary") or "").strip() if isinstance(token_data, dict) else ""
    primary_username = (token_data.get("username_primary") or "").strip() if isinstance(token_data, dict) else ""
    secondary_username = (token_data.get("username_secondary") or "").strip() if isinstance(token_data, dict) else ""
    if not primary_token and not secondary_token:
        token_path = Path(token_root) / f"{owner}.token"
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write(f"GitLab token missing for user {owner} at {token_path}\n")
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
    env["RUN_CLONE"] = "1" if run_clone else "0"
    env["RUN_EDIT"] = "1" if run_edit else "0"
    env["RUN_INIT"] = "1" if run_init else "0"
    env["RUN_BUILD"] = "1" if run_build else "0"
    try:
        settings = SimpleNamespace(
            gitlab_username_primary=owner,
            gitlab_token_primary=primary_token,
            gitlab_username_secondary=owner,
            gitlab_token_secondary=secondary_token,
            gitlab_username=owner,
            gitlab_token=primary_token or secondary_token,
        )
        settings.gitlab_username_primary = primary_username or owner
        settings.gitlab_username_secondary = secondary_username or owner
        settings.gitlab_username = settings.gitlab_username_primary
        env.update(
            setup_job_git_env(
                job_root,
                settings,
                fallback_token=primary_token or secondary_token,
                fallback_username=owner,
            )
        )
    except Exception:
        logger.warning("Failed to inject per-job git credentials", exc_info=True)
    logger.info("Starting runner for job %s (owner=%s) log=%s cmd=%s", job_id, owner, log_path, cmd)
    try:
        log_fp = log_path.open("ab", buffering=0)
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            cwd="/opt/autobuild",
            start_new_session=True,
        )
        def _mark_running(data: Dict[str, object]) -> None:
            data.update(
                {
                    "runner_pid": proc.pid,
                    "started_at": started_at,
                    "status": STATUS_RUNNING,
                }
            )
            snap = data.get("snapshot")
            if isinstance(snap, dict):
                snap["status"] = STATUS_RUNNING
                data["snapshot"] = snap

        _update_job_spec(job_id, _mark_running)
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
    _schedule_poll_job(job_id, proc=proc)


def _notify_job_status(job_id: int, status: str) -> None:
    try:
        job = get_job(job_id)
    except Exception:
        job = None
    if not job:
        return
    owner = job.get("owner") or job.get("created_by")
    profile = load_user_tokens(owner or "")
    try:
        send_job_notification(job, status, profile)
    except Exception:
        return


async def poll_job(job_id: int, proc: Optional[subprocess.Popen] = None, interval: float = 2.0) -> None:
    while True:
        if status_file(job_id).exists():
            try:
                data = json.loads(status_file(job_id).read_text(encoding="utf-8"))
                status = data.get("status")
                exit_code = data.get("exit_code")
                finished_at = data.get("finished_at")
                if status in (STATUS_SUCCESS, STATUS_FAILED):
                    if status == STATUS_SUCCESS:
                        collect_artifacts(job_id)
                    update_job_status(job_id, status, finished_at=finished_at, exit_code=exit_code)
                    _notify_job_status(job_id, status)
                    return
            except Exception:
                logger.debug("Failed to read status for job %s", job_id, exc_info=True)
        elif exit_code_file(job_id).exists():
            try:
                exit_code = int(exit_code_file(job_id).read_text(encoding="utf-8").strip())
            except ValueError:
                exit_code = -1
            status = STATUS_SUCCESS if exit_code == 0 else STATUS_FAILED
            if status == STATUS_SUCCESS:
                collect_artifacts(job_id)
            update_job_status(job_id, status, finished_at=now_iso(), exit_code=exit_code)
            _notify_job_status(job_id, status)
            return
        elif proc is not None and proc.poll() is not None:
            exit_code = proc.returncode
            logger.warning("Runner process for job %s exited early with code %s", job_id, exit_code)
            try:
                log_path = log_file(job_id)
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as fp:
                    fp.write(f"[job {job_id}] Runner exited unexpectedly with code {exit_code} at {now_iso()}\n")
            except Exception:
                logger.debug("Failed to append unexpected-exit note for job %s", job_id, exc_info=True)
            update_job_status(job_id, STATUS_FAILED, finished_at=now_iso(), exit_code=exit_code)
            _notify_job_status(job_id, STATUS_FAILED)
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


def _copy_unique(src: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / src.name
    if target.exists():
        stem, suffix = src.stem, src.suffix
        counter = 1
        while target.exists():
            target = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    shutil.copy2(src, target)


def collect_artifacts(job_id: int) -> None:
    """
    Collect build artifacts into the job's artifacts directory.
    Looks for *.static.mtd and *.static.mtd.tar under the job directory (recursively).
    """
    base_dir = job_dir(job_id)
    artifacts_dir = base_dir / "artifacts"
    if not base_dir.exists():
        return
    patterns = ["*.static.mtd", "*.static.mtd.tar"]
    found_any = False
    for pattern in patterns:
        for path in base_dir.rglob(pattern):
            try:
                if artifacts_dir in path.parents:
                    continue
                if path.is_file():
                    _copy_unique(path, artifacts_dir)
                    found_any = True
            except Exception:
                logger.debug("Failed to copy artifact %s for job %s", path, job_id, exc_info=True)
    if found_any:
        logger.info("Collected artifacts for job %s into %s", job_id, artifacts_dir)


def write_job_spec(job_id: int, spec: Dict[str, object]) -> None:
    path = job_dir(job_id) / "job.json"
    tmp = path.with_suffix(".json.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2, sort_keys=True)
    tmp.replace(path)


def _find_runner_pids(job_id: int) -> Set[int]:
    pids: Set[int] = set()
    search_term = f"/opt/autobuild/runner/run_job.sh {job_id}"
    try:
        out = subprocess.check_output(["pgrep", "-f", search_term], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            try:
                pids.add(int(line.strip()))
            except Exception:
                continue
    except Exception:
        pass
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,args"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if "/opt/autobuild/runner/run_job.sh" in line and str(job_id) in line.split():
                try:
                    pid_str = line.strip().split(None, 1)[0]
                    pids.add(int(pid_str))
                except Exception:
                    continue
    except Exception:
        pass
    return pids


def resolve_base_job_path(base_job_id: int) -> Path:
    base_dir = job_dir(base_job_id) / "work"
    if not base_dir.exists() or not base_dir.is_dir():
        raise ValueError(f"Job {base_job_id} work directory not found")
    subdirs = [p for p in base_dir.iterdir() if p.is_dir()]
    if len(subdirs) == 0:
        raise ValueError(f"No source directory found in Job {base_job_id} work folder.")
    if len(subdirs) > 1:
        raise ValueError(f"Ambiguous source: Multiple directories found in Job {base_job_id} work folder.")
    return subdirs[0]


def stop_job(job_id: int) -> bool:
    spec = load_job_spec(job_id) or {}
    pids: Set[int] = set()
    runner_pid = spec.get("runner_pid")
    try:
        if runner_pid is not None:
            pids.add(int(runner_pid))
    except Exception:
        pass
    pids.update(_find_runner_pids(job_id))
    if not pids:
        logger.warning("No runner PID found for job %s; nothing to stop", job_id)
        return False
    stopped = False
    def _kill_with_signal(pid: int, sig: int) -> None:
        if hasattr(os, "killpg"):
            try:
                os.killpg(pid, sig)
                return
            except Exception:
                pass
        os.kill(pid, sig)

    def _is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    for pid in pids:
        try:
            _kill_with_signal(pid, signal.SIGTERM)
            for _ in range(50):
                time.sleep(0.1)
                if not _is_alive(pid):
                    break
            else:
                logger.warning("Force killing job %s (pid=%s) after SIGTERM timeout", job_id, pid)
                _kill_with_signal(pid, signal.SIGKILL)
            stopped = True
        except ProcessLookupError:
            continue
        except Exception:
            logger.warning("Failed to terminate pid %s for job %s", pid, job_id, exc_info=True)
    if stopped:
        try:
            update_job_status(job_id, STATUS_FAILED, finished_at=now_iso(), exit_code=-9)
        except Exception:
            logger.warning("Failed to update status after stopping job %s", job_id, exc_info=True)
        def _mark_stopped(data: Dict[str, object]) -> None:
            data.pop("runner_pid", None)
            stopped_at = now_iso()
            data.update({"status": STATUS_FAILED, "stopped_at": stopped_at})
            snap = data.get("snapshot")
            if isinstance(snap, dict):
                snap["status"] = STATUS_FAILED
                snap["stopped_at"] = stopped_at
                data["snapshot"] = snap

        _update_job_spec(job_id, _mark_stopped)
        try:
            log_path = log_file(job_id)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as fp:
                fp.write(f"[job {job_id}] Stopped by user at {now_iso()}\n")
        except Exception:
            logger.warning("Failed to append stop notice to log for job %s", job_id, exc_info=True)
    return stopped


def retry_job(job_id: int, owner: Optional[str] = None) -> bool:
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT created_by, owner FROM jobs WHERE id = ?", (job_id,)).fetchone()
            db_owner = None
            if row:
                db_owner = row[0] or row[1]
            owner = owner or db_owner
            conn.execute(
                """
                UPDATE jobs
                   SET status = ?,
                       started_at = NULL,
                       finished_at = NULL,
                       exit_code = NULL
                 WHERE id = ?
                """,
                (STATUS_PENDING, job_id),
            )
            conn.commit()
    except Exception:
        logger.warning("Failed to reset job state for retry (job=%s)", job_id, exc_info=True)
        return False

    # Best-effort cleanup of prior workspace to reduce flakiness on retry.
    try:
        ws_dir = job_dir(job_id) / "workspace"
        if ws_dir.exists():
            shutil.rmtree(ws_dir)
    except Exception:
        logger.warning("Failed to clean workspace before retry for job %s", job_id, exc_info=True)

    def _mark_retry(data: Dict[str, object]) -> None:
        data.pop("runner_pid", None)
        retry_at = now_iso()
        data.update({"status": STATUS_PENDING, "last_retry_at": retry_at})
        snap = data.get("snapshot")
        if isinstance(snap, dict):
            snap["status"] = STATUS_PENDING
            snap["last_retry_at"] = retry_at
            data["snapshot"] = snap

    _update_job_spec(job_id, _mark_retry)
    try:
        log_path = log_file(job_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write(f"[job {job_id}] Retry requested at {now_iso()}\n")
    except Exception:
        logger.warning("Failed to append retry notice to log for job %s", job_id, exc_info=True)
    start_job_runner(job_id, owner=owner)
    return True
