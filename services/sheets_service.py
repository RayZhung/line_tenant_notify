"""
Google Sheets API 資料服務模組
遵循 ISO27001 資安規範：透過 Base64 環境變數或安全憑證檔案載入 Service Account 金鑰
"""
import os
import json
import base64
import logging
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("sheets_service")

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False
    logger.warning("gspread or google-auth not installed, running in mock/fallback mode.")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly"
]

def get_google_credentials() -> Optional[Any]:
    """
    從環境變數載入 Google Service Account 憑證
    支援:
      1. GOOGLE_SERVICE_ACCOUNT_BASE64 (Vercel Serverless 雲端部署最佳實踐)
      2. GOOGLE_SERVICE_ACCOUNT_FILE (地端 / Ubuntu 主機金鑰檔案)
    """
    if not GSPREAD_AVAILABLE:
        return None

    # 1. 優先檢查 Base64 編碼的 JSON
    b64_creds = os.getenv("GOOGLE_SERVICE_ACCOUNT_BASE64")
    if b64_creds:
        try:
            json_text = base64.b64decode(b64_creds.strip()).decode("utf-8")
            cred_dict = json.loads(json_text)
            return Credentials.from_service_account_info(cred_dict, scopes=SCOPES)
        except Exception as e:
            logger.error(f"Failed to parse GOOGLE_SERVICE_ACCOUNT_BASE64: {e}")

    # 2. 檢查本機檔案路徑
    cred_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    if os.path.exists(cred_file):
        try:
            return Credentials.from_service_account_file(cred_file, scopes=SCOPES)
        except Exception as e:
            logger.error(f"Failed to load credentials from file {cred_file}: {e}")

    logger.warning("No valid Google Service Account credentials found.")
    return None

class SheetsService:
    def __init__(self):
        self.spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID", "")
        self.sheet_name_data = os.getenv("SHEET_NAME_DATA", "場站營運數據")
        self.sheet_name_tenants = os.getenv("SHEET_NAME_TENANTS", "多租戶權限表")
        self.sheet_name_settings = os.getenv("SHEET_NAME_SETTINGS", "場站警報設定")
        self.sheet_name_cooldown = os.getenv("SHEET_NAME_COOLDOWN", "最後警報紀錄")
        self.client = None
        self._init_client()

    def _init_client(self):
        creds = get_google_credentials()
        if creds and GSPREAD_AVAILABLE:
            try:
                self.client = gspread.authorize(creds)
                logger.info("Successfully authorized with Google Sheets API.")
            except Exception as e:
                logger.error(f"gspread authorization failed: {e}")

    def fetch_operations_data(self) -> List[Dict[str, Any]]:
        """
        讀取最新場站營運數據
        回傳欄位結構範例:
        [
          {"station_id": "STN_001", "station_name": "台北站", "metric_value": 4.5, "metric_name": "庫存水位", "threshold": 10.0, "status": "active"},
          ...
        ]
        """
        if not self.client or not self.spreadsheet_id:
            logger.warning("Sheets client not configured, using fallback test data.")
            return [
                {
                    "station_id": "STN_001",
                    "station_name": "台北信義站",
                    "tenant_id": "TENANT_A",
                    "metric_name": "發票剩餘數量",
                    "current_value": 8.0,
                    "threshold": 20.0,
                    "update_time": "2026-10-22 15:00:00"
                },
                {
                    "station_id": "STN_002",
                    "station_name": "台中一中站",
                    "tenant_id": "TENANT_B",
                    "metric_name": "發票剩餘數量",
                    "current_value": 55.0,
                    "threshold": 20.0,
                    "update_time": "2026-10-22 15:00:00"
                }
            ]

        try:
            sh = self.client.open_by_key(self.spreadsheet_id)
            worksheet = sh.worksheet(self.sheet_name_data)
            records = worksheet.get_all_records()
            clean_records = [{str(k).strip(): v for k, v in r.items()} for r in records]
            return clean_records
        except Exception as e:
            logger.error(f"Error reading operations worksheet: {e}")
            return []

    def fetch_tenant_permissions(self) -> List[Dict[str, Any]]:
        """
        讀取多租戶權限表
        回傳結構:
        [
          {
            "tenant_id": "TENANT_A",
            "tenant_name": "安維智能營運部",
            "authorized_line_user_ids": "U1234567890abcdef,U0987654321fedcba",
            "notify_enabled": True
          },
          ...
        ]
        """
        if not self.client or not self.spreadsheet_id:
            logger.warning("Sheets client not configured, using fallback tenant permission data.")
            return [
                {
                    "tenant_id": "TENANT_A",
                    "tenant_name": "優質車行股份有限公司",
                    "authorized_line_user_ids": "U42607efe1d183ad9503f72a1f0b0553d",
                    "notify_enabled": True
                },
                {
                    "tenant_id": "TENANT_B",
                    "tenant_name": "智慧洗車聯盟",
                    "authorized_line_user_ids": "U_DEMO_TENANT_B",
                    "notify_enabled": True
                }
            ]

        try:
            sh = self.client.open_by_key(self.spreadsheet_id)
            worksheet = sh.worksheet(self.sheet_name_tenants)
            records = worksheet.get_all_records()
            clean_records = [{str(k).strip(): v for k, v in r.items()} for r in records]
            return clean_records
        except Exception as e:
            logger.error(f"Error reading tenant permissions worksheet: {e}")
            return []

    def fetch_alert_settings(self, company_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        讀取場站警報門檻與冷卻設定
        可依 company_id 進行租戶資料隔離篩選
        """
        if not self.client or not self.spreadsheet_id:
            return []
        try:
            sh = self.client.open_by_key(self.spreadsheet_id)
            ws = sh.worksheet(self.sheet_name_settings)
            records = ws.get_all_records()
            clean_records = [{str(k).strip(): v for k, v in r.items()} for r in records]

            if not company_id or str(company_id).upper() == "ALL":
                return clean_records

            cid_str = str(company_id).strip()
            filtered = []
            for r in clean_records:
                rc_id = str(r.get("company_id", "")).strip()
                if cid_str == rc_id or cid_str in rc_id:
                    filtered.append(r)
            return filtered
        except Exception as e:
            logger.error(f"Error reading alert settings worksheet: {e}")
            return []

    def update_station_space_threshold(
        self,
        company_id: str,
        station_query: str,
        lower: str,
        upper: str
    ) -> Dict[str, Any]:
        """
        更新特定場站的可用車位上限與下限
        """
        if not self.client or not self.spreadsheet_id:
            return {"success": False, "error": "Google Sheets client not configured"}
        try:
            from datetime import datetime
            sh = self.client.open_by_key(self.spreadsheet_id)
            ws = sh.worksheet(self.sheet_name_settings)
            all_values = ws.get_all_values()
            if not all_values or len(all_values) < 2:
                return {"success": False, "error": "Settings worksheet is empty"}

            header = [h.strip() for h in all_values[0]]
            try:
                col_cid = header.index("company_id")
                col_stn = header.index("station_name")
                col_low = header.index("space_lower")
                col_up = header.index("space_upper")
                col_upd = header.index("updated_at")
            except ValueError as ve:
                return {"success": False, "error": f"Header format error: {ve}"}

            cid_str = str(company_id).strip()
            target_row_idx = None
            matched_station_name = ""

            for idx, row in enumerate(all_values[1:], start=2):
                r_cid = str(row[col_cid] if col_cid < len(row) else "").strip()
                r_stn = str(row[col_stn] if col_stn < len(row) else "").strip()

                # 租戶授權比對
                if cid_str != "ALL" and (cid_str != r_cid and cid_str not in r_cid):
                    continue

                # 場站名稱比對 (精準或包含)
                if station_query.lower() in r_stn.lower():
                    target_row_idx = idx
                    matched_station_name = r_stn
                    break

            if not target_row_idx:
                return {
                    "success": False,
                    "error": f"找不到名稱包含「{station_query}」的所屬場站，請確認場站名稱是否正確。"
                }

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 更新 lower (col_low + 1), upper (col_up + 1), updated_at (col_upd + 1)
            ws.update_cell(target_row_idx, col_low + 1, str(lower))
            ws.update_cell(target_row_idx, col_up + 1, str(upper))
            ws.update_cell(target_row_idx, col_upd + 1, now_str)

            logger.info(f"Updated space threshold for {matched_station_name}: lower={lower}, upper={upper}")
            return {
                "success": True,
                "station_name": matched_station_name,
                "lower": str(lower),
                "upper": str(upper),
                "updated_at": now_str
            }
        except Exception as e:
            logger.error(f"Error updating space threshold: {e}")
            return {"success": False, "error": str(e)}

    def update_station_cooldown(
        self,
        company_id: str,
        alert_type: str,
        hours: int,
        station_query: str = "全部"
    ) -> Dict[str, Any]:
        """
        更新場站冷卻時間 (支援單場或全部場站批次更新)
        alert_type: '車位' 或 '斷線'
        hours: 6, 12, 18, 24, 36
        """
        if hours not in [6, 12, 18, 24, 36]:
            return {"success": False, "error": "冷卻時數僅支援 6、12、18、24 或 36 小時。"}

        if not self.client or not self.spreadsheet_id:
            return {"success": False, "error": "Google Sheets client not configured"}

        try:
            from datetime import datetime
            sh = self.client.open_by_key(self.spreadsheet_id)
            ws = sh.worksheet(self.sheet_name_settings)
            all_values = ws.get_all_values()
            if not all_values or len(all_values) < 2:
                return {"success": False, "error": "Settings worksheet is empty"}

            header = [h.strip() for h in all_values[0]]
            col_cid = header.index("company_id")
            col_stn = header.index("station_name")
            target_col_name = "space_cooldown" if "車位" in alert_type else "offline_cooldown"
            col_cool = header.index(target_col_name)
            col_upd = header.index("updated_at")

            cid_str = str(company_id).strip()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            updated_stations = []
            is_all = (station_query in ["全部", "ALL", "*", "所有"])

            for idx, row in enumerate(all_values[1:], start=2):
                r_cid = str(row[col_cid] if col_cid < len(row) else "").strip()
                r_stn = str(row[col_stn] if col_stn < len(row) else "").strip()

                if cid_str != "ALL" and (cid_str != r_cid and cid_str not in r_cid):
                    continue

                if is_all or (station_query.lower() in r_stn.lower()):
                    ws.update_cell(idx, col_cool + 1, str(hours))
                    ws.update_cell(idx, col_upd + 1, now_str)
                    updated_stations.append(r_stn)
                    if not is_all:
                        break

            if not updated_stations:
                return {
                    "success": False,
                    "error": f"找不到符合條件「{station_query}」的場站。"
                }

            logger.info(f"Updated cooldown for {len(updated_stations)} stations to {hours}h ({alert_type})")
            return {
                "success": True,
                "alert_type": alert_type,
                "hours": hours,
                "updated_count": len(updated_stations),
                "updated_stations": updated_stations[:5],
                "updated_at": now_str
            }
        except Exception as e:
            logger.error(f"Error updating cooldown: {e}")
            return {"success": False, "error": str(e)}

    def fetch_cooldown_history(self) -> Dict[tuple, Any]:
        """
        讀取各場站各警報最後推播時間
        回傳: { (station_name, alert_type): datetime_obj }
        """
        if not self.client or not self.spreadsheet_id:
            return {}
        try:
            from datetime import datetime
            sh = self.client.open_by_key(self.spreadsheet_id)
            ws = sh.worksheet(self.sheet_name_cooldown)
            records = ws.get_all_records()
            history = {}
            for r in records:
                sname = str(r.get("station_name", "")).strip()
                atype = str(r.get("alert_type", "")).strip().upper()
                dt_str = str(r.get("last_triggered_at", "")).strip()
                if sname and atype and dt_str:
                    try:
                        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                        history[(sname, atype)] = dt
                    except Exception:
                        pass
            return history
        except Exception as e:
            logger.warning(f"Error reading cooldown history worksheet: {e}")
            return {}

    def record_alert_triggers(self, triggered_items: List[Dict[str, Any]]):
        """
        記錄推播警報時間，用於冷卻判定
        """
        if not triggered_items or not self.client or not self.spreadsheet_id:
            return
        try:
            from datetime import datetime
            sh = self.client.open_by_key(self.spreadsheet_id)
            ws = sh.worksheet(self.sheet_name_cooldown)
            all_values = ws.get_all_values()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            existing_map = {}
            if all_values and len(all_values) > 1:
                for idx, row in enumerate(all_values[1:], start=2):
                    if len(row) >= 2:
                        existing_map[(row[0].strip(), row[1].strip().upper())] = idx

            for item in triggered_items:
                sname = str(item.get("station_name", "")).strip()
                atype = str(item.get("category", "")).strip().upper()
                if not sname or not atype:
                    continue

                if (sname, atype) in existing_map:
                    row_idx = existing_map[(sname, atype)]
                    ws.update_cell(row_idx, 3, now_str)
                else:
                    ws.append_row([sname, atype, now_str])
                    existing_map[(sname, atype)] = len(all_values) + 1
            logger.info(f"Recorded {len(triggered_items)} alert timestamps into cooldown log.")
        except Exception as e:
            logger.error(f"Error recording alert triggers: {e}")
