#!/usr/bin/env bash
# ==============================================================================
# 模組一排程啟動腳本 (Linux / Ubuntu Crontab)
# 防禦機制: 使用 flock 防止程式撞車重複執行 (Non-blocking file lock)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_FILE="/tmp/crawler_job.lock"
LOG_FILE="${SCRIPT_DIR}/crawler_cron.log"

# 使用 flock 確保只有一個實例在運行 (若撞車則直接跳過並記錄)
exec 200>"${LOCK_FILE}"
flock -n 200 || {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 警告: 前一次爬蟲作業仍在執行中，本次排程自動跳過以防撞車。" >> "${LOG_FILE}"
    exit 1
}

# 進入爬蟲專案目錄
cd "${SCRIPT_DIR}/.." || exit 1

# 啟用 Python 虛擬環境 (若存在)
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# 執行爬蟲主程式並輸出至 log
python3 crawler/crawler.py >> "${LOG_FILE}" 2>&1

# 完成釋放檔案鎖
flock -u 200
