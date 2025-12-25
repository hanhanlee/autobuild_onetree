#!/usr/bin/env bash
set -e

# =================CONFIGURATION=================
# 您的開發目錄 (當前目錄)
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
echo -e "Source: ${SRC_DIR}"
echo -e "Target: ${DEST_DIR}"
echo ""

# 檢查是否為 Root 執行，如果不是，自動加 sudo 重跑自己
if [[ $EUID -ne 0 ]]; then
   echo -e "${YELLOW}此腳本需要管理員權限，正在嘗試自動提權...${NC}"
   # "$0" 代表腳本自己，"$@" 代表傳進來的所有參數
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

    # --- 關鍵修復：這裡就是之前遺漏的 Rsync 步驟 ---
    # --delete: 刪除目標目錄中有，但來源目錄中沒有的檔案 (保持乾淨)
    # --exclude: 排除不必要的檔案
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
    
    # 1. 確保 /opt/autobuild 程式碼權限正確
    chown -R "${TARGET_USER}:${TARGET_GROUP}" "$DEST_DIR"
    chmod -R 755 "$DEST_DIR"

    # 2. 確保 /work/autobuild_workspace 資料硬碟權限正確
    if [ -d "/work/autobuild_workspace" ]; then
        echo "Fixing /work/autobuild_workspace permissions..."
        # 擁有者設為 autobuild:scm-bmc
        chown -R "${TARGET_USER}:${TARGET_GROUP}" "/work/autobuild_workspace"
        
        # 設定目錄為 2775 (SGID + 群組可寫)
        # 這是為了解決您遇到的 "Permission denied" 問題
        find "/work/autobuild_workspace" -type d -exec chmod 2775 {} \;
        
        # 設定檔案為 664 (群組可讀寫)
        find "/work/autobuild_workspace" -type f -exec chmod 664 {} \;
    fi

    echo -e "${GREEN}Permissions fixed.${NC}"
}

restart_service() {
    echo -e "${YELLOW}[3/3] Restarting Systemd service...${NC}"
    
    systemctl daemon-reload
    systemctl restart "$SERVICE_NAME"
    
    # 檢查服務狀態
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
            echo -e "${GREEN}✅ Full deployment completed successfully!${NC}"
            break
            ;;
        2)
            sync_code
            fix_permissions # Sync 後通常需要修權限，比較保險
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