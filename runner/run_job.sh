#!/usr/bin/env bash
# [DEBUG] ?«æ??¿æ? pipefailï¼Œé¿??id ?‡ä»¤?ºéŒ¯å°±ç›´?¥é€€?ºï??‘å€‘é?è¦ç??°å??´ç??µéŒ¯è¨Šæ¯
set -eu
# set -euo pipefail

# 1. [?œéµä¿®æ­£] è¨­å? umask ??002ï¼Œç¢ºä¿ç¾¤çµ„å¯å¯?umask 002

# ================= [DEBUG DIAGNOSIS START] =================
# ?™æ®µä»?¢¼?ªè?è²¬å°?ºæ??è?è¨Šï?å®Œå…¨ä¸å½±?¿å??¢ç?æ¥­å??è¼¯
echo "================= [DEBUG INFO] ================="
echo "Timestamp: $(date)"

# 1. æª¢æŸ¥?·è?èº«å?
echo "[1] Current User Identity:"
id
echo "Effective User: $(whoami)"

# 2. æª¢æŸ¥?°å?è®Šæ•¸
echo "[2] Job Parameters:"
echo "JOB_ID (Arg 1): ${1:-<missing>}"
echo "AUTOBUILD_JOBS_ROOT: ${AUTOBUILD_JOBS_ROOT:-<unset>}"

# 3. æ¨¡æ“¬è¨ˆç?è·¯å? (?‡ä??¹é?è¼¯ä???
_JOB_ID="${1:-}"
_JOBS_ROOT="${AUTOBUILD_JOBS_ROOT:-${AUTO_BUILD_JOBS_ROOT:-/opt/autobuild/workspace/jobs}}"
_JOB_DIR="${_JOBS_ROOT}/${_JOB_ID}"
_WORK_DIR="${_JOB_DIR}/work"

# 4. æª¢æŸ¥?¶ç›®?„æ???if [ -d "${_JOB_DIR}" ]; then
    echo "[3] Permissions of JOB_DIR (${_JOB_DIR}):"
    ls -ld "${_JOB_DIR}"
else
    echo "[3] JOB_DIR (${_JOB_DIR}) does not exist yet."
    echo "Parent (JOBS_ROOT) permissions:"
    ls -ld "${_JOBS_ROOT}"
fi

# 5. ?¾å ´å¯«å…¥æ¸¬è©¦ (Touch Test)
if [ -d "${_JOB_DIR}" ]; then
    echo "[4] Try to write to JOB_DIR:"
    if touch "${_JOB_DIR}/debug_write_test" 2>/dev/null; then
        echo "SUCCESS: Write permission confirmed."
        rm "${_JOB_DIR}/debug_write_test"
    else
        echo "FAILURE: Cannot write to ${_JOB_DIR}!"
    fi
fi

# 6. æª¢æŸ¥?›è?é»?(?’é™¤ Systemd ReadOnlyPaths å¹²æ“¾)
echo "[5] Mount info for /work:"
grep "/work" /proc/self/mounts || echo "/work not found in mounts"

echo "================= [DEBUG INFO END] ================="
# ================= [DEBUG DIAGNOSIS END] =================


# --- ä»¥ä??ºæ‚¨?Ÿæœ¬?„ä»£ç¢?(å®Œå…¨ä¿ç?ï¼Œåª??mkdir ?•å?å¼·éŒ¯èª¤é¡¯ç¤? ---

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

# [DEBUG] ?¨é€™è£¡? å…¥?¯èª¤?•æ?ï¼Œå???mkdir å¤±æ?ï¼Œå°?ºæ›´è©³ç´°?„å???if ! mkdir -p "${LOG_DIR}" "${ARTIFACT_DIR}" "${WORK_DIR}"; then
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

python3 - "${SPEC_PATH}" "${RAW
