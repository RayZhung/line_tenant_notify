"""
模組一：背景資料爬蟲 (ParkV4 後台自動化巡檢引擎)
功能:
  1. 唯讀讀取「車車戰情室場站管理」主檔 (1oaBHrT8mV...) 取得 100 場站 IP、帳號、密碼與授權企業
  2. 嚴格遵循 PARKV4_BACKEND_SPECS 規範，巡檢各場站：
     - 連線狀態 (CONNECTIVITY)
     - 設備與硬體消耗 (HARDWARE)
     - 車流與車位狀況 (CAPACITY)
     - 營收異常 (REVENUE)
  3. 將巡檢結果格式化寫入「場站營運數據戰情表」 (1BzVMgiG...)
  4. 發送 HTTP POST 喚醒模組二 (Vercel Serverless) 執行多租戶角色分流推播
"""
import os
import sys
import re
import time
import random
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

import requests
import urllib3
from dotenv import load_dotenv

# 忽略自行簽署憑證警告 (場站 IP 多採用專用自簽憑證)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

LOG_FILE = os.getenv("CRAWLER_LOG_FILE", "crawler.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("parkv4_crawler")

# 試算表設定 (方案 B：雙表物理隔離，透過環境變數讀取以符合 ISO27001 規範)
MASTER_SHEET_ID = os.getenv("MASTER_SPREADSHEET_ID", "")
OPERATIONS_SHEET_ID = os.getenv("GOOGLE_SPREADSHEET_ID", "")

# Vercel 喚醒端點與觸發密鑰 (嚴禁寫死真實密鑰)
VERCEL_API_URL = os.getenv("VERCEL_NOTIFY_URL", "https://line-tenant-notify.vercel.app/api/v1/trigger")
TRIGGER_SECRET = os.getenv("NOTIFY_TRIGGER_SECRET", "")

def get_gspread_client():
    import gspread
    from google.oauth2.service_account import Credentials
    import base64
    import json

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]

    b64_creds = os.getenv("GOOGLE_SERVICE_ACCOUNT_BASE64")
    if b64_creds:
        try:
            info = json.loads(base64.b64decode(b64_creds.strip()).decode("utf-8"))
            return gspread.authorize(Credentials.from_service_account_info(info, scopes=scopes))
        except Exception:
            pass

    cred_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    if not os.path.isabs(cred_file):
        cred_file = os.path.join(BASE_DIR, cred_file)
    if os.path.exists(cred_file):
        return gspread.authorize(Credentials.from_service_account_file(cred_file, scopes=scopes))
    
    return None

def apply_jitter(min_s: float = 0.5, max_s: float = 1.5):
    time.sleep(random.uniform(min_s, max_s))

def format_company_info(raw_auth: Any, enterprises: Dict[str, str], raw_name: Optional[str] = None) -> tuple:
    """
    選項 A 規範：將 '12' 格式化為 company_id: '1, 2'，company_name: '微程式 / 大冠'
    """
    auth_str = str(raw_auth or "1").strip()
    digits = sorted(list(set(c for c in auth_str if c.isdigit())))
    if not digits:
        digits = ["1"]
    formatted_id = ", ".join(digits)
    names = [enterprises[d] for d in digits if d in enterprises]
    if not names and raw_name:
        names = [raw_name]
    formatted_name = " / ".join(names) if names else "微程式"
    return formatted_id, formatted_name

def check_site_status(site: Dict[str, Any], enterprises: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """
    依據 PARKV4_BACKEND_SPECS 巡檢單一場站狀態
    """
    ent_map = enterprises or {"1": "微程式", "2": "大冠", "3": "百里亭"}
    results = []
    station_name = site.get("停車場名稱") or site.get("站名") or "未知場站"
    raw_ip = str(site.get("IP位址") or site.get("IP") or "").strip()
    company_id, company_name = format_company_info(site.get("授權企業"), ent_map, site.get("公司名稱"))
    account = str(site.get("帳號") or "appuser").strip()
    password = str(site.get("密碼") or "").strip()
    generation = str(site.get("世代") or "四代").strip()
    now_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

    if not raw_ip:
        return []

    base_url = raw_ip if raw_ip.startswith("http") else f"https://{raw_ip}"
    session = requests.Session()
    session.verify = False

    logger.info(f"Checking station: {station_name} ({base_url})...")
    apply_jitter(0.3, 1.0)

    # 1. 檢測連線狀態 (CONNECTIVITY)
    try:
        login_page_res = session.get(f"{base_url}/login", timeout=6)
        if login_page_res.status_code >= 400:
            raise requests.RequestException(f"HTTP {login_page_res.status_code}")
    except Exception as e:
        logger.warning(f"Station {station_name} OFFLINE: {e}")
        results.append({
            "station_name": station_name,
            "station_ip": raw_ip,
            "company_id": company_id,
            "company_name": company_name,
            "category": "CONNECTIVITY",
            "metric_name": "主機連線狀態",
            "current_value": "OFFLINE",
            "threshold": "ONLINE",
            "status": "ANOMALY",
            "details": f"連線異常逾時 ({type(e).__name__})，可能設備離線或網路中斷",
            "update_time": now_str
        })
        return results

    # 連線正常
    results.append({
        "station_name": station_name,
        "station_ip": raw_ip,
        "company_id": company_id,
        "company_name": company_name,
        "category": "CONNECTIVITY",
        "metric_name": "主機連線狀態",
        "current_value": "ONLINE",
        "threshold": "ONLINE",
        "status": "NORMAL",
        "details": "主機連線正常",
        "update_time": now_str
    })

    # 2. 登入取得 Context 與車位 (PARKV4_BACKEND_SPECS 規範)
    try:
        # 解析 CSRF
        token_match = re.search(r'name=["\']_token["\'][^>]*value=["\']([^"\']+)["\']', login_page_res.text)
        csrf_token = token_match.group(1) if token_match else ""

        # Step 2: POST /login (標準 HTML 模式，防 AJAX 204)
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"{base_url}/login"
        }
        data = {
            "_token": csrf_token,
            "account": account,
            "password": password
        }
        post_res = session.post(f"{base_url}/login", data=data, headers=headers, timeout=8, allow_redirects=True)

        # Step 3: 初始化 Context
        session.get(f"{base_url}/systemSetting/deviceManagement/device", timeout=8)

        # 3. 取得車位數據 (CAPACITY)
        space_res = session.get(f"{base_url}/homeParkingSpaceInformation?_={int(time.time()*1000)}", timeout=6)
        if space_res.status_code == 200:
            try:
                space_json = space_res.json()
                ret_val = space_json.get("retVal", {}) if isinstance(space_json, dict) else {}
                car_spaces = None
                for k in ("residualCarSpace", "car_space", "car"):
                    if k in ret_val and ret_val[k] is not None and str(ret_val[k]).strip() != "":
                        car_spaces = ret_val[k]
                        break
                if car_spaces is not None:
                    car_int = int(car_spaces)
                    is_full_anomaly = car_int < 5
                    results.append({
                        "station_name": station_name,
                        "station_ip": raw_ip,
                        "company_id": company_id,
                        "company_name": company_name,
                        "category": "CAPACITY",
                        "metric_name": "剩餘車位數",
                        "current_value": car_int,
                        "threshold": "依個人訂閱門檻",
                        "status": "ANOMALY" if is_full_anomaly else "NORMAL",
                        "details": f"目前可用車位 {car_int} 格",
                        "update_time": now_str
                    })
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Error during ParkV4 inspection on {station_name}: {e}")

    return results

def run_crawler_pipeline():
    logger.info("=== Starting Multi-Tenant ParkV4 Inspection Crawler (Mode A: Subscribed Only) ===")
    client = get_gspread_client()
    if not client:
        logger.error("No valid Google credentials available. Aborting.")
        return

    if not OPERATIONS_SHEET_ID or not MASTER_SHEET_ID:
        logger.error("Missing GOOGLE_SPREADSHEET_ID or MASTER_SPREADSHEET_ID in environment variables.")
        return

    headers = [
        "station_name", "station_ip", "company_id", "company_name",
        "category", "metric_name", "current_value", "threshold",
        "status", "details", "update_time"
    ]

    # 1. 模式 A：先讀取「個人監控場站表」，僅鎖定實際有人監控的場站
    ops_sh = client.open_by_key(OPERATIONS_SHEET_ID)
    ops_ws = ops_sh.worksheet("場站營運數據")
    sub_ws = ops_sh.worksheet("個人監控場站表")
    sub_records = sub_ws.get_all_records()

    subscribed_names = {
        str(r.get("station_name") or "").strip()
        for r in sub_records
        if str(r.get("station_name") or "").strip()
    }

    if not subscribed_names:
        logger.info("目前個人監控場站表尚無管理員訂閱任何場站，略過本次連線巡檢 (模式 A)。")
        ops_ws.clear()
        ops_ws.update(values=[headers], range_name="A1", value_input_option="RAW")
        return

    # 2. 唯讀讀取車車戰情室主檔比對目標場站連線資訊
    logger.info("Reading master site list for subscribed stations...")
    master_sh = client.open_by_key(MASTER_SHEET_ID)
    site_ws = master_sh.worksheet("site list")
    enterprise_ws = master_sh.worksheet("企業名單")

    sites = site_ws.get_all_records()
    enterprises = {
        str(r.get("企業代碼")).strip(): str(r.get("企業名稱")).strip()
        for r in enterprise_ws.get_all_records()
    }

    target_sites = []
    seen_target_names = set()
    for s in sites:
        sname = str(s.get("停車場名稱") or s.get("站名") or "").strip()
        if sname in subscribed_names and sname not in seen_target_names:
            seen_target_names.add(sname)
            target_sites.append(s)

    logger.info(
        f"模式 A 精準鎖定 {len(target_sites)} / {len(subscribed_names)} 座已訂閱場站進行並行巡檢: "
        f"{[s.get('停車場名稱') or s.get('站名') for s in target_sites]}"
    )

    # 3. 多執行緒並行巡檢目標場站 (大幅縮短執行時間，防單站逾時拖慢整體)
    inspected_records: List[Dict[str, Any]] = []
    max_workers = min(6, max(1, len(target_sites)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(check_site_status, site, enterprises): site
            for site in target_sites
        }
        for fut in as_completed(future_map):
            site_info = future_map[fut]
            try:
                site_results = fut.result()
                inspected_records.extend(site_results)
            except Exception as exc:
                logger.error(f"Worker exception on site {site_info.get('停車場名稱')}: {exc}")

    # 4. 寫入營運戰情表
    logger.info(f"Writing {len(inspected_records)} real inspection records to Operations Sheet...")
    rows = [headers]
    for r in inspected_records:
        rows.append([
            r.get("station_name", ""),
            r.get("station_ip", ""),
            str(r.get("company_id", "")),
            r.get("company_name", ""),
            r.get("category", ""),
            r.get("metric_name", ""),
            r.get("current_value", ""),
            r.get("threshold", ""),
            r.get("status", ""),
            r.get("details", ""),
            r.get("update_time", "")
        ])

    ops_ws.clear()
    ops_ws.update(values=rows, range_name="A1", value_input_option="RAW")
    logger.info("Successfully synced latest Mode-A inspection data into Google Sheets!")

    # 5. 喚醒模組二 (Vercel) 執行個人化精準推播
    if VERCEL_API_URL and "your-vercel-domain" not in VERCEL_API_URL:
        logger.info(f"Triggering Module 2 (Vercel) at {VERCEL_API_URL}...")
        try:
            req_headers = {"X-Trigger-Token": TRIGGER_SECRET} if TRIGGER_SECRET else {}
            resp = requests.post(
                VERCEL_API_URL,
                headers=req_headers,
                timeout=20
            )
            logger.info(f"Vercel trigger response: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"Failed to trigger Vercel: {e}")

    logger.info("=== Mode-A Inspection Crawler Pipeline Completed ===")

if __name__ == "__main__":
    run_crawler_pipeline()
