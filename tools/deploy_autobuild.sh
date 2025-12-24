#!/usr/bin/env bash
set -euo pipefail

# ==========================================
# 1. 設定與變數 (Configuration)
# ==========================================
REPO_DIR="${REPO_DIR:-/home/nathan/Project/autobuild-onetree/autobuild_onetree}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/autobuild}"
# [新增] 資料目錄設定 (對應您的 /work)
DATA_DIR="${DATA_DIR:-/work/autobuild_workspace}" 
SERVICE_NAME="${SERVICE_NAME:-autobuild}"

OWNER_USER="${OWNER_USER:-autobuild}"
OWNER_GROUP="${OWNER_GROUP:-scm-bmc}"

# 顏色定義
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 預設行為標記
SKIP_PULL=0
SKIP_RESTART=0
SKIP_LOGS=0
FORCE_DIRTY=0
INTERACTIVE=1

# ==========================================
# 2. 輔助函式 (Helper Functions)
# ==========================================
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ==========================================
# 3. 核心功能函式 (Core Actions)
# ==========================================

step_git_update() {
    log_info "Step 1: Checking Git Repository ($REPO_DIR)..."
    cd "$REPO_DIR"

    if [[ $FORCE_DIRTY -eq 0 ]]; then
        if ! git diff --quiet || ! git diff --cached --quiet; then
            log_error "Repo has uncommitted changes! Commit/stash first or use --force-dirty."
            git status --short
            exit 1
        fi
    fi

    if [[ $SKIP_PULL -eq 0 ]]; then
        log_info "Pulling latest changes..."
        git pull --rebase
        log_success "Git pull complete."
    else
        log_warn "Skipping Git pull."
    fi
    
    local commit_hash=$(git log -1 --format="%h - %s")
    echo -e "   Current Commit: ${YELLOW}${commit_hash}${NC}"
}

step_rsync_deploy() {
    log_info "Step 2: Syncing files to $DEPLOY_DIR..."
    
    # 這裡排除了 workspace 和 _work，確保不會覆蓋資料
    sudo rsync -a --delete \
      --exclude '.git/' \
      --exclude 'venv/' \
      --exclude 'workspace/' \
      --exclude '_work/' \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      . "$DEPLOY_DIR/"
      
    log_success "Rsync complete."
}

step_fix_permissions() {
    log_info "Step 3: Fixing permissions..."
    
    # 3.1 修復程式碼目錄 (/opt/autobuild)
    log_info " -> Fixing Code Directory ($DEPLOY_DIR)..."
    sudo chown "${OWNER_USER}:${OWNER_GROUP}" "$DEPLOY_DIR"

    for d in app runner static templates systemd nginx; do
        if [[ -e "$DEPLOY_DIR/$d" ]]; then
            sudo chown -R "${OWNER_USER}:${OWNER_GROUP}" "$DEPLOY_DIR/$d"
        fi
    done

    for f in README.md pyproject.toml requirements.txt; do
        if [[ -e "$DEPLOY_DIR/$f" ]]; then
            sudo chown "${OWNER_USER}:${OWNER_GROUP}" "$DEPLOY_DIR/$f"
        fi
    done

    # 3.2 [新增] 修復資料目錄 (/work/autobuild_workspace)
    # 這是為了確保 autobuild 使用者可以讀寫，且同群組成員(如 nathan)也能操作
    if [[ -d "$DATA_DIR" ]]; then
        log_info " -> Fixing Data Directory ($DATA_DIR)..."
        sudo chown -R "${OWNER_USER}:${OWNER_GROUP}" "$DATA_DIR"
        # 設定 775: 擁有者(rwx) 群組(rwx) 其他人(rx)
        sudo chmod -R 775 "$DATA_DIR"
    else
        log_warn "Data dir $DATA_DIR not found. Skipping data permission fix."
    fi
    
    log_success "All Permissions fixed (Code & Data)."
}

step_update_dependencies() {
    log_info "Step 4: Checking Python Environment..."

    if ! dpkg -s python3-venv >/dev/null 2>&1; then
        log_warn "Package 'python3-venv' missing. Installing..."
        sudo apt-get update -qq
        sudo apt-get install -y python3-venv
    fi

    if [ ! -x "$DEPLOY_DIR/venv/bin/pip" ]; then
        log_warn "Venv missing or broken. Re-creating..."
        sudo rm -rf "$DEPLOY_DIR/venv"
        sudo -u "$OWNER_USER" python3 -m venv "$DEPLOY_DIR/venv"
    fi

    log_info "Updating pip dependencies..."
    sudo -u "$OWNER_USER" "$DEPLOY_DIR/venv/bin/pip" install --upgrade pip > /dev/null
    sudo -u "$OWNER_USER" "$DEPLOY_DIR/venv/bin/pip" install -r "$DEPLOY_DIR/requirements.txt"
    
    log_success "Dependencies updated."
}

step_restart_service() {
    if [[ $SKIP_RESTART -eq 0 ]]; then
        log_info "Step 5: Restarting Service ($SERVICE_NAME)..."
        
        # 強制重載 Systemd 設定，確保吃到最新的 Environment 變數
        # 這步驟很重要，確保不會發生設定檔改了但服務還吃舊設定的情況
        log_info "Reloading systemd daemon..."
        sudo systemctl daemon-reload
        
        # 這裡雖然用 sudo，但 Systemd 會根據 .service 檔裡的 User=autobuild 來啟動
        # 所以絕對是安全的，不會變成 root 執行
        log_info "Restarting $SERVICE_NAME..."
        sudo systemctl restart "$SERVICE_NAME"
        
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            log_success "Service is ACTIVE."
        else
            log_error "Service failed to start!"
            sudo systemctl status "$SERVICE_NAME" --no-pager
            exit 1
        fi
    else
        log_warn "Skipping service restart."
    fi
}

step_show_logs() {
    if [[ $SKIP_LOGS -eq 0 ]]; then
        echo
        log_info "Tail of Journal Logs:"
        echo "---------------------------------------------------"
        sudo journalctl -u "$SERVICE_NAME" -n 20 --no-pager
        echo "---------------------------------------------------"
    fi
}

# ==========================================
# 4. 流程組合 (Workflows)
# ==========================================

run_full_deploy() {
    step_git_update
    step_rsync_deploy
    step_fix_permissions
    step_update_dependencies
    step_restart_service
    step_show_logs
    log_success "🚀 Full Deployment Finished!"
}

# ==========================================
# 5. 主選單 (Interactive Menu)
# ==========================================
show_menu_and_act() {
    clear
    echo -e "${BLUE}=========================================${NC}"
    echo -e "   🤖 AutoBuild Deployment Manager"
    echo -e "${BLUE}=========================================${NC}"
    echo -e "Repo:   $REPO_DIR"
    echo -e "Deploy: $DEPLOY_DIR"
    echo -e "Data:   $DATA_DIR"
    echo -e "Service: $SERVICE_NAME"
    echo -e "-----------------------------------------"
    echo -e "${GREEN}1)${NC} 🚀 Full Deploy (Git Pull + Pip + Restart)"
    echo -e "${GREEN}2)${NC} 📂 Quick Deploy (Sync Code + Restart)"
    echo -e "${GREEN}3)${NC} 🐍 Update Dependencies Only"
    echo -e "${GREEN}4)${NC} 🔄 Restart Service Only (Daemon Reload)"
    echo -e "${GREEN}5)${NC} 📜 View Logs"
    echo -e "${GREEN}6)${NC} 🔧 Repair Permissions (Code & Data)"
    echo -e "${GREEN}q)${NC} Quit"
    echo -e "-----------------------------------------"
    read -rp "Select an option: " choice

    case $choice in
        1)
            run_full_deploy
            ;;
        2)
            # Quick Deploy: Git update -> Rsync -> Permissions -> Restart
            step_git_update
            step_rsync_deploy
            step_fix_permissions # 這一步會修復所有權限
            step_restart_service
            log_success "📂 Quick Deployment Finished!"
            ;;
        3)
            step_update_dependencies
            ;;
        4)
            # Restart Only: 重載設定 -> 重啟 -> 看 Log
            SKIP_RESTART=0
            SKIP_LOGS=0
            step_restart_service
            step_show_logs
            ;;
        5)
            log_info "Press Ctrl+C to exit logs..."
            sudo journalctl -u "$SERVICE_NAME" -f
            ;;
        6)
            # 單獨修復權限 (Code + Data)
            step_fix_permissions
            log_success "Permissions repaired successfully!"
            ;;
        q|Q)
            echo "Cancelled."
            exit 0
            ;;
        *)
            log_error "Invalid option."
            exit 1
            ;;
    esac
}

# ==========================================
# 6. 參數解析與進入點 (Main)
# ==========================================

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --no-pull        Skip 'git pull'
  --no-restart     Skip systemctl restart
  --no-log         Skip journalctl tail
  --force-dirty    Deploy even if uncommitted changes
  -h, --help       Show help
EOF
}

if [[ $# -gt 0 ]]; then
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --no-pull) SKIP_PULL=1; shift ;;
        --no-restart) SKIP_RESTART=1; shift ;;
        --no-log) SKIP_LOGS=1; shift ;;
        --force-dirty) FORCE_DIRTY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown arg: $1"; usage; exit 2 ;;
      esac
    done
    run_full_deploy
else
    show_menu_and_act
fi