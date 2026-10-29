"""
Google Sheets API 資料服務模組
遵循 ISO27001 資安規範：透過 Base64 環境變數或安全憑證檔案載入 Service Account 金鑰
"""
import os
import json
import base64
import logging
from typing import List, Dict, Any, Optional, Tuple
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
        self.master_spreadsheet_id = os.getenv("MASTER_SPREADSHEET_ID", "1oaBHrT8mVfarrVXYJVwqphJEGk0JKWezUHmlRhAMVAQ")
        self.sheet_name_data = os.getenv("SHEET_NAME_DATA", "場站營運數據")
        self.sheet_name_tenants = os.getenv("SHEET_NAME_TENANTS", "多租戶權限表")
        self.sheet_name_settings = os.getenv("SHEET_NAME_SETTINGS", "場站警報設定")
        self.sheet_name_cooldown = os.getenv("SHEET_NAME_COOLDOWN", "最後警報紀錄")
        self.sheet_name_user_subs = os.getenv("SHEET_NAME_USER_SUBS", "個人監控場站表")
        self.sheet_name_config = os.getenv("SHEET_NAME_CONFIG", "系統全域參數")
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
            logger.warning("Sheets client not configured, cannot fetch operations data.")
            return []

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
            logger.warning("Sheets client not configured, cannot fetch tenant permissions.")
            return []

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
                r_cids = [c.strip() for c in rc_id.split(",") if c.strip()]
                if cid_str == "ALL" or cid_str in r_cids:
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
                r_cids = [c.strip() for c in r_cid.split(",") if c.strip()]
                if cid_str != "ALL" and cid_str not in r_cids:
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

                r_cids = [c.strip() for c in r_cid.split(",") if c.strip()]
                if cid_str != "ALL" and cid_str not in r_cids:
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

    def fetch_enterprise_auth_map(self) -> Dict[str, Dict[str, Any]]:
        """
        讀取車車戰情室主檔「企業名單」，取得企業授權碼對照表
        從雲端主檔即時動態比對，落實 ISO27001 不寫死任何真實授權碼
        """
        if not self.client or not self.master_spreadsheet_id:
            logger.warning("Master sheets client not configured, cannot fetch enterprise auth map.")
            return {}
        try:
            m_sh = self.client.open_by_key(self.master_spreadsheet_id)
            ws = m_sh.worksheet("企業名單")
            values = ws.get_all_values()
            if not values or len(values) < 2:
                return {}

            auth_map = {}
            for row in values[1:]:
                if len(row) >= 3:
                    cname = str(row[0]).strip()
                    code = str(row[1]).strip()
                    cid = str(row[2]).strip()
                    if code:
                        auth_map[code] = {"company_id": cid, "company_name": cname}
            return auth_map
        except Exception as e:
            logger.error(f"Error reading enterprise auth map: {e}")
            return {}

    def bind_user_to_company(self, line_user_id: str, auth_code: str) -> Optional[Dict[str, Any]]:
        """
        使用企業授權碼綁定使用者，寫入「多租戶權限表」
        """
        auth_map = self.fetch_enterprise_auth_map()
        clean_code = auth_code.strip()
        if clean_code not in auth_map:
            return None

        ent_info = auth_map[clean_code]
        target_cid = ent_info["company_id"]
        target_cname = ent_info["company_name"]

        if not self.client or not self.spreadsheet_id:
            return ent_info

        try:
            sh = self.client.open_by_key(self.spreadsheet_id)
            ws = sh.worksheet(self.sheet_name_tenants)
            all_values = ws.get_all_values()
            if not all_values or len(all_values) < 2:
                return ent_info

            header = [h.strip() for h in all_values[0]]
            col_cid = header.index("company_id")
            col_uids = header.index("authorized_line_user_ids")

            user_added = False
            for idx, row in enumerate(all_values[1:], start=2):
                r_cid = str(row[col_cid] if col_cid < len(row) else "").strip()
                if r_cid == target_cid:
                    current_uids = str(row[col_uids] if col_uids < len(row) else "").strip()
                    uid_list = [u.strip() for u in current_uids.replace(";", ",").split(",") if u.strip()]
                    if line_user_id not in uid_list:
                        uid_list.append(line_user_id)
                        ws.update_cell(idx, col_uids + 1, ",".join(uid_list))
                        logger.info(f"Bound user {line_user_id} to company {target_cname} ({target_cid})")
                    user_added = True
                    break

            if not user_added:
                # 若無該公司列則新增一列
                ws.append_row([target_cid, target_cname, "管理員 (甲+乙)", "ALL", line_user_id, "TRUE"])

            return ent_info
        except Exception as e:
            logger.error(f"Error binding user to company: {e}")
            return ent_info

    def fetch_candidate_sites(self, company_id: str, line_user_id: str) -> List[Dict[str, Any]]:
        """
        從車車戰情室主檔讀取該公司有權限的所有候選場站 (已依序號編列 [01]~[99])
        並標記哪些已被該使用者加入個人監控
        """
        if not self.client or not self.master_spreadsheet_id:
            return []

        try:
            m_sh = self.client.open_by_key(self.master_spreadsheet_id)
            ws = m_sh.worksheet("site list")
            records = ws.get_all_records()

            user_subs = self.fetch_user_subscriptions(line_user_id)
            subscribed_names = set(s.get("station_name") for s in user_subs)

            cid_str = str(company_id).strip()
            candidate_list = []
            seen_names = set()

            for r in records:
                sname = str(r.get("停車場名稱") or r.get("場站名稱") or "").strip()
                sip = str(r.get("IP位址") or r.get("IP") or "-").strip()
                auth_str = str(r.get("授權企業") or "1").strip()
                auth_cids = [c.strip() for c in auth_str.replace(" ", "").split(",") if c.strip()]
                # 若為 '12' 包含 '1'
                for ch in auth_str:
                    if ch.isdigit() and ch not in auth_cids:
                        auth_cids.append(ch)

                if cid_str not in ["ALL", "0", "ROOT"] and cid_str not in auth_cids:
                    continue

                if not sname or sname in seen_names:
                    continue
                seen_names.add(sname)

                candidate_list.append({
                    "station_name": sname,
                    "station_ip": sip,
                    "is_subscribed": (sname in subscribed_names)
                })

            # 編排 2 碼代號 01, 02...
            for idx, c in enumerate(candidate_list, start=1):
                c["station_code"] = f"{idx:02d}"

            return candidate_list
        except Exception as e:
            logger.error(f"Error fetching candidate sites: {e}")
            return []

    def fetch_user_subscriptions(self, line_user_id: str) -> List[Dict[str, Any]]:
        """
        讀取該使用者在「個人監控場站表」中自選的場站 (最多 10 場)
        """
        if not self.client or not self.spreadsheet_id:
            return []
        try:
            sh = self.client.open_by_key(self.spreadsheet_id)
            ws = sh.worksheet(self.sheet_name_user_subs)
            records = ws.get_all_records()
            clean_records = [{str(k).strip(): v for k, v in r.items()} for r in records]

            user_subs = []
            clean_uid = str(line_user_id).strip()
            for r in clean_records:
                if str(r.get("line_user_id", "")).strip() == clean_uid:
                    user_subs.append(r)
            return user_subs
        except Exception as e:
            logger.error(f"Error reading user subscriptions: {e}")
            return []

    def add_user_subscription(
        self,
        line_user_id: str,
        company_id: str,
        station_query: str
    ) -> Dict[str, Any]:
        """
        管理者自行新增監控場站 (依「系統全域參數」之 max_stations_per_user 進行動態上限限制)
        """
        clean_uid = str(line_user_id).strip()
        current_subs = self.fetch_user_subscriptions(clean_uid)

        # 1. 取得全域上限 (由 Google 試算表決定，預設 10)
        configs = self.fetch_system_configs()
        try:
            max_limit = int(configs.get("max_stations_per_user", 10))
        except (ValueError, TypeError):
            max_limit = 10

        # 超級管理者 (company_id == '0') 可不受 10 場限制，或享有更高上限
        is_super = (str(company_id).strip() in ["0", "ROOT"])
        effective_max = 50 if is_super else max_limit

        if len(current_subs) >= effective_max:
            return {
                "success": False,
                "error": f"您目前已監控 {len(current_subs)} 座場站（已達上限 {effective_max} 座），請先輸入 /移除 移除不需要的場站後再新增。"
            }

        # 2. 搜尋候選場站
        candidates = self.fetch_candidate_sites(company_id, clean_uid)
        matched = None
        q = station_query.strip().lower()

        # 支援純數字或 2 碼代號比對 (如 '1' -> '01')
        q_code = f"{int(q):02d}" if q.isdigit() else q

        for c in candidates:
            if c["station_code"] == q_code or q in c["station_name"].lower():
                matched = c
                break

        if not matched:
            return {
                "success": False,
                "error": f"找不到代號或名稱為「{station_query}」的合法授權場站。請輸入 /可選場站 查看可用清單。"
            }

        # 3. 檢查是否已在監控中
        for s in current_subs:
            if s.get("station_name") == matched["station_name"]:
                return {
                    "success": False,
                    "error": f"場站「{matched['station_name']}」已在您的監控清單中，無需重複新增。"
                }

        # 4. 寫入個人監控場站表
        try:
            from datetime import datetime
            sh = self.client.open_by_key(self.spreadsheet_id)
            ws = sh.worksheet(self.sheet_name_user_subs)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            new_row = [
                clean_uid,
                str(company_id),
                matched["station_code"],
                matched["station_name"],
                matched["station_ip"],
                "5",   # space_lower default
                "無",  # space_upper default
                "6",   # space_cooldown default
                "24",  # offline_cooldown default
                now_str
            ]
            ws.append_row(new_row)
            new_count = len(current_subs) + 1
            logger.info(f"Added subscription for user {clean_uid}: {matched['station_name']} (Total: {new_count}/10)")
            return {
                "success": True,
                "station_name": matched["station_name"],
                "station_code": matched["station_code"],
                "station_ip": matched["station_ip"],
                "current_count": new_count,
                "max_count": effective_max
            }
        except Exception as e:
            logger.error(f"Error adding user subscription: {e}")
            return {"success": False, "error": str(e)}

    def remove_user_subscription(self, line_user_id: str, station_query: str) -> Dict[str, Any]:
        """
        管理者移除個人監控場站
        """
        clean_uid = str(line_user_id).strip()
        q = station_query.strip().lower()
        q_code = f"{int(q):02d}" if q.isdigit() else q

        if not self.client or not self.spreadsheet_id:
            return {"success": False, "error": "Sheets client not configured"}

        try:
            sh = self.client.open_by_key(self.spreadsheet_id)
            ws = sh.worksheet(self.sheet_name_user_subs)
            all_values = ws.get_all_values()
            if not all_values or len(all_values) < 2:
                return {"success": False, "error": "您目前尚未監控任何場站。"}

            header = [h.strip() for h in all_values[0]]
            col_uid = header.index("line_user_id")
            col_code = header.index("station_code")
            col_stn = header.index("station_name")

            target_row_idx = None
            removed_name = ""

            for idx, row in enumerate(all_values[1:], start=2):
                r_uid = str(row[col_uid] if col_uid < len(row) else "").strip()
                r_code = str(row[col_code] if col_code < len(row) else "").strip()
                r_stn = str(row[col_stn] if col_stn < len(row) else "").strip()

                if r_uid == clean_uid:
                    if r_code == q_code or q in r_stn.lower():
                        target_row_idx = idx
                        removed_name = r_stn
                        break

            if not target_row_idx:
                return {
                    "success": False,
                    "error": f"在您的個人監控清單中找不到「{station_query}」。請輸入 /查詢 查看您目前的監控場站。"
                }

            ws.delete_rows(target_row_idx)
            current_subs = self.fetch_user_subscriptions(clean_uid)
            return {
                "success": True,
                "station_name": removed_name,
                "remaining_count": len(current_subs),
                "max_count": 10
            }
        except Exception as e:
            logger.error(f"Error removing user subscription: {e}")
            return {"success": False, "error": str(e)}

    def update_user_station_space(
        self,
        line_user_id: str,
        station_query: str,
        lower: str,
        upper: str
    ) -> Dict[str, Any]:
        """
        更新該使用者名下特定監控場站的車位門檻
        """
        clean_uid = str(line_user_id).strip()
        q = station_query.strip().lower()
        q_code = f"{int(q):02d}" if q.isdigit() else q

        if not self.client or not self.spreadsheet_id:
            return {"success": False, "error": "Sheets client not configured"}

        try:
            from datetime import datetime
            sh = self.client.open_by_key(self.spreadsheet_id)
            ws = sh.worksheet(self.sheet_name_user_subs)
            all_values = ws.get_all_values()
            if not all_values or len(all_values) < 2:
                return {"success": False, "error": "您尚未監控此場站。"}

            header = [h.strip() for h in all_values[0]]
            col_uid = header.index("line_user_id")
            col_code = header.index("station_code")
            col_stn = header.index("station_name")
            col_low = header.index("space_lower")
            col_up = header.index("space_upper")
            col_upd = header.index("updated_at")

            target_row_idx = None
            matched_name = ""

            for idx, row in enumerate(all_values[1:], start=2):
                r_uid = str(row[col_uid] if col_uid < len(row) else "").strip()
                r_code = str(row[col_code] if col_code < len(row) else "").strip()
                r_stn = str(row[col_stn] if col_stn < len(row) else "").strip()

                if r_uid == clean_uid and (r_code == q_code or q in r_stn.lower()):
                    target_row_idx = idx
                    matched_name = r_stn
                    break

            if not target_row_idx:
                return {
                    "success": False,
                    "error": f"在您的監控清單中找不到「{station_query}」。請先輸入 /新增 將其加入監控。"
                }

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ws.update_cell(target_row_idx, col_low + 1, str(lower))
            ws.update_cell(target_row_idx, col_up + 1, str(upper))
            ws.update_cell(target_row_idx, col_upd + 1, now_str)

            return {
                "success": True,
                "station_name": matched_name,
                "lower": str(lower),
                "upper": str(upper),
                "updated_at": now_str
            }
        except Exception as e:
            logger.error(f"Error updating user space threshold: {e}")
            return {"success": False, "error": str(e)}

    def update_user_station_cooldown(
        self,
        line_user_id: str,
        station_query: str,
        alert_type: str,
        hours: int
    ) -> Dict[str, Any]:
        """
        更新該使用者名下場站的冷卻時間
        """
        if hours not in [6, 12, 18, 24, 36]:
            return {"success": False, "error": "冷卻時數僅支援 6、12、18、24 或 36 小時。"}

        clean_uid = str(line_user_id).strip()
        q = station_query.strip().lower()
        q_code = f"{int(q):02d}" if q.isdigit() else q
        is_all = (q in ["全部", "all", "*", "所有"])

        if not self.client or not self.spreadsheet_id:
            return {"success": False, "error": "Sheets client not configured"}

        try:
            from datetime import datetime
            sh = self.client.open_by_key(self.spreadsheet_id)
            ws = sh.worksheet(self.sheet_name_user_subs)
            all_values = ws.get_all_values()
            if not all_values or len(all_values) < 2:
                return {"success": False, "error": "您目前尚未監控任何場站。"}

            header = [h.strip() for h in all_values[0]]
            col_uid = header.index("line_user_id")
            col_code = header.index("station_code")
            col_stn = header.index("station_name")
            target_col_name = "space_cooldown" if "車位" in alert_type else "offline_cooldown"
            col_cool = header.index(target_col_name)
            col_upd = header.index("updated_at")

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            updated_stations = []

            for idx, row in enumerate(all_values[1:], start=2):
                r_uid = str(row[col_uid] if col_uid < len(row) else "").strip()
                r_code = str(row[col_code] if col_code < len(row) else "").strip()
                r_stn = str(row[col_stn] if col_stn < len(row) else "").strip()

                if r_uid == clean_uid:
                    if is_all or (r_code == q_code or q in r_stn.lower()):
                        ws.update_cell(idx, col_cool + 1, str(hours))
                        ws.update_cell(idx, col_upd + 1, now_str)
                        updated_stations.append(r_stn)
                        if not is_all:
                            break

            if not updated_stations:
                return {
                    "success": False,
                    "error": f"在您的監控清單中找不到「{station_query}」。"
                }

            return {
                "success": True,
                "alert_type": alert_type,
                "hours": hours,
                "updated_count": len(updated_stations),
                "updated_stations": updated_stations[:5],
                "updated_at": now_str
            }
        except Exception as e:
            logger.error(f"Error updating user cooldown: {e}")
            return {"success": False, "error": str(e)}

    def fetch_all_user_subscriptions(self) -> List[Dict[str, Any]]:
        """
        讀取所有使用者的個人監控清單 (供巡檢推播時分派使用)
        """
        if not self.client or not self.spreadsheet_id:
            return []
        try:
            sh = self.client.open_by_key(self.spreadsheet_id)
            ws = sh.worksheet(self.sheet_name_user_subs)
            records = ws.get_all_records()
            return [{str(k).strip(): v for k, v in r.items()} for r in records]
        except Exception as e:
            logger.error(f"Error fetching all subscriptions: {e}")
            return []

    def fetch_system_configs(self) -> Dict[str, str]:
        """
        讀取「系統全域參數」分頁，支援換月自動將推播次數歸零
        """
        if not self.client or not self.spreadsheet_id:
            return {
                "max_stations_per_user": "10",
                "maintenance_mode": "OFF",
                "monthly_push_count": "0",
                "monthly_push_warning": "195",
                "default_space_lower": "5",
                "default_space_cooldown": "6",
                "default_offline_cooldown": "24"
            }
        try:
            from datetime import datetime
            sh = self.client.open_by_key(self.spreadsheet_id)
            ws = sh.worksheet(self.sheet_name_config)
            records = ws.get_all_records()
            configs = {}
            for r in records:
                k = str(r.get("config_key") or "").strip()
                v = str(r.get("config_value") or "").strip()
                if k:
                    configs[k] = v

            # 換月自動歸零檢查 (例如 2026-10 進入 2026-11)
            cur_month = datetime.now().strftime("%Y-%m")
            saved_month = configs.get("current_count_month", "")
            if saved_month and saved_month != cur_month:
                logger.info(f"Month rollover detected ({saved_month} -> {cur_month}). Resetting monthly push counter to 0.")
                self.update_system_config("monthly_push_count", "0")
                self.update_system_config("current_count_month", cur_month)
                configs["monthly_push_count"] = "0"
                configs["current_count_month"] = cur_month

            return configs
        except Exception as e:
            logger.error(f"Error reading system configs: {e}")
            return {
                "max_stations_per_user": "10",
                "maintenance_mode": "OFF",
                "monthly_push_count": "0",
                "monthly_push_warning": "195"
            }

    def update_system_config(self, key: str, value: str) -> bool:
        """
        更新系統全域參數
        """
        if not self.client or not self.spreadsheet_id:
            return False
        try:
            from datetime import datetime
            sh = self.client.open_by_key(self.spreadsheet_id)
            ws = sh.worksheet(self.sheet_name_config)
            all_values = ws.get_all_values()
            if not all_values or len(all_values) < 2:
                return False

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            target_key = key.strip()

            for idx, row in enumerate(all_values[1:], start=2):
                if row and row[0].strip() == target_key:
                    ws.update_cell(idx, 3, str(value))
                    ws.update_cell(idx, 5, now_str)
                    logger.info(f"Updated system config: {target_key} = {value}")
                    return True

            # 若無該鍵值則新增
            ws.append_row([target_key, target_key, str(value), "動態新增參數", now_str])
            return True
        except Exception as e:
            logger.error(f"Error updating system config {key}: {e}")
            return False

    def increment_monthly_push_count(self, increment_by: int = 1) -> Tuple[int, bool]:
        """
        累加本月推播則數，並判定是否剛達到 195 則警戒門檻
        回傳: (new_count, just_reached_195)
        """
        configs = self.fetch_system_configs()
        try:
            current_count = int(configs.get("monthly_push_count", 0))
        except (ValueError, TypeError):
            current_count = 0

        try:
            warning_threshold = int(configs.get("monthly_push_warning", 195))
        except (ValueError, TypeError):
            warning_threshold = 195

        new_count = current_count + increment_by
        self.update_system_config("monthly_push_count", str(new_count))

        # 判定是否「首次達標或跨越警戒線」
        just_reached = (current_count < warning_threshold and new_count >= warning_threshold)
        return new_count, just_reached

    def fetch_super_admin_user_ids(self) -> List[str]:
        """
        取得所有超級管理者 (company_id == '0') 的 LINE User ID 清單
        """
        permissions = self.fetch_tenant_permissions()
        super_uids = []
        for p in permissions:
            cid = str(p.get("company_id") or p.get("tenant_id") or "").strip()
            if cid in ["0", "ROOT"]:
                raw_ids = str(p.get("authorized_line_user_ids", "")).replace(";", ",")
                uids = [u.strip() for u in raw_ids.split(",") if u.strip()]
                super_uids.extend(uids)
        return list(set(super_uids))
