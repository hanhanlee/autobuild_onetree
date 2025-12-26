#!/usr/bin/env bash
set -e

# =================CONFIGURATION=================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(dirname "$SCRIPT_DIR")"
DEST_DIR="/opt/autobuild"
SERVICE_NAME="autobuild"
TARGET_USER="autobuild"
TARGET_GROUP="scm-bmc"
# ===============================================

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Autobuild Deployment Tool ===${NC}"
echo -e "Script Location: ${SCRIPT_DIR}"
echo -e "Project Root (Source): ${SRC_DIR}"
echo -e "Deploy Target: ${DEST_DIR}"
echo ""

# Elevate to root if needed
if [[ $EUID -ne 0 ]]; then
   echo -e "${YELLOW}Need root privileges. Elevating with sudo...${NC}"
   exec sudo "$0" "$@"
fi

require_cmd() {
    local cmd="$1"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo -e "${RED}Missing required command: ${cmd}. Please install it and retry.${NC}"
        exit 1
    fi
}

require_cmd git
require_cmd python3

update_source_code() {
    echo -e "${YELLOW}[0/3] Updating source code (git pull)...${NC}"
    if [ ! -d "$SRC_DIR/.git" ]; then
        echo -e "${RED}No git repository found at $SRC_DIR. Skipping git pull.${NC}"
        return
    fi
    local git_user="${SUDO_USER:-}"
    local git_cmd=("git" "-C" "$SRC_DIR" "pull" "--ff-only")
    if [ -n "$git_user" ]; then
        sudo -u "$git_user" "${git_cmd[@]}"
    else
        "${git_cmd[@]}"
    fi
    echo -e "${GREEN}Source updated.${NC}"
}

show_menu() {
    echo "Please select an action:"
    echo "1) 🚀 Full Deploy (All Steps + Pip Install)"
    echo "2) ⚡ Fast Deploy (Pull + Sync + Fix + Restart)"
    echo "3) 📂 Sync Code Only (No Restart)"
    echo "4) 🧹 Fix Permissions Only (Code & Data)"
    echo "5) 📦 Install/Update Python Requirements"
    echo "6) 🔁 Restart Service Only"
    echo "7) 📜 View Service Logs"
    echo "q) Quit"
    echo -n "Select option: "
}

sync_code() {
    echo -e "${YELLOW}[1/3] Syncing code using rsync...${NC}"
    
    if [ ! -d "$DEST_DIR" ]; then
        mkdir -p "$DEST_DIR"
    fi

    if [ ! -d "$SRC_DIR/app" ]; then
        echo -e "${RED}Error: Cannot find 'app' directory in $SRC_DIR.${NC}"
        echo -e "${RED}Are you running this script from the 'tools' directory?${NC}"
        exit 1
    fi

    rsync -av --delete \
        --exclude 'venv' \
        --exclude 'workspace' \
        --exclude 'data' \
        --exclude '__pycache__' \
        --exclude '.git' \
        --exclude '.idea' \
        --exclude '.vscode' \
        --exclude '*.pyc' \
        --exclude 'deploy_autobuild.sh' \
        "$SRC_DIR/" "$DEST_DIR/"
    
    echo -e "${GREEN}Code sync complete.${NC}"

    echo -e "${YELLOW}Auto-fixing line endings and heredoc delimiters in shell scripts...${NC}"
    find "$DEST_DIR" -type f -name "*.sh" | while read -r shfile; do
        sed -i 's/\r$//' "$shfile"
        sed -i -E 's/^[[:space:]]+(PY|EOF)$/\1/' "$shfile"
    done
    echo -e "${GREEN}Shell script normalization complete.${NC}"
}

check_dependencies() {
    echo -e "${YELLOW}[Deps] Ensuring virtual environment and Python packages...${NC}"
    mkdir -p "$DEST_DIR"
    chown "${TARGET_USER}:${TARGET_GROUP}" "$DEST_DIR"

    if [ ! -d "$DEST_DIR/venv" ]; then
        sudo -u "$TARGET_USER" python3 -m venv "$DEST_DIR/venv"
    fi

    if [ ! -f "$SRC_DIR/requirements.txt" ]; then
        echo -e "${YELLOW}requirements.txt not found in $SRC_DIR; skipping pip install.${NC}"
    else
        local req_src="$SRC_DIR/requirements.txt"
        local req_copy="$DEST_DIR/requirements.txt"
        cp "$req_src" "$req_copy"
        chown "${TARGET_USER}:${TARGET_GROUP}" "$req_copy"
        sudo -u "$TARGET_USER" bash -c "source \"$DEST_DIR/venv/bin/activate\" && pip install --upgrade pip && pip install --upgrade -r \"$req_copy\""
    fi

    chown -R "${TARGET_USER}:${TARGET_GROUP}" "$DEST_DIR/venv"
    echo -e "${GREEN}Python dependencies are up to date.${NC}"
}

fix_permissions() {
    echo -e "${YELLOW}[2/3] Fixing ownership...${NC}"
    
    chown -R "${TARGET_USER}:${TARGET_GROUP}" "$DEST_DIR"
    chmod +x "$DEST_DIR/runner/run_job.sh"

    if [ -d "/work/autobuild_workspace" ]; then
        echo "Fixing /work/autobuild_workspace permissions..."
        chown -R "${TARGET_USER}:${TARGET_GROUP}" "/work/autobuild_workspace"
        find "/work/autobuild_workspace" -type d -exec chmod 2775 {} +
        find "/work/autobuild_workspace" -type f -exec chmod 664 {} +
    fi

    echo -e "${GREEN}Permissions fixed.${NC}"
}

restart_service() {
    echo -e "${YELLOW}[3/3] Restarting Systemd service...${NC}"
    
    systemctl daemon-reload
    systemctl restart "$SERVICE_NAME"
    
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo -e "${GREEN}Service '$SERVICE_NAME' is RUNNING.${NC}"
    else
        echo -e "${RED}Service '$SERVICE_NAME' failed to start! Check logs.${NC}"
        exit 1
    fi
}

# --- Main loop ---

while true; do
    show_menu
    read -r opt
    case $opt in
        1)
            update_source_code
            sync_code
            check_dependencies
            fix_permissions
            restart_service
            echo -e "${GREEN}🚀 Full Deploy completed!${NC}"
            break
            ;;
        2)
            update_source_code
            sync_code
            fix_permissions
            restart_service
            echo -e "${GREEN}⚡ Fast Deploy completed!${NC}"
            break
            ;;
        3)
            sync_code
            fix_permissions
            echo -e "${GREEN}📂 Code synced.${NC}"
            break
            ;;
        4)
            fix_permissions
            echo -e "${GREEN}🧹 Permissions repaired.${NC}"
            break
            ;;
        5)
            check_dependencies
            echo -e "${GREEN}📦 Python requirements installed/updated.${NC}"
            break
            ;;
        6)
            restart_service
            echo -e "${GREEN}🔁 Service restarted.${NC}"
            break
            ;;
        7)
            journalctl -u "$SERVICE_NAME" -n 50 -f
            break
            ;;
        q)
            echo "Bye."
            exit 0
            ;;
        *)
            echo -e "${RED}Invalid option.${NC}"
            ;;
    esac
    echo ""
done
