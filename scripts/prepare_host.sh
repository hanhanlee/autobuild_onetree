#!/usr/bin/env bash
# scripts/prepare_host.sh
#
# Idempotent host bootstrap for Autobuild Onetree.
# Performs everything DEPLOY_GUIDE.md sections 1-3 list manually:
#   1. install OS dependencies (Yocto + app)
#   2. create scm-bmc group and autobuild service user
#   3. create /opt/autobuild and /work/autobuild_workspace tree with
#      correct ownership / SetGID / mode
#   4. add the invoking user to scm-bmc
#
# Safe to re-run. Does NOT touch:
#   - /opt/autobuild/.env  (create manually with `sudoedit`)
#   - source code under /opt/autobuild  (use tools/deploy_autobuild.sh)
#   - systemd unit installation        (do that after .env exists)
#
# Usage:
#   sudo ./scripts/prepare_host.sh
#   sudo ./scripts/prepare_host.sh --skip-apt   # skip OS package install

set -euo pipefail

SKIP_APT=0
for arg in "$@"; do
    case "$arg" in
        --skip-apt) SKIP_APT=1 ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *)
            echo "unknown arg: $arg" >&2
            exit 1
            ;;
    esac
done

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Please run with sudo: sudo $0" >&2
    exit 1
fi

INVOKING_USER="${SUDO_USER:-}"

GROUP_NAME="scm-bmc"
SERVICE_USER="autobuild"
APP_DIR="/opt/autobuild"
WORK_ROOT="/work/autobuild_workspace"
JOBS_DIR="$WORK_ROOT/jobs"
DATA_DIR="$WORK_ROOT/data"
SECRETS_DIR="$WORK_ROOT/secrets/gitlab"

YOCTO_PKGS=(
    gawk wget git diffstat unzip texinfo gcc-multilib build-essential chrpath
    socat cpio python3 python3-pip python3-pexpect xz-utils debianutils
    iputils-ping python3-git python3-jinja2 libegl1-mesa libsdl1.2-dev
    pylint3 xterm python3-subunit mesa-common-dev zstd liblz4-tool
)
APP_PKGS=(python3-venv libpam0g-dev rsync git nginx)

log() { echo -e "\033[0;34m[prepare]\033[0m $*"; }
ok()  { echo -e "\033[0;32m[ ok ]\033[0m $*"; }
warn(){ echo -e "\033[1;33m[warn]\033[0m $*"; }

# ---------- 1. apt packages ----------
if [[ "$SKIP_APT" -eq 1 ]]; then
    warn "skipping apt install (--skip-apt)"
else
    log "1/4 installing OS packages"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y "${YOCTO_PKGS[@]}" "${APP_PKGS[@]}"
    ok "OS packages installed"
fi

# ---------- 2. group + service user ----------
log "2/4 ensuring group $GROUP_NAME and user $SERVICE_USER"
if ! getent group "$GROUP_NAME" >/dev/null; then
    groupadd "$GROUP_NAME"
    ok "created group $GROUP_NAME"
else
    ok "group $GROUP_NAME already present"
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    adduser --system --group "$SERVICE_USER"
    ok "created system user $SERVICE_USER"
else
    ok "user $SERVICE_USER already present"
fi
usermod -aG "$GROUP_NAME" "$SERVICE_USER"

if [[ -n "$INVOKING_USER" && "$INVOKING_USER" != "root" ]]; then
    usermod -aG "$GROUP_NAME" "$INVOKING_USER"
    ok "added $INVOKING_USER to $GROUP_NAME (re-login required)"
fi

# ---------- 3. directories ----------
log "3/4 creating directory tree"
mkdir -p "$APP_DIR" "$JOBS_DIR" "$DATA_DIR" "$SECRETS_DIR"
chown -R "$SERVICE_USER:$GROUP_NAME" "$APP_DIR" "$WORK_ROOT"
find "$APP_DIR" "$WORK_ROOT" -type d -exec chmod 2775 {} +
find "$APP_DIR" "$WORK_ROOT" -type f -exec chmod 664 {} + 2>/dev/null || true
chmod 2770 "$SECRETS_DIR"
ok "directories ready under $APP_DIR and $WORK_ROOT"

# ---------- 4. summary ----------
log "4/4 summary"
cat <<EOF
Host prepared:
  service user      : $SERVICE_USER
  shared group      : $GROUP_NAME
  app dir           : $APP_DIR
  workspace root    : $WORK_ROOT
  jobs dir          : $JOBS_DIR
  data dir          : $DATA_DIR
  gitlab secrets    : $SECRETS_DIR

Next steps:
  1. Sync source code into $APP_DIR (clone, or run tools/deploy_autobuild.sh)
  2. Create $APP_DIR/.env from .env.example (mode 600, owner $SERVICE_USER:$GROUP_NAME)
  3. Install the systemd unit:
       sudo cp $APP_DIR/systemd/autobuild.service /etc/systemd/system/
       sudo systemctl daemon-reload
       sudo systemctl enable --now autobuild
EOF

if [[ -n "$INVOKING_USER" && "$INVOKING_USER" != "root" ]]; then
    warn "log out and back in as $INVOKING_USER for $GROUP_NAME membership to take effect"
fi
