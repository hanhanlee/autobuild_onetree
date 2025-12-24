#!/usr/bin/env bash
set -euo pipefail

# ==========================================
# 1. 設定與變數 (Configuration)
# ==========================================
REPO_DIR="${REPO_DIR:-/home/nathan/Project/autobuild-onetree/autobuild_onetree}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/autobuild}"
SERVICE_NAME="${SERVICE_NAME:-autobuild}"

OWNER_USER="${OWNER_USER:-autobuild}"
OWNER_GROUP="${OWNER_GROUP:-scm-bmc}"

# 顏色定義 (讓輸出更好讀)
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
INTERACTIVE=1 # 預設開啟互動模式

# ==========================================
# 2. 輔助函式 (Helper Functions)
# ==========================================
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

check_root() {
    # 這裡不需要強制 root，因為後面的指令都用了 sudo
    # 但如果要檢查 sudo 權限可以在這裡加
    true
}

# ==========================================
# 3. 核心功能函式 (Core Actions)
# ==========================================

step_git_update() {
    log_info "Step 1: Checking Git Repository ($REPO_DIR)..."
    cd "$REPO_DIR"

    # 檢查是否有未提交的修改
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
    
    # 顯示當前版本
    local commit_hash=$(git log -1 --format="%h - %s")
    echo -e "   Current Commit: ${YELLOW}${commit_hash}${NC}"
}

step_rsync_deploy() {
    log_info "Step 2: Syncing files to $DEPLOY_DIR..."
    
    # 加入 --dry-run 選項的檢查可以放在這裡擴充
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
    
    # 設定根目錄
    sudo chown "${OWNER_USER}:${OWNER_GROUP}" "$DEPLOY_DIR"

    # 設定子目錄
    for d in app runner static templates systemd nginx; do
        if [[ -e "$DEPLOY_DIR/$d" ]]; then
            sudo chown -R "${OWNER_USER}:${OWNER_GROUP}" "$DEPLOY_DIR/$d"
        fi
    done

    # 設定檔案
    for f in README.md README_New.md pyproject.toml requirements.txt; do
        if [[ -e "$DEPLOY_DIR/$f" ]]; then
            sudo chown "${OWNER_USER}:${OWNER_GROUP}" "$DEPLOY_DIR/$f"
        fi
    done
    
    log_success "Permissions fixed."
}

step_update_dependencies() {
    log_info "Step 4: Checking Python Environment..."

    # 1. 確保系統套件
    if ! dpkg -s python3-venv >/dev/null 2>&1; then
        log_warn "Package 'python3-venv' missing. Installing..."
        sudo apt-get update -qq
        sudo apt-get install -y python3-venv
    fi

    # 2. 檢查 Venv
    if [ ! -x "$DEPLOY_DIR/venv/bin/pip" ]; then
        log_warn "Venv missing or broken. Re-creating..."
        sudo rm -rf "$DEPLOY_DIR/venv"
        sudo -u "$OWNER_USER" python3 -m venv "$DEPLOY_DIR/venv"
    fi

    # 3. 更新套件
    log_info "Updating pip dependencies..."
    sudo -u "$OWNER_USER" "$DEPLOY_DIR/venv/bin/pip" install --upgrade pip > /dev/null
    sudo -u "$OWNER_USER" "$DEPLOY_DIR/venv/bin/pip" install -r "$DEPLOY_DIR/requirements.txt"
    
    log_success "Dependencies updated."
}

step_restart_service() {
    if [[ $SKIP_RESTART -eq 0 ]]; then
        log_info "Step 5: Restarting Service ($SERVICE_NAME)..."
        sudo systemctl restart "$SERVICE_NAME"
        
        # 簡單檢查狀態
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

run_code_sync_only() {
    SKIP_PULL=1 # 預設不強制拉，除非使用者選
    step_rsync_deploy
    step_fix_permissions
    step_restart_service
    log_success "📂 Code Sync & Restart Finished!"
}

run_restart_only() {
    SKIP_RESTART=0
    step_restart_service
    step_show_logs
}

# ==========================================
# 5. 主選單 (Interactive Menu)
# ==========================================
show_menu() {
    clear
    echo -e "${BLUE}=========================================${NC}"
    echo -e "   🤖 AutoBuild Deployment Manager"
    echo -e "${BLUE}=========================================${NC}"
    echo -e "Repo:   $REPO_DIR"
    echo -e "Deploy: $DEPLOY_DIR"
    echo -e "Service: $SERVICE_NAME"
    echo -e "-----------------------------------------"
    echo -e "${GREEN}1)${NC} 🚀 Full Deploy (Git Pull + Sync + Pip + Restart)"
    echo -e "${GREEN}2)${NC} 📂 Quick Deploy (Sync Code + Restart) ${YELLOW}[Skip Pip]${NC}"
    echo -e "${GREEN}3)${NC} 🐍 Update Dependencies Only (Pip Install)"
    echo -e "${GREEN}4)${NC} 🔄 Restart Service Only"
    echo -e "${GREEN}5)${NC} 📜 View Logs (Journalctl -f)"
    echo -e "${GREEN}6)${NC} 🐚 Open Shell in Venv"
    echo -e "${GREEN}q)${NC} Quit"
    echo -e "-----------------------------------------"
    read -rp "Select an option: " choice

    case $choice in
        1)
            run_full_deploy
            ;;
        2)
            # 快速部署：通常需要拉 code，但不跑 pip install
            step_git_update
            step_rsync_deploy
            step_fix_permissions
            step_restart_service
            ;;
        3)
            step_update_dependencies
            ;;
        4)
            run_restart_only
            ;;
        5)
            log_info "Press Ctrl+C to exit logs..."
            sudo journalctl -u "$SERVICE_NAME" -f
            ;;
        6)
            log_info "Entering Venv Shell (Type 'exit' to leave)..."
            sudo -u "$OWNER_USER" bash -c "source $DEPLOY_DIR/venv/bin/activate && bash"
            ;;
        q|Q)
            echo "Bye!"
            exit 0
            ;;
        *)
            log_error "Invalid option."
            sleep 1
            show_menu
            ;;
    esac
}

# ==========================================
# 6. 參數解析與進入點 (Main)
# ==========================================

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

If no options are provided, the Interactive Menu is launched.

Options:
  --no-pull        Skip 'git pull --rebase'
  --no-restart     Skip systemctl restart/status
  --no-log         Skip journalctl tail
  --force-dirty    Deploy even if repo has uncommitted changes
  -h, --help       Show this help
EOF
}

# 如果有傳入參數，則進入「非互動模式 (Batch Mode)」，保持向下相容
if [[ $# -gt 0 ]]; then
    INTERACTIVE=0
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
    
    # 執行完整部署流程
    run_full_deploy
else
    # 沒有參數 -> 進入互動選單
    while true; do
        show_menu
        echo
        read -rp "Press Enter to return to menu..."
        sleep 0.5
    done
fi