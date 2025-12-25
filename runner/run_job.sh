#!/usr/bin/env bash
# [DEBUG] Keeping pipefail disabled to avoid premature exit while collecting diagnostics; enable if you need stricter handling
set -eu
# set -euo pipefail

# 1. Set umask to 002 to keep group-writable files/dirs

# ================= [DEBUG DIAGNOSIS START] =================
# This section only prints diagnostics and does not affect job flow
echo "================= [DEBUG INFO] ================="
echo "Timestamp: $(date)"

# 1. Check current user identity
echo "[1] Current User Identity:"
id
echo "Effective User: $(whoami)"

# 2. Check job parameters
echo "[2] Job Parameters:"
echo "JOB_ID (Arg 1): ${1:-<missing>}"
echo "AUTOBUILD_JOBS_ROOT: ${AUTOBUILD_JOBS_ROOT:-<unset>}"

# 3. Compute job paths (with sensible defaults)
_JOB_ID="${1:-}"
_JOBS_ROOT="${AUTOBUILD_JOBS_ROOT:-${AUTO_BUILD_JOBS_ROOT:-/opt/autobuild/workspace/jobs}}"
_JOB_DIR="${_JOBS_ROOT}/${_JOB_ID}"
_WORK_DIR="${_JOB_DIR}/work"

# 4. Inspect job directory if present
if [ -d "${_JOB_DIR}" ]; then
    echo "[3] Permissions of JOB_DIR (${_JOB_DIR}):"
    ls -ld "${_JOB_DIR}"
else
    echo "[3] JOB_DIR (${_JOB_DIR}) does not exist yet."
    echo "Parent (JOBS_ROOT) permissions:"
    ls -ld "${_JOBS_ROOT}"
fi

# 5. Touch test to verify write permissions
if [ -d "${_JOB_DIR}" ]; then
    echo "[4] Try to write to JOB_DIR:"
    if touch "${_JOB_DIR}/debug_write_test" 2>/dev/null; then
        echo "SUCCESS: Write permission confirmed."
        rm "${_JOB_DIR}/debug_write_test"
    else
        echo "FAILURE: Cannot write to ${_JOB_DIR}!"
    fi
fi

# 6. Check /work mount info (ignoring systemd ReadOnlyPaths)
echo "[5] Mount info for /work:"
grep "/work" /proc/self/mounts || echo "/work not found in mounts"

echo "================= [DEBUG INFO END] ================="
# ================= [DEBUG DIAGNOSIS END] =================


# --- Base script; only mkdir failures should hard-error ---

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <job_id>" >&2
  exit 1
fi

JOB_ID="$1"
JOBS_ROOT="${AUTOBUILD_JOBS_ROOT:-${AUTO_BUILD_JOBS_ROOT:-/opt/autobuild/workspace/jobs}}"
: "${JOB_DIR:=${JOBS_ROOT}/${JOB_ID}}"
LOG_DIR="${JOB_DIR}/logs"
LOG_FILE="${LOG_DIR}/build.log"
ARTIFACT_DIR="${JOB_DIR}/artifacts"
STATUS_FILE="${JOB_DIR}/status.json"
EXIT_CODE_FILE="${JOB_DIR}/exit_code"
WORK_DIR="${JOB_DIR}/work"
WORKSPACES_ROOT="${AUTOBUILD_WORKSPACE_ROOT:-${AUTO_BUILD_WORKSPACE_ROOT:-/opt/autobuild/workspace}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCHES_FILE="${JOB_DIR}/patches.json"

# Git credential environment: force HOME and XDG so the per-job credentials are picked up
export HOME="${JOB_DIR}"
export XDG_CONFIG_HOME="${JOB_DIR}/.config"
mkdir -p "${HOME}" "${XDG_CONFIG_HOME}"
git config --global credential.helper "store --file=${JOB_DIR}/.git-credentials"
git config --global credential.useHttpPath false

# [DEBUG] If mkdir fails, print more detail before exiting
if ! mkdir -p "${LOG_DIR}" "${ARTIFACT_DIR}" "${WORK_DIR}"; then
    echo "CRITICAL ERROR: Failed to create directories!"
    echo "Target: ${LOG_DIR}, ${ARTIFACT_DIR}, ${WORK_DIR}"
    echo "Check permissions of ${JOB_DIR}"
    ls -ld "${JOB_DIR}"
    exit 1
fi

touch "${LOG_FILE}"

write_status() {
  local status="$1"
  local exit_code="$2"
  local finished="$3"
  local finished_json="null"
  if [[ -n "${finished}" ]]; then
    finished_json="\"${finished}\""
  fi
  cat > "${STATUS_FILE}" <<EOF
{"status":"${status}","exit_code":${exit_code},"finished_at":${finished_json}}
EOF
}

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

calc_disk_usage() {
  local target_dir="${BASE_DIR:-${JOB_DIR}}"
  if command -v du >/dev/null 2>&1; then
    echo "Calculating disk usage..."
    du -sh "${target_dir}" | awk '{print $1}' > "${target_dir}/disk_usage.txt"
    echo "Disk usage recorded: $(cat "${target_dir}/disk_usage.txt")"
    du -sh "${target_dir}" | awk '{print $1}'
  fi
}

update_job_json() {
  local status="$1"
  local exit_code="$2"
  local finished_at="$3"
  local job_json="${JOB_DIR}/job.json"
  if [[ ! -f "${job_json}" ]]; then
    echo "[job.json] skipping update (missing ${job_json})"
    return 0
  fi
  local disk_usage="Unavailable"
  local du_val
  du_val="$(calc_disk_usage || true)"
  if [[ -n "${du_val}" ]]; then
    disk_usage="${du_val}"
  fi
  python3 - "${job_json}" "${status}" "${exit_code}" "${finished_at}" "${disk_usage}" <<'PY'
import json
import sys
from pathlib import Path

job_json, status, exit_code, finished_at, disk_usage = sys.argv[1:]
path = Path(job_json)
try:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        data = {}
except Exception:
    data = {}

def parse_exit(val: str):
    try:
        return int(val)
    except Exception:
        return None

data["status"] = status
data["exit_code"] = parse_exit(exit_code)
data["finished_at"] = finished_at
data["disk_usage"] = disk_usage
data["is_pruned"] = False
snap = data.get("snapshot")
if isinstance(snap, dict):
    snap.update(
        {
            "status": data["status"],
            "exit_code": data["exit_code"],
            "finished_at": finished_at,
            "disk_usage": disk_usage,
            "is_pruned": False,
        }
    )
    data["snapshot"] = snap
tmp = path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
tmp.replace(path)
PY
  echo "[job.json] updated with status=${status} exit_code=${exit_code} disk_usage=${disk_usage}"
}

cleanup() {
  local code=$?
  if [[ ${LOCK_ACQUIRED:-0} -eq 1 ]]; then
    echo "[lock] releasing codebase lock"
  fi
  echo "Job ${JOB_ID} completed with code ${code}"
  echo "${code}" > "${EXIT_CODE_FILE}"
  local finished_at
  finished_at=$(timestamp)
  local status_val="failed"
  if [[ ${code} -eq 0 ]]; then
    status_val="success"
  fi
  write_status "${status_val}" "${code}" "${finished_at}"
  update_job_json "${status_val}" "${code}" "${finished_at}" || true
}
trap cleanup EXIT

exec > >(tee -a "${LOG_FILE}") 2>&1

echo "Starting job ${JOB_ID} at $(timestamp)"

write_status "running" "null" ""

SPEC_PATH="${JOB_DIR}/job_spec.json"
if [[ ! -f "${SPEC_PATH}" ]]; then
  SPEC_PATH="${JOB_DIR}/job.json"
fi
if [[ ! -f "${SPEC_PATH}" ]]; then
  echo "CONFIG ERROR: job spec missing at ${JOB_DIR}/job_spec.json" >&2
  exit 2
fi

RAW_RECIPE="${JOB_DIR}/raw_recipe.yaml"
if [[ ! -s "${RAW_RECIPE}" ]]; then
  echo "CONFIG ERROR: raw_recipe.yaml missing or empty at ${RAW_RECIPE}" >&2
  exit 2
fi

META_SH="${WORK_DIR}/meta.sh"
CLONE_CMDS="${WORK_DIR}/clone_commands.txt"
INIT_CMDS="${WORK_DIR}/init_commands.txt"
MODIFY_CMDS="${WORK_DIR}/modify_commands.txt"
BUILD_CMDS="${WORK_DIR}/build_commands.txt"
FILE_EDITS_JSON="${WORK_DIR}/file_edits.json"

# Ensure work dir exists
mkdir -p "${WORK_DIR}"

# Generate command files from raw_recipe.yaml (clone/init/build), meta stub, and file_edits.json
python3 - "${RAW_RECIPE}" "${WORK_DIR}" "${SPEC_PATH}" <<'PY'
import sys
import os
import json
import yaml

raw_path, work_dir, spec_path = sys.argv[1:4]

def fail(msg: str, exc: Exception | None = None) -> "NoReturn":
    if exc:
        print(f"[generate] {msg}: {exc}", file=sys.stderr)
    else:
        print(f"[generate] {msg}", file=sys.stderr)
    sys.exit(1)

def clean_lines(raw):
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            out.append(text)
    return out

def write_file(filename, content):
    path = os.path.join(work_dir, filename)
    os.makedirs(work_dir, exist_ok=True)
    if isinstance(content, list):
        if content:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(content) + "\n")
            print(f"[generate] wrote {filename} ({len(content)} lines)")
        else:
            open(path, "w", encoding="utf-8").close()
            print(f"[generate] created empty {filename} (no lines)")
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2)
        print(f"[generate] wrote {filename} (json)")

try:
    with open(raw_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
except Exception as exc:
    fail("Failed to load recipe YAML", exc)

for block, fname in (
    ("clone_block", "clone_commands.txt"),
    ("init_block", "init_commands.txt"),
    ("build_block", "build_commands.txt"),
):
    blk = data.get(block) if isinstance(data, dict) else None
    lines = clean_lines(blk.get("lines") if isinstance(blk, dict) else None)
    write_file(fname, lines)

# Optional meta.sh: change directory to workdir if provided
meta_lines = []
workdir = data.get("workdir") if isinstance(data, dict) else None
if isinstance(workdir, str) and workdir.strip():
    meta_lines.append(f"cd {workdir.strip()}")
write_file("meta.sh", meta_lines)

# Generate file_edits.json from job spec (file_patches)
file_edits = []
try:
    with open(spec_path, encoding="utf-8") as sp:
        spec = json.load(sp)
    patches = spec.get("file_patches") if isinstance(spec, dict) else []
    if isinstance(patches, list):
        for item in patches:
            if not isinstance(item, dict):
                continue
            path_val = str(item.get("path") or "").strip()
            action = str(item.get("action") or "").strip()
            content = item.get("content")
            find = item.get("find")
            if not path_val:
                continue
            file_edits.append(
                {
                    "path": path_val,
                    "action": action,
                    "content": content,
                    "find": find,
                }
            )
except Exception as exc:
    fail("Failed to load job spec for file edits", exc)

write_file("file_edits.json", file_edits)
PY

run_script() {
  local path="$1"
  local label="$2"
  if [[ -f "${path}" ]]; then
    echo "[RUN] ${label}: ${path}"
    (cd "${WORK_DIR}" && bash "${path}")
  else
    echo "[SKIP] ${label}: ${path} (missing)"
  fi
}

run_cmds_file() {
  local path="$1"
  local label="$2"
  if [[ ! -f "${path}" ]]; then
    echo "[SKIP] ${label}: ${path} (missing)"
    return 0
  fi
  echo "[RUN] ${label}: ${path}"
  ( cd "${WORK_DIR}" && while IFS= read -r line || [[ -n "${line}" ]]; do
      [[ -z "${line}" ]] && continue
      echo "+ ${line}"
      eval "${line}"
    done < "${path}"
  )
}

# Apply patches if provided
if [[ -s "${PATCHES_FILE}" ]]; then
  echo "[Patch] Applying patches from ${PATCHES_FILE}"
  if ! (cd "${WORK_DIR}" && python3 "${SCRIPT_DIR}/patcher.py" "${PATCHES_FILE}"); then
    echo "[Patch] Warning: patch application failed"
  fi
fi

run_script "${META_SH}" "Meta script"
echo "================= [GIT AUTH DEBUG START] ================="
echo "Current User: $(whoami)"
echo "HOME: $HOME"
echo "JOB_DIR: $JOB_DIR"
echo "XDG_CONFIG_HOME: ${XDG_CONFIG_HOME:-<unset>}"
echo "Checking .git-credentials..."
if [ -f "${JOB_DIR}/.git-credentials" ]; then
    echo "FOUND: ${JOB_DIR}/.git-credentials"
    echo "Content (Masked):"
    sed -E 's/:([^:@]{0,64})@/:***@/' "${JOB_DIR}/.git-credentials"
else
    echo "CRITICAL ERROR: .git-credentials NOT FOUND at ${JOB_DIR}/.git-credentials"
fi
echo "Effective Git Config:"
git config --list --show-origin
echo "Directory listing for JOB_DIR:"
ls -ld "${JOB_DIR}"
ls -la "${JOB_DIR}"
echo "================= [GIT AUTH DEBUG END] ================="
run_cmds_file "${CLONE_CMDS}" "Clone commands"
run_cmds_file "${INIT_CMDS}" "Init commands"
run_cmds_file "${MODIFY_CMDS}" "Modify commands"
run_cmds_file "${BUILD_CMDS}" "Build commands"

echo "Job ${JOB_ID} main steps completed."
exit 0
