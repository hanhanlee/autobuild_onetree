#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 5 ]]; then
  echo "Usage: $0 <job_id> <repo_url> <ref> <machine> <target>" >&2
  exit 1
fi

JOB_ID="$1"
REPO_URL="$2"
REF="$3"
MACHINE="$4"
TARGET="$5"

JOBS_ROOT="${AUTO_BUILD_JOBS_ROOT:-/srv/autobuild/jobs}"
JOB_DIR="${JOB_DIR:-${JOBS_ROOT}/${JOB_ID}}"
LOG_DIR="${JOB_DIR}/logs"
LOG_FILE="${LOG_DIR}/build.log"
ARTIFACT_DIR="${JOB_DIR}/artifacts"
STATUS_FILE="${JOB_DIR}/status.json"
EXIT_CODE_FILE="${JOB_DIR}/exit_code"
WORK_DIR="${JOB_DIR}/work"
TOKEN_FILE="${HOME}/.autobuild/gitlab_token"

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
echo "Repository: ${REPO_URL}"
echo "Ref: ${REF}"
echo "Machine: ${MACHINE}"
echo "Target: ${TARGET}"

write_status "RUNNING" "null" ""

if [[ ! -f "${TOKEN_FILE}" ]]; then
  echo "GitLab token not found at ${TOKEN_FILE}" >&2
  exit 2
fi
GITLAB_TOKEN="$(cat "${TOKEN_FILE}")"

ASKPASS="${WORK_DIR}/git_askpass.sh"
cat > "${ASKPASS}" <<'EOF'
#!/bin/sh
printf '%s\n' "$GITLAB_TOKEN"
EOF
chmod 700 "${ASKPASS}"

export GIT_ASKPASS="${ASKPASS}"
export GIT_TERMINAL_PROMPT=0
export GIT_CURL_VERBOSE=0

rm -rf "${WORK_DIR}/repo"
echo "Cloning repository..."
git -c credential.helper= -c core.askPass="${ASKPASS}" clone --depth 1 --branch "${REF}" "${REPO_URL}" "${WORK_DIR}/repo"

cd "${WORK_DIR}/repo"
echo "Repository cloned. Starting build placeholder..."
echo "TODO: replace with real Yocto build. Running simple checks."

# Placeholder build step; replace with real build invocation.
if [[ -x "./autobuild.sh" ]]; then
  ./autobuild.sh "${MACHINE}" "${TARGET}"
else
  echo "No autobuild.sh found; skipping real build."
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
