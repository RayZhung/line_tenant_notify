"""
模組一：背景資料爬蟲 (部署於 Ubuntu 主機)
功能:
  1. 隨機延遲防禦機制 (防止被外部網站偵測封鎖)
  2. 抓取外部營運數據並清洗
  3. 透過 Google Sheets API 將最新數值寫入試算表
  4. 最終發送 HTTP POST 喚醒模組二 (Vercel Serverless) 執行多租戶推播
"""
import os
import sys
import time
import random
import logging
from datetime import datetime
import requests
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

# 日誌設定 (同時輸出至終端機與 crawler.log)
LOG_FILE = os.getenv("CRAWLER_LOG_FILE", "crawler.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("crawler_job")

# Vercel 模組二呼叫端點
VERCEL_API_URL = os.getenv("VERCEL_NOTIFY_URL", "https://your-vercel-domain.vercel.app/api/v1/trigger")
TRIGGER_SECRET = os.getenv("NOTIFY_TRIGGER_SECRET", "")

def apply_anti_ban_jitter(min_seconds: float = 1.0, max_seconds: float = 5.0):
    """
    隨機延遲機制，防止週期性固定頻率請求遭目標網站 WAF/IP 封鎖
    """
    sleep_time = random.uniform(min_seconds, max_seconds)
    logger.info(f"Applying anti-ban delay: sleeping for {sleep_time:.2f} seconds...")
    time.sleep(sleep_time)

def scrape_external_data() -> list:
    """
    抓取外部營運數據 (此處提供範例結構，可依實際抓取站點替換 HTML/API 邏輯)
    """
    logger.info("Starting data scraping from target external site...")
    apply_anti_ban_jitter(1.5, 4.0)

    # 模擬清洗後的最新營運數據
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scraped_records = [
        {
            "station_id": "STN_001",
            "station_name": "台北信義站",
            "tenant_id": "TENANT_A",
            "metric_name": "發票剩餘數量",
            "current_value": 8.0,
            "threshold": 20.0,
            "update_time": now_str
        },
        {
            "station_id": "STN_002",
            "station_name": "台中一中站",
            "tenant_id": "TENANT_B",
            "metric_name": "發票剩餘數量",
            "current_value": 45.0,
            "threshold": 20.0,
            "update_time": now_str
        },
        {
            "station_id": "STN_003",
            "station_name": "高雄左營站",
            "tenant_id": "TENANT_A",
            "metric_name": "發票剩餘數量",
            "current_value": 5.0,
            "threshold": 15.0,
            "update_time": now_str
        }
    ]
    logger.info(f"Successfully scraped and cleaned {len(scraped_records)} station records.")
    return scraped_records

def write_to_google_sheet(records: list):
    """
    將最新數值覆蓋更新至 Google Sheets
    """
    spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID")
    sheet_name = os.getenv("SHEET_NAME_DATA", "場站營運數據")

    if not spreadsheet_id:
        logger.warning("GOOGLE_SPREADSHEET_ID not set. Skipping real Google Sheets update.")
        return

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        cred_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
        if not os.path.exists(cred_file):
            logger.warning(f"Credentials file {cred_file} not found. Skipping Google Sheets update.")
            return

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(cred_file, scopes=scopes)
        client = gspread.authorize(creds)
        sh = client.open_by_key(spreadsheet_id)
        worksheet = sh.worksheet(sheet_name)

        # 準備寫入標頭與資料
        headers = ["station_id", "station_name", "tenant_id", "metric_name", "current_value", "threshold", "update_time"]
        rows = [headers]
        for r in records:
            rows.append([
                r.get("station_id", ""),
                r.get("station_name", ""),
                r.get("tenant_id", ""),
                r.get("metric_name", ""),
                r.get("current_value", 0),
                r.get("threshold", 0),
                r.get("update_time", "")
            ])

        worksheet.clear()
        worksheet.update("A1", rows)
        logger.info(f"Successfully updated Google Sheet '{sheet_name}' with {len(records)} rows.")
    except Exception as e:
        logger.error(f"Failed to update Google Sheets: {e}", exc_info=True)

def trigger_module_two():
    """
    交接觸發：發送 HTTP POST 請求喚醒 Vercel 模組二執行條件判斷與推播
    """
    if not VERCEL_API_URL or "your-vercel-domain" in VERCEL_API_URL:
        logger.warning(f"VERCEL_NOTIFY_URL is not properly configured ({VERCEL_API_URL}). Skipping trigger.")
        return

    logger.info(f"Triggering Module 2 (Vercel) at: {VERCEL_API_URL}...")
    headers = {
        "Content-Type": "application/json",
        "X-Trigger-Token": TRIGGER_SECRET
    }

    try:
        response = requests.post(VERCEL_API_URL, headers=headers, timeout=15)
        logger.info(f"Module 2 response status: {response.status_code}, body: {response.text}")
    except Exception as e:
        logger.error(f"Failed to trigger Module 2 on Vercel: {e}")

def main():
    logger.info("=== Starting Crawler Job Execution ===")
    try:
        # 1. 抓取與清洗資料
        data = scrape_external_data()
        # 2. 寫入 Google Sheets
        write_to_google_sheet(data)
        # 3. 喚醒模組二
        trigger_module_two()
        logger.info("=== Crawler Job Completed Successfully ===")
    except Exception as e:
        logger.critical(f"Unhandled error in crawler job: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
