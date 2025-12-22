#!/usr/bin/env bash
set -euo pipefail
umask 002

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <job_id>" >&2
  exit 1
fi

JOB_ID="$1"
JOBS_ROOT="${AUTOBUILD_JOBS_ROOT:-${AUTO_BUILD_JOBS_ROOT:-/srv/autobuild/jobs}}"
: "${JOB_DIR:=${JOBS_ROOT}/${JOB_ID}}"
LOG_DIR="${JOB_DIR}/logs"
LOG_FILE="${LOG_DIR}/build.log"
ARTIFACT_DIR="${JOB_DIR}/artifacts"
STATUS_FILE="${JOB_DIR}/status.json"
EXIT_CODE_FILE="${JOB_DIR}/exit_code"
WORK_DIR="${JOB_DIR}/work"
WORKSPACES_ROOT="/srv/autobuild/workspaces"

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
  if [[ ${LOCK_ACQUIRED:-0} -eq 1 ]]; then
    echo "[lock] releasing codebase lock"
  fi
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

write_status "RUNNING" "null" ""

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

python3 - "${SPEC_PATH}" "${RAW_RECIPE}" "${META_SH}" "${CLONE_CMDS}" "${INIT_CMDS}" "${MODIFY_CMDS}" "${BUILD_CMDS}" "${FILE_EDITS_JSON}" <<'PY'
import hashlib
import json
import shlex
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover - runtime guard
    print(f"CONFIG ERROR: PyYAML required to parse raw_recipe.yaml: {exc}", file=sys.stderr)
    sys.exit(2)

spec_path, raw_recipe_path, meta_sh, clone_out, init_out, modify_out, build_out, edits_out = sys.argv[1:]

spec = {}
try:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
except Exception as exc:
    print(f"CONFIG ERROR: failed to read job spec: {exc}", file=sys.stderr)
    sys.exit(2)

raw_recipe_file = Path(raw_recipe_path)
if not raw_recipe_file.exists() or raw_recipe_file.stat().st_size == 0:
    print(f"CONFIG ERROR: raw_recipe.yaml missing or empty: {raw_recipe_file}", file=sys.stderr)
    sys.exit(2)

raw_content = raw_recipe_file.read_text(encoding="utf-8")
try:
    parsed = yaml.safe_load(raw_content)
    if parsed is None:
        parsed = {}
except Exception as exc:
    print(f"CONFIG ERROR: failed to parse raw_recipe.yaml: {exc}", file=sys.stderr)
    sys.exit(2)
if "file_appends" in parsed:
    print("CONFIG ERROR: file_appends not supported; use modify_lines/modify_block instead", file=sys.stderr)
    sys.exit(2)

snapshot = spec.get("snapshot") or {}
mode = (spec.get("mode") or snapshot.get("mode") or "full").strip().lower() or "full"
codebase_id = (spec.get("codebase_id") or snapshot.get("codebase_id") or "").strip()
recipe_id = snapshot.get("recipe_id") or spec.get("recipe_id") or ""
note = snapshot.get("note") or spec.get("note") or ""
created_by = snapshot.get("created_by") or spec.get("created_by") or spec.get("owner") or ""
created_at = snapshot.get("created_at") or spec.get("created_at") or ""

def ensure_lines(value, field):
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        print(f"CONFIG ERROR: {field} must be a list of strings", file=sys.stderr)
        sys.exit(2)
    out = []
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            print(f"CONFIG ERROR: {field}[{idx}] must be a string", file=sys.stderr)
            sys.exit(2)
        line = item.strip()
        if line:
            out.append(line)
    return out

def extract_lines(obj, keys):
    for key in keys:
        if key not in obj:
            continue
        val = obj.get(key)
        if isinstance(val, dict):
            val = val.get("lines")
        lines = ensure_lines(val, key)
        return lines
    return []

clone_lines = extract_lines(parsed, ["clone_block", "clone_lines", "clone"])
init_lines = extract_lines(parsed, ["init_block", "init_lines", "init"])
modify_lines = extract_lines(parsed, ["modify_block", "modify_lines", "modify"])
build_lines = extract_lines(parsed, ["build_block", "build_lines", "build"])
workdir = parsed.get("workdir") or parsed.get("work_dir") or ""

if mode not in {"full", "clone_only", "build_only", "edit_only"}:
    print(f"CONFIG ERROR: invalid mode: {mode}", file=sys.stderr)
    sys.exit(2)
if mode in {"full", "clone_only"} and not clone_lines:
    print("CONFIG ERROR: clone commands required for mode", mode, file=sys.stderr)
    sys.exit(2)
if mode in {"full", "build_only"} and not build_lines:
    print("CONFIG ERROR: build commands required for mode", mode, file=sys.stderr)
    sys.exit(2)

def parse_file_edits(obj):
    edits_raw = obj.get("file_edits") if isinstance(obj, dict) else None
    if edits_raw is None:
        return []
    if not isinstance(edits_raw, list):
        print("CONFIG ERROR: file_edits must be a list", file=sys.stderr)
        sys.exit(2)
    edits = []
    for idx, edit in enumerate(edits_raw):
        if not isinstance(edit, dict):
            print(f"CONFIG ERROR: file_edits[{idx}] must be an object", file=sys.stderr)
            sys.exit(2)
        path = edit.get("path")
        if not isinstance(path, str) or not path.strip():
            print(f"CONFIG ERROR: file_edits[{idx}].path must be a non-empty string", file=sys.stderr)
            sys.exit(2)
        ensure_lines = edit.get("ensure_lines")
        ensure_block = edit.get("ensure_block")
        if ensure_lines is None and ensure_block is None:
            print(f"CONFIG ERROR: file_edits[{idx}] must contain ensure_lines or ensure_block", file=sys.stderr)
            sys.exit(2)
        if ensure_lines is not None:
            if isinstance(ensure_lines, str):
                ensure_lines = [ensure_lines]
            if not isinstance(ensure_lines, list) or not all(isinstance(x, str) for x in ensure_lines):
                print(f"CONFIG ERROR: file_edits[{idx}].ensure_lines must be a list of strings", file=sys.stderr)
                sys.exit(2)
        if ensure_block is not None:
            if not isinstance(ensure_block, dict):
                print(f"CONFIG ERROR: file_edits[{idx}].ensure_block must be an object", file=sys.stderr)
                sys.exit(2)
            marker = ensure_block.get("marker")
            content = ensure_block.get("content")
            if not isinstance(marker, str) or not marker.strip() or not isinstance(content, str):
                print(f"CONFIG ERROR: file_edits[{idx}].ensure_block requires marker and content strings", file=sys.stderr)
                sys.exit(2)
        edits.append({"path": path, "ensure_lines": ensure_lines, "ensure_block": ensure_block})
    return edits

file_edits = parse_file_edits(parsed)

meta_lines = []
def emit(key, value):
    meta_lines.append(f'{key}={shlex.quote(value)}')

emit("MODE", mode)
emit("CODEBASE_ID_RAW", codebase_id)
emit("WORKDIR", str(workdir).strip())
emit("RECIPE_ID", recipe_id)
emit("NOTE", note)
emit("CREATED_BY", created_by)
emit("CREATED_AT", created_at)

sha256 = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
emit("RAW_SHA256", sha256)
emit("RAW_BYTES", str(len(raw_content.encode("utf-8"))))
emit("RAW_LINES", str(len(raw_content.splitlines())))

Path(meta_sh).write_text("\n".join(meta_lines) + "\n", encoding="utf-8")
Path(clone_out).write_text("\n".join(clone_lines), encoding="utf-8")
Path(init_out).write_text("\n".join(init_lines), encoding="utf-8")
Path(modify_out).write_text("\n".join(modify_lines), encoding="utf-8")
Path(build_out).write_text("\n".join(build_lines), encoding="utf-8")
Path(edits_out).write_text(json.dumps(file_edits), encoding="utf-8")
PY

if [[ ! -f "${META_SH}" ]]; then
  echo "CONFIG ERROR: failed to prepare metadata" >&2
  exit 2
fi

# shellcheck source=/dev/null
source "${META_SH}"

MODE="${MODE:-full}"
CODEBASE_ID="${CODEBASE_ID_RAW:-}"
WORKDIR_VAL="${WORKDIR:-}"

RAW_SHA256="${RAW_SHA256:-}"
RAW_BYTES="${RAW_BYTES:-0}"
RAW_LINES="${RAW_LINES:-0}"
BASE_DIR="${CODEBASE_DIR:-}"
APPLY_FILE_EDITS_FOR_BUILD_ONLY="${APPLY_FILE_EDITS_FOR_BUILD_ONLY:-1}"

if [[ -z "${CODEBASE_ID}" && "${MODE}" =~ ^(full|clone_only)$ ]]; then
  CODEBASE_ID="job-${JOB_ID}"
fi

if [[ -z "${MODE}" ]]; then
  echo "CONFIG ERROR: mode missing" >&2
  exit 2
fi

if [[ -z "${CODEBASE_ID}" ]] && [[ "${MODE}" =~ ^(build_only|edit_only)$ ]]; then
  echo "CONFIG ERROR: codebase_id is required for mode ${MODE}" >&2
  exit 2
fi

if [[ -n "${CODEBASE_ID}" ]]; then
  if [[ "${CODEBASE_ID}" == *".."* ]] || [[ "${CODEBASE_ID}" == *"/"* ]] || [[ "${CODEBASE_ID}" == *"\\"* ]] || ! [[ "${CODEBASE_ID}" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "CONFIG ERROR: invalid codebase_id ${CODEBASE_ID}" >&2
    exit 2
  fi
fi

echo "job_id=${JOB_ID}"
echo "job_dir=${JOB_DIR}"
echo "spec=${SPEC_PATH}"
echo "mode=${MODE}"
echo "codebase_id=${CODEBASE_ID:-<none>}"
echo "raw_recipe.yaml sha256=${RAW_SHA256} bytes=${RAW_BYTES} lines=${RAW_LINES}"
echo "workdir=${WORKDIR_VAL:-<none>}"

OWNER="${CREATED_BY:-${AUTOBUILD_JOB_OWNER:-${SUDO_USER:-${USER:-}}}}"
if [[ -z "${OWNER}" ]]; then
  echo "CONFIG ERROR: cannot determine owner" >&2
  exit 2
fi

TOKEN_ROOT="${AUTOBUILD_TOKEN_ROOT:-/opt/autobuild/workspace/secrets/gitlab}"
TOKEN_FILE="${TOKEN_ROOT}/${OWNER}.token"

mkdir -p "${WORKSPACES_ROOT}"

CODEBASE_DIR="${WORKSPACES_ROOT}/${CODEBASE_ID}"
LOCK_TIMEOUT_SECONDS="${LOCK_TIMEOUT_SECONDS:-600}"
LOCKFILE="${CODEBASE_DIR}/.lock"
WORKSPACE_CREATED=0

if [[ ! -d "${CODEBASE_DIR}" ]]; then
  if [[ "${MODE}" =~ ^(full|clone_only)$ ]]; then
    echo "Creating workspace at ${CODEBASE_DIR}"
    mkdir -p "${CODEBASE_DIR}"
    cat > "${CODEBASE_DIR}/codebase.json" <<EOF
{"id": "${CODEBASE_ID}", "label": "${CODEBASE_ID}", "owner": "${OWNER}", "created_at": "$(timestamp)"}
EOF
  else
    echo "CONFIG ERROR: workspace ${CODEBASE_DIR} missing for mode ${MODE}" >&2
    exit 2
  fi
  WORKSPACE_CREATED=1
fi

if [[ ! -f "${CODEBASE_DIR}/codebase.json" ]]; then
  echo "CONFIG ERROR: workspace ${CODEBASE_DIR} missing codebase.json" >&2
  exit 2
fi

validate_workdir() {
  if [[ -z "${WORKDIR_VAL}" ]]; then
    return 0
  fi
  local target="${CODEBASE_DIR}/${WORKDIR_VAL}"
  if [[ -d "${target}" ]]; then
    return 0
  fi
  echo "CONFIG ERROR: workdir '${WORKDIR_VAL}' not found under ${CODEBASE_DIR}" >&2
  echo "CONFIG ERROR: available subdirs:" >&2
  if ls -1d "${CODEBASE_DIR}"/*/ >/dev/null 2>&1; then
    ls -1d "${CODEBASE_DIR}"/*/ | sed 's:.*/::' >&2
  else
    echo "(none)" >&2
  fi
  exit 2
}

if [[ "${MODE}" =~ ^(build_only|edit_only)$ || "${WORKSPACE_CREATED}" != "1" ]]; then
  validate_workdir
fi

echo "[lock] attempting to acquire codebase lock at ${LOCKFILE} (timeout=${LOCK_TIMEOUT_SECONDS}s)"
exec 200>"${LOCKFILE}"
if ! flock -w "${LOCK_TIMEOUT_SECONDS}" 200; then
  echo "CONFIG ERROR: failed to acquire lock for codebase_id=${CODEBASE_ID} lockfile=${LOCKFILE} timeout=${LOCK_TIMEOUT_SECONDS}s (job_id=${JOB_ID})" >&2
  exit 2
fi
echo "[lock] acquired codebase lock for ${CODEBASE_ID}"
LOCK_ACQUIRED=1

touch_codebase_last_used() {
  python3 - "${CODEBASE_DIR}/codebase.json" <<'PY'
import json, sys, tempfile, time
from pathlib import Path

meta_path = Path(sys.argv[1])
try:
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print(f"[meta] warning: codebase.json is not an object: {meta_path}", file=sys.stderr)
        sys.exit(0)
except Exception as exc:
    print(f"[meta] warning: failed to parse {meta_path}: {exc}", file=sys.stderr)
    sys.exit(0)

now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
data["last_used_at"] = now

tmp = meta_path.with_suffix(".json.tmp")
try:
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(meta_path)
    print(f"[meta] updated last_used_at={now}")
except Exception as exc:
    print(f"[meta] warning: failed to update last_used_at for {meta_path}: {exc}", file=sys.stderr)
PY
}

touch_codebase_last_used

if [[ ! -f "${TOKEN_FILE}" ]]; then
  echo "WARN: GitLab token not found for ${OWNER} at ${TOKEN_FILE}; clone commands may fail" >&2
else
  GITLAB_TOKEN="$(tr -d '\r\n' < "${TOKEN_FILE}")"
  if [[ -n "${GITLAB_TOKEN}" ]]; then
    ASKPASS="${WORK_DIR}/git_askpass.sh"
    cat > "${ASKPASS}" <<'EOF'
#!/bin/sh
printf '%s\n' "$GITLAB_TOKEN"
EOF
    chmod 700 "${ASKPASS}"
    export GIT_ASKPASS="${ASKPASS}"
  fi
fi
export GIT_TERMINAL_PROMPT=0
export GIT_CURL_VERBOSE=0

run_stage() {
  local stage="$1"
  local file="$2"
  # recipe lines are shell commands; evaluated as-is
  if [[ ! -s "${file}" ]]; then
    echo "[${stage}] no commands; skipping"
    return 0
  fi
  if [[ "${stage}" == "init" ]]; then
    echo "[${stage}] running ${file} in isolated subshell"
    local init_wrapper
    init_wrapper="$(mktemp "${WORK_DIR:-/tmp}/init_wrapper.XXXXXX")"
    {
      echo "set +u"
      cat "${file}"
      echo "env"
    } > "${init_wrapper}"
    chmod +x "${init_wrapper}"
    local init_env
    init_env="$(/bin/bash "${init_wrapper}")"
    rm -f "${init_wrapper}"
    while IFS='=' read -r key value; do
      case "${key}" in
        PATH)
          export PATH="${value}"
          ;;
        PWD)
          cd "${value}" || exit 2
          ;;
        BUILDDIR)
          export BUILDDIR="${value}"
          ;;
      esac
    done <<< "${init_env}"
    return 0
  fi
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ -z "${line}" ]] && continue
    echo "[${stage}] ${line}"
    eval "${line}"
  done < "${file}"
}

apply_file_edits() {
  local base_dir="$1"
  local edits_json="$2"
  if [[ ! -s "${edits_json}" ]]; then
    echo "[file_edits] total=0 (none)"
    return 0
  fi
  python3 - "${base_dir}" "${edits_json}" <<'PY'
import json, os, sys, tempfile
from pathlib import Path

base_dir = Path(sys.argv[1]).resolve()
edits_path = Path(sys.argv[2])
try:
    edits = json.loads(edits_path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"CONFIG ERROR: failed to load file_edits: {exc}", file=sys.stderr)
    sys.exit(2)

if not isinstance(edits, list):
    print("CONFIG ERROR: file_edits must be a list", file=sys.stderr)
    sys.exit(2)

print(f"[file_edits] total={len(edits)}")

def safe_target(rel_path: str) -> Path:
    if rel_path.startswith("/") or "\x00" in rel_path or "\\" in rel_path or ".." in rel_path:
        raise ValueError(f"Unsafe path: {rel_path}")
    target = (base_dir / rel_path).resolve()
    try:
        base_resolved = base_dir.resolve()
    except Exception as exc:
        raise ValueError(f"Base dir not accessible: {exc}")
    if base_resolved not in target.parents and target != base_resolved:
        raise ValueError(f"Path escapes workspace: {rel_path}")
    return target

def ensure_lines(path: Path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            print(f"CONFIG ERROR: failed to read {path}: {exc}", file=sys.stderr)
            sys.exit(2)
    existing_set = set(existing)
    appended = 0
    for line in lines:
        if line in existing_set:
            continue
        existing.append(line)
        existing_set.add(line)
        appended += 1
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
            tmp_path = Path(tmp.name)
            tmp.write("\n".join(existing) + ("\n" if existing else ""))
        tmp_path.replace(path)
    except Exception as exc:
        print(f"CONFIG ERROR: failed to write {path}: {exc}", file=sys.stderr)
        sys.exit(2)
    print(f"[file_edits] {path}: ensured {len(lines)} lines (appended {appended})")

def ensure_block(path: Path, marker: str, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ""
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"CONFIG ERROR: failed to read {path}: {exc}", file=sys.stderr)
            sys.exit(2)
    begin = f"# BEGIN {marker}"
    end = f"# END {marker}"
    if begin in text and end in text and text.index(begin) < text.index(end):
        before = text.split(begin, 1)[0]
        after = text.split(end, 1)[1]
        new_text = before + begin + "\n" + content.rstrip("\n") + "\n" + end + after
    else:
        extra_newline = "" if text.endswith("\n") or text == "" else "\n"
        new_text = text + extra_newline + begin + "\n" + content.rstrip("\n") + "\n" + end + "\n"
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(new_text)
        tmp_path.replace(path)
    except Exception as exc:
        print(f"CONFIG ERROR: failed to write block in {path}: {exc}", file=sys.stderr)
        sys.exit(2)
    print(f"[file_edits] {path}: ensured block {marker}")

for edit in edits:
    rel_path = edit.get("path")
    ensure_lines_data = edit.get("ensure_lines")
    ensure_block_data = edit.get("ensure_block")
    try:
        target = safe_target(rel_path)
    except ValueError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    if ensure_lines_data is not None:
        ensure_lines(target, ensure_lines_data or [])
    if ensure_block_data is not None:
        ensure_block(target, ensure_block_data.get("marker", ""), ensure_block_data.get("content", ""))
PY
}

cd "${CODEBASE_DIR}"
if [[ -n "${WORKDIR_VAL}" ]]; then
  mkdir -p "${WORKDIR_VAL}"
fi

case "${MODE}" in
  full)
    ( cd "${CODEBASE_DIR}" && run_stage "clone" "${CLONE_CMDS}" )
    validate_workdir
    if [[ -n "${WORKDIR_VAL}" ]]; then cd "${CODEBASE_DIR}/${WORKDIR_VAL}" || exit 2; else cd "${CODEBASE_DIR}" || exit 2; fi
    apply_file_edits "${BASE_DIR}" "${FILE_EDITS_JSON}"
    run_stage "init" "${INIT_CMDS}"
    run_stage "modify" "${MODIFY_CMDS}"
    run_stage "build" "${BUILD_CMDS}"
    ;;
  clone_only)
    ( cd "${CODEBASE_DIR}" && run_stage "clone" "${CLONE_CMDS}" )
    validate_workdir
    ;;
  edit_only)
    if [[ -n "${WORKDIR_VAL}" ]]; then cd "${CODEBASE_DIR}/${WORKDIR_VAL}" || exit 2; else cd "${CODEBASE_DIR}" || exit 2; fi
    apply_file_edits "${BASE_DIR}" "${FILE_EDITS_JSON}"
    run_stage "modify" "${MODIFY_CMDS}"
    ;;
  build_only)
    if [[ -n "${WORKDIR_VAL}" ]]; then cd "${CODEBASE_DIR}/${WORKDIR_VAL}" || exit 2; else cd "${CODEBASE_DIR}" || exit 2; fi
    if [[ "${APPLY_FILE_EDITS_FOR_BUILD_ONLY}" != "0" ]]; then
      apply_file_edits "${BASE_DIR}" "${FILE_EDITS_JSON}"
    else
      echo "[file_edits] skipped for build_only (APPLY_FILE_EDITS_FOR_BUILD_ONLY=0)"
    fi
    run_stage "build" "${BUILD_CMDS}"
    ;;
  *)
    echo "CONFIG ERROR: unknown mode ${MODE}" >&2
    exit 2
    ;;
esac

echo "Collecting artifacts..."
IMAGE_DIR="${PWD}/repo/build/tmp/deploy/images"
if [[ -n "${AUTOBUILD_MACHINE:-}" ]]; then
  IMAGE_DIR="${IMAGE_DIR}/${AUTOBUILD_MACHINE}"
fi
if [[ -d "${IMAGE_DIR}" ]]; then
  find "${IMAGE_DIR}" -maxdepth 1 -type f \( -name "*.bin" -o -name "*.mtd" \) -print0 | while IFS= read -r -d '' file; do
    cp "${file}" "${ARTIFACT_DIR}/"
  done
else
  echo "Image directory not found: ${IMAGE_DIR}"
fi

echo "Job ${JOB_ID} finished at $(timestamp)"
