#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <job_id> [repo_url] [ref] [machine] [target]" >&2
  exit 1
fi

JOB_ID="$1"
REPO_URL="${2:-}"
REF="${3:-}"
MACHINE="${4:-}"
TARGET="${5:-}"

JOBS_ROOT="${AUTOBUILD_JOBS_ROOT:-${AUTO_BUILD_JOBS_ROOT:-/opt/autobuild/workspace/jobs}}"
: "${JOB_DIR:=${JOBS_ROOT}/${JOB_ID}}"
LOG_DIR="${JOB_DIR}/logs"
LOG_FILE="${LOG_DIR}/build.log"
ARTIFACT_DIR="${JOB_DIR}/artifacts"
STATUS_FILE="${JOB_DIR}/status.json"
EXIT_CODE_FILE="${JOB_DIR}/exit_code"
WORK_DIR="${JOB_DIR}/work"
OWNER="${AUTOBUILD_JOB_OWNER:-}"
if [[ -z "${OWNER}" ]]; then
OWNER="$(python3 - "${JOB_DIR}" <<'PY'
import json, os, sys

job_dir = sys.argv[1]
owner = None
path = os.path.join(job_dir, "job.json")
try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    owner = data.get("created_by") or data.get("owner")
except Exception:
    owner = None
owner = owner or os.environ.get("SUDO_USER") or os.environ.get("USER")
if not owner:
    sys.exit(1)
print(owner)
PY
)"
fi
if [[ -z "${OWNER}" ]]; then
  OWNER="${USER:-${SUDO_USER:-}}"
fi
if [[ -z "${OWNER}" ]]; then
  echo "Failed to determine job owner; JOB_DIR=${JOB_DIR}" >&2
  exit 1
fi
echo "JOB_DIR=${JOB_DIR}"
echo "OWNER=${OWNER}"
TOKEN_ROOT="${AUTOBUILD_TOKEN_ROOT:-/opt/autobuild/workspace/secrets/gitlab}"
TOKEN_FILE="${TOKEN_ROOT}/${OWNER}.token"

mkdir -p "${LOG_DIR}" "${ARTIFACT_DIR}" "${WORK_DIR}"
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

cleanup() {
  local code=$?
  echo "Job ${JOB_ID} completed with code ${code}"
  echo "${code}" > "${EXIT_CODE_FILE}"
  local finished_at
  finished_at=$(timestamp)
  if [[ ${code} -eq 0 ]]; then
    write_status "SUCCESS" "${code}" "${finished_at}"
  else
    write_status "FAILED" "${code}" "${finished_at}"
  fi
}
trap cleanup EXIT

exec > >(tee -a "${LOG_FILE}") 2>&1

echo "Starting job ${JOB_ID} at $(timestamp)"
echo "Repository: ${REPO_URL:-<none>}"
echo "Ref: ${REF:-<none>}"
echo "Machine: ${MACHINE:-<none>}"
echo "Target: ${TARGET:-<none>}"

write_status "RUNNING" "null" ""

PROJECT_CLONE_SCRIPT=""
PROJECT_BUILD_SCRIPT=""
python3 - "$JOB_DIR" "$WORK_DIR" <<'PY'
import json, os, sys
job_dir, work_dir = sys.argv[1], sys.argv[2]
path = os.path.join(job_dir, "job.json")
try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    proj = (data.get("effective") or {}).get("project") or data.get("project") or {}
    clone_script = proj.get("clone_script") or ""
    build_script = proj.get("build_script") or ""
    if clone_script:
        try:
            with open(os.path.join(work_dir, "project_clone.sh"), "w", encoding="utf-8") as f:
                f.write(clone_script.rstrip("\n") + "\n")
        except Exception:
            sys.exit(2)
    if build_script:
        try:
            with open(os.path.join(work_dir, "project_build.sh"), "w", encoding="utf-8") as f:
                f.write(build_script.rstrip("\n") + "\n")
        except Exception:
            sys.exit(2)
except json.JSONDecodeError:
    sys.exit(3)
except Exception:
    sys.exit(4)
PY
status=$?
if [[ ${status} -ne 0 ]]; then
  if [[ ${status} -eq 3 ]]; then
    echo "WARN: failed to parse job.json for project scripts; falling back to default flow" >> "${LOG_FILE}"
  elif [[ ${status} -eq 2 ]]; then
    echo "WARN: failed to write project scripts from job.json; falling back to default flow" >> "${LOG_FILE}"
  else
    echo "WARN: failed to load project scripts from job.json; falling back to default flow" >> "${LOG_FILE}"
  fi
fi

if [[ ! -f "${TOKEN_FILE}" ]]; then
  echo "GitLab token not found for user ${OWNER} at ${TOKEN_FILE}" >&2
  exit 2
fi
GITLAB_TOKEN="$(tr -d '\r\n' < "${TOKEN_FILE}")"
if [[ -z "${GITLAB_TOKEN}" ]]; then
  echo "GitLab token is empty for user ${OWNER} at ${TOKEN_FILE}" >&2
  exit 2
fi

ASKPASS="${WORK_DIR}/git_askpass.sh"
cat > "${ASKPASS}" <<'EOF'
#!/bin/sh
printf '%s\n' "$GITLAB_TOKEN"
EOF
chmod 700 "${ASKPASS}"

export GIT_ASKPASS="${ASKPASS}"
export GIT_TERMINAL_PROMPT=0
export GIT_CURL_VERBOSE=0

CLONE_SCRIPT_FILE="${WORK_DIR}/project_clone.sh"
BUILD_SCRIPT_FILE="${WORK_DIR}/project_build.sh"
RECIPE_FILE="${WORK_DIR}/recipe.yaml"

if [[ -f "${RECIPE_FILE}" ]]; then
  echo "Recipe mode detected; recipe saved at ${RECIPE_FILE}"
fi

if [[ -f "${CLONE_SCRIPT_FILE}" ]]; then
  echo "Running project clone script..."
  chmod 700 "${CLONE_SCRIPT_FILE}"
  (cd "${WORK_DIR}" && bash -e -u -o pipefail "${CLONE_SCRIPT_FILE}")
elif [[ -n "${REPO_URL}" ]]; then
  echo "Using legacy repo clone flow..."
  rm -rf "${WORK_DIR}/repo"
  git -c core.askPass="${ASKPASS}" clone --depth 1 --branch "${REF:-main}" "${REPO_URL}" "${WORK_DIR}/repo"
  cd "${WORK_DIR}/repo" || true
else
  echo "No clone instructions provided (recipe-only mode). Skipping clone."
fi

if [[ -f "${BUILD_SCRIPT_FILE}" ]]; then
  echo "Running project build script..."
  chmod 700 "${BUILD_SCRIPT_FILE}"
  (cd "${WORK_DIR}" && bash -e -u -o pipefail "${BUILD_SCRIPT_FILE}")
else
  echo "No build script provided; placeholder complete."
fi

echo "Collecting artifacts..."
IMAGE_DIR="${WORK_DIR}/repo/build/tmp/deploy/images/${MACHINE}"
if [[ -d "${IMAGE_DIR}" ]]; then
  find "${IMAGE_DIR}" -maxdepth 1 -type f \( -name "*.bin" -o -name "*.mtd" \) -print0 | while IFS= read -r -d '' file; do
    cp "${file}" "${ARTIFACT_DIR}/"
  done
else
  echo "Image directory not found: ${IMAGE_DIR}"
fi

echo "Job ${JOB_ID} finished at $(timestamp)"
