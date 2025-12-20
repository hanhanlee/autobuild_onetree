#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <job_id>" >&2
  exit 1
fi

JOB_ID="$1"
MACHINE="${AUTOBUILD_MACHINE:-}"

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
echo "Loading recipe snapshot from ${JOB_DIR}/job.json"

write_status "RUNNING" "null" ""

CLONE_SCRIPT_FILE="${WORK_DIR}/project_clone.sh"
BUILD_SCRIPT_FILE="${WORK_DIR}/project_build.sh"
RECIPE_FILE="${WORK_DIR}/recipe.yaml"

python3 - "$JOB_DIR" "$WORK_DIR" "$RECIPE_FILE" "$CLONE_SCRIPT_FILE" "$BUILD_SCRIPT_FILE" <<'PY'
import json, os, sys
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover - runner runtime guard
    print(f"FATAL: PyYAML required to parse raw_recipe_yaml: {exc}", file=sys.stderr)
    sys.exit(12)

job_dir, work_dir, recipe_file, clone_path, build_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
path = os.path.join(job_dir, "job.json")
try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
except json.JSONDecodeError as exc:
    print(f"FATAL: job.json is invalid JSON: {exc}", file=sys.stderr)
    sys.exit(10)
except Exception as exc:
    print(f"FATAL: failed to read job.json: {exc}", file=sys.stderr)
    sys.exit(11)

snapshot = data.get("snapshot") or {}
raw_yaml = snapshot.get("raw_recipe_yaml") or data.get("raw_recipe_yaml")
recipe_id = snapshot.get("recipe_id") or data.get("recipe_id") or ""
note = snapshot.get("note") or data.get("note") or ""
if not raw_yaml:
    print("FATAL: raw_recipe_yaml missing from job snapshot", file=sys.stderr)
    sys.exit(13)
try:
    parsed = yaml.safe_load(raw_yaml)
    if parsed is None:
        parsed = {}
except Exception as exc:
    print(f"FATAL: unable to parse raw_recipe_yaml: {exc}", file=sys.stderr)
    sys.exit(14)

recipe_path = Path(recipe_file)
recipe_path.parent.mkdir(parents=True, exist_ok=True)
try:
    recipe_path.write_text(raw_yaml, encoding="utf-8")
except Exception as exc:
    print(f"FATAL: failed to write recipe file: {exc}", file=sys.stderr)
    sys.exit(15)

def _ensure_list(obj, field):
    if obj is None:
        return []
    if not isinstance(obj, (list, tuple)):
        print(f"FATAL: recipe field {field} must be a list", file=sys.stderr)
        sys.exit(16)
    items = []
    for idx, val in enumerate(obj):
        if not isinstance(val, str):
            print(f"FATAL: recipe field {field}[{idx}] must be a string", file=sys.stderr)
            sys.exit(17)
        cleaned = val.strip()
        if cleaned:
            items.append(cleaned)
    return items

def _ensure_file_appends(obj):
    if obj is None:
        return []
    if not isinstance(obj, (list, tuple)):
        print("FATAL: recipe field file_appends must be a list", file=sys.stderr)
        sys.exit(19)
    items = []
    for idx, val in enumerate(obj):
        if not isinstance(val, dict):
            print(f"FATAL: recipe field file_appends[{idx}] must be a mapping", file=sys.stderr)
            sys.exit(19)
        path = val.get("path") or ""
        append = val.get("append", "")
        if not isinstance(path, str) or not path.strip():
            print(f"FATAL: recipe field file_appends[{idx}].path must be a non-empty string", file=sys.stderr)
            sys.exit(19)
        if not isinstance(append, str):
            print(f"FATAL: recipe field file_appends[{idx}].append must be a string", file=sys.stderr)
            sys.exit(19)
        items.append({"path": path.strip(), "append": append})
    return items

clone_lines = _ensure_list((parsed.get("clone_block") or {}).get("lines"), "clone_block.lines")
init_lines = _ensure_list((parsed.get("init_block") or {}).get("lines"), "init_block.lines")
build_lines = _ensure_list((parsed.get("build_block") or {}).get("lines"), "build_block.lines")
workdir = parsed.get("workdir") or ""
file_appends = _ensure_file_appends(parsed.get("file_appends"))

recipe_run = Path(work_dir) / "recipe_run.sh"
script_lines = ["#!/usr/bin/env bash", "set -euo pipefail", 'echo "[recipe] start"']
if workdir:
    script_lines.append(f'cd "{workdir}"')
script_lines.append('base_dir="$(pwd)"')
if clone_lines:
    script_lines.append('echo "[recipe] clone_block"')
    script_lines.extend(clone_lines)
else:
    script_lines.append('echo "[recipe] no clone_block provided; skipping clone"')
if init_lines:
    script_lines.append('echo "[recipe] init_block"')
    script_lines.extend(init_lines)
if file_appends:
    script_lines.append('echo "[recipe] file_appends"')
    script_lines.append('if [[ -z "${base_dir}" || ! -d "${base_dir}" ]]; then echo "FATAL: base_dir not set for file_appends" >&2; exit 20; fi')
    for idx, item in enumerate(file_appends):
        script_lines.append(f'file_append_path_{idx}="{item["path"]}"')
        script_lines.append(f'if [[ -z "${{file_append_path_{idx}}}" ]]; then echo "FATAL: file_appends path missing" >&2; exit 21; fi')
        script_lines.append(f'abs_path_{idx}="$(realpath -m "${{file_append_path_{idx}}}")" || exit 21')
        script_lines.append(f'mkdir -p "$(dirname "${{abs_path_{idx}}}")"')
        script_lines.append(f'touch "${{abs_path_{idx}}}"')
        script_lines.append(f'resolved_path_{idx}="$(realpath "${{abs_path_{idx}}}")" || exit 21')
        script_lines.append(f'case "${{resolved_path_{idx}}}" in')
        script_lines.append('  "${base_dir}"|"${base_dir}"/*) ;;')
        script_lines.append(f'  *) echo "FATAL: file_appends path outside workdir: ${{file_append_path_{idx}}}" >&2; exit 21 ;;')
        script_lines.append('esac')
        script_lines.append(f'cat <<\'FILEAPPEND_{idx}\' >> "${{resolved_path_{idx}}}"')
        script_lines.append(item["append"])
        script_lines.append(f'FILEAPPEND_{idx}')
if build_lines:
    script_lines.append('echo "[recipe] build_block"')
    script_lines.extend(build_lines)
else:
    script_lines.append('echo "[recipe] no build_block provided; nothing to build"')

try:
    recipe_run.write_text("\n".join(script_lines) + "\n", encoding="utf-8")
    recipe_run.chmod(0o700)
except Exception as exc:
    print(f"FATAL: failed to write recipe_run.sh: {exc}", file=sys.stderr)
    sys.exit(18)

meta_path = Path(work_dir) / "snapshot_meta.json"
meta = {
    "recipe_id": recipe_id,
    "note": note,
    "created_by": snapshot.get("created_by") or data.get("created_by") or data.get("owner") or "",
    "created_at": snapshot.get("created_at") or data.get("created_at") or "",
}
try:
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
except Exception:
    pass

print(f"Snapshot recipe_id={recipe_id or '<unknown>'}")
if note:
    print(f"Snapshot note={note}")
PY

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

if [[ -f "${RECIPE_FILE}" ]]; then
  echo "Recipe snapshot saved at ${RECIPE_FILE}"
fi

RECIPE_RUN="${WORK_DIR}/recipe_run.sh"
if [[ ! -f "${RECIPE_RUN}" ]]; then
  echo "Recipe script missing; cannot continue" >&2
  exit 19
fi
echo "Executing recipe_run.sh ..."
(cd "${WORK_DIR}" && bash -e -u -o pipefail "${RECIPE_RUN}")

echo "Collecting artifacts..."
IMAGE_DIR="${WORK_DIR}/repo/build/tmp/deploy/images"
if [[ -n "${MACHINE}" ]]; then
  IMAGE_DIR="${IMAGE_DIR}/${MACHINE}"
fi
if [[ -d "${IMAGE_DIR}" ]]; then
  find "${IMAGE_DIR}" -maxdepth 1 -type f \( -name "*.bin" -o -name "*.mtd" \) -print0 | while IFS= read -r -d '' file; do
    cp "${file}" "${ARTIFACT_DIR}/"
  done
else
  echo "Image directory not found: ${IMAGE_DIR}"
fi

echo "Job ${JOB_ID} finished at $(timestamp)"
