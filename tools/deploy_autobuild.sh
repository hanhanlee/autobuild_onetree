#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/nathan/Project/autobuild-onetree/autobuild_onetree}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/autobuild}"
SERVICE_NAME="${SERVICE_NAME:-autobuild}"

OWNER_USER="${OWNER_USER:-autobuild}"
OWNER_GROUP="${OWNER_GROUP:-scm-bmc}"

NO_PULL=0
NO_RESTART=0
NO_LOG=0
FORCE_DIRTY=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --no-pull        Skip 'git pull --rebase'
  --no-restart     Skip systemctl restart/status
  --no-log         Skip journalctl tail
  --force-dirty    Deploy even if repo has uncommitted changes
  -h, --help       Show this help

Env overrides:
  REPO_DIR, DEPLOY_DIR, SERVICE_NAME, OWNER_USER, OWNER_GROUP
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-pull) NO_PULL=1; shift ;;
    --no-restart) NO_RESTART=1; shift ;;
    --no-log) NO_LOG=1; shift ;;
    --force-dirty) FORCE_DIRTY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 2 ;;
  esac
done

echo "[1/5] Update repo: $REPO_DIR"
cd "$REPO_DIR"

git status

if [[ $FORCE_DIRTY -eq 0 ]]; then
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: repo has uncommitted changes. Commit/stash first, or use --force-dirty."
    exit 1
  fi
fi

if [[ $NO_PULL -eq 0 ]]; then
  git pull --rebase
fi

git log -1 --oneline

echo
echo "[2/5] Rsync deploy -> $DEPLOY_DIR (exclude .git/ venv/ workspace/)"
sudo rsync -a --delete \
  --exclude '.git/' \
  --exclude 'venv/' \
  --exclude 'workspace/' \
  . "$DEPLOY_DIR/"

echo
echo "[3/5] Fix ownership (exclude venv/ and workspace/)"
# chown top-level deploy dir itself
sudo chown "${OWNER_USER}:${OWNER_GROUP}" "$DEPLOY_DIR"

# chown only deployed paths (do not touch venv/ and workspace/)
for d in app runner static templates systemd nginx; do
  if [[ -e "$DEPLOY_DIR/$d" ]]; then
    sudo chown -R "${OWNER_USER}:${OWNER_GROUP}" "$DEPLOY_DIR/$d"
  fi
done

# chown common top-level files if present
for f in README.md README_New.md pyproject.toml requirements.txt; do
  if [[ -e "$DEPLOY_DIR/$f" ]]; then
    sudo chown "${OWNER_USER}:${OWNER_GROUP}" "$DEPLOY_DIR/$f"
  fi
done

echo
echo "[3.5/5] Check and Update Python Dependencies"

# 1. 確保系統有安裝 venv 工具 (Debian/Ubuntu 必備)
if ! dpkg -s python3-venv >/dev/null 2>&1; then
    echo "  > System package 'python3-venv' missing. Installing..."
    sudo apt-get update -qq
    sudo apt-get install -y python3-venv
fi

# 2. 檢查 pip 是否可執行？如果不行的話 (或不存在)，就刪掉重來
if [ ! -x "$DEPLOY_DIR/venv/bin/pip" ]; then
    echo "  > venv is missing or broken. Re-creating in $DEPLOY_DIR..."

    # 刪除舊的 (如果存在)
    sudo rm -rf "$DEPLOY_DIR/venv"

    # 以 autobuild 身分建立全新的 venv
    sudo -u "$OWNER_USER" python3 -m venv "$DEPLOY_DIR/venv"

    if [ ! -x "$DEPLOY_DIR/venv/bin/pip" ]; then
        echo "ERROR: Failed to create venv with pip. Please check 'python3-venv' installation."
        exit 1
    fi
    echo "  > venv created successfully."
fi

# 3. 確保 pip 是最新的
echo "  > Upgrading pip..."
sudo -u "$OWNER_USER" "$DEPLOY_DIR/venv/bin/pip" install --upgrade pip

# 4. 安裝 requirements.txt
echo "  > Installing dependencies..."
sudo -u "$OWNER_USER" "$DEPLOY_DIR/venv/bin/pip" install -r "$DEPLOY_DIR/requirements.txt"

echo
echo "[4/5] Restart service: $SERVICE_NAME"
if [[ $NO_RESTART -eq 0 ]]; then
  sudo systemctl restart "$SERVICE_NAME"
  sudo systemctl status "$SERVICE_NAME" --no-pager
else
  echo "Skipped restart (--no-restart)."
fi

echo
echo "[5/5] Tail journal (last 120 lines)"
if [[ $NO_LOG -eq 0 ]]; then
  sudo journalctl -u "$SERVICE_NAME" -n 120 --no-pager
else
  echo "Skipped journal tail (--no-log)."
fi

echo
echo "Done."

