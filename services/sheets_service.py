"""
Google Sheets API 資料服務模組
遵循 ISO27001 資安規範：透過 Base64 環境變數或安全憑證檔案載入 Service Account 金鑰
"""
import os
import json
import base64
import logging
from typing import List, Dict, Any, Optional

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
            return records
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
            return records
        except Exception as e:
            logger.error(f"Error reading tenant permissions worksheet: {e}")
            return []
