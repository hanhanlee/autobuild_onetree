#!/usr/bin/env bash
set -e

# =================CONFIGURATION=================
# [關鍵修正] 取得腳本所在目錄 (tools)，然後往上一層 (..) 找到專案根目錄
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(dirname "$SCRIPT_DIR")"

# 伺服器部署目標目錄
DEST_DIR="/opt/autobuild"
# 服務名稱
SERVICE_NAME="autobuild"
# 使用者與群組
TARGET_USER="autobuild"
TARGET_GROUP="scm-bmc"
# ===============================================

# 顏色定義
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Autobuild Deployment Tool ===${NC}"
echo -e "Script Location: ${SCRIPT_DIR}"
echo -e "Project Root (Source): ${SRC_DIR}"
echo -e "Deploy Target: ${DEST_DIR}"
echo ""

# 檢查是否為 Root 執行，如果不是，自動加 sudo 重跑自己
if [[ $EUID -ne 0 ]]; then
   echo -e "${YELLOW}Need root privileges. Elevating with sudo...${NC}"
   exec sudo "$0" "$@"
fi

show_menu() {
    echo "Please select an action:"
    echo "1) 🚀 Full Deploy (Sync Code + Fix Permissions + Restart Service)"
    echo "2) 📂 Sync Code Only (No Restart)"
    echo "3) 🔧 Fix Permissions Only (Code & Data)"
    echo "4) 🔄 Restart Service Only"
    echo "5) 📜 View Service Logs"
    echo "q) Quit"
    echo -n "Select option: "
}

sync_code() {
    echo -e "${YELLOW}[1/3] Syncing code using rsync...${NC}"
    
    # 確保目標目錄存在
    if [ ! -d "$DEST_DIR" ]; then
        mkdir -p "$DEST_DIR"
    fi

    # 再次檢查路徑是否正確 (避免同步錯誤)
    if [ ! -d "$SRC_DIR/app" ]; then
        echo -e "${RED}Error: Cannot find 'app' directory in $SRC_DIR.${NC}"
        echo -e "${RED}Are you running this script from the 'tools' directory?${NC}"
        exit 1
    fi

    # --- Rsync 同步 ---
    # --delete: 確保伺服器跟開發環境完全一致
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
}

fix_permissions() {
    echo -e "${YELLOW}[2/3] Fixing ownership and permissions...${NC}"
    
    # 1. 確保程式碼權限
    chown -R "${TARGET_USER}:${TARGET_GROUP}" "$DEST_DIR"
    # 設定目錄權限 755, 檔案 644
    find "$DEST_DIR" -type d -exec chmod 755 {} \;
    find "$DEST_DIR" -type f -exec chmod 644 {} \;
    # 特別確保執行腳本有 x 權限
    chmod +x "$DEST_DIR/runner/run_job.sh"

    # 2. 確保資料硬碟權限
    if [ -d "/work/autobuild_workspace" ]; then
        echo "Fixing /work/autobuild_workspace permissions..."
        chown -R "${TARGET_USER}:${TARGET_GROUP}" "/work/autobuild_workspace"
        # 關鍵：設定 SGID 與群組可寫
        find "/work/autobuild_workspace" -type d -exec chmod 2775 {} \;
        find "/work/autobuild_workspace" -type f -exec chmod 664 {} \;
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

# --- 主程式 ---

while true; do
    show_menu
    read -r opt
    case $opt in
        1)
            sync_code
            fix_permissions
            restart_service
            echo -e "${GREEN}✅ Full deployment completed!${NC}"
            break
            ;;
        2)
            sync_code
            fix_permissions
            echo -e "${GREEN}✅ Code synced.${NC}"
            break
            ;;
        3)
            fix_permissions
            echo -e "${GREEN}✅ Permissions repaired.${NC}"
            break
            ;;
        4)
            restart_service
            echo -e "${GREEN}✅ Service restarted.${NC}"
            break
            ;;
        5)
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