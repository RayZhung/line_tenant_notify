"""
Google Sheets API 資料服務模組
遵循 ISO27001 資安規範：透過 Base64 環境變數或安全憑證檔案載入 Service Account 金鑰
"""
import os
import time
import json
import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple, Callable
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("sheets_service")


def _tw_now() -> datetime:
    """取得台灣標準時間 (UTC+8)，避免 Vercel / GitHub Actions 顯示慢 8 小時的 UTC 時間"""
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)

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
        self._sh_cache: Dict[str, Any] = {}
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._stale_cache: Dict[str, Any] = {}
        self.last_error: Optional[str] = None
        self._init_client()

    def _init_client(self):
        creds = get_google_credentials()
        if creds and GSPREAD_AVAILABLE:
            try:
                self.client = gspread.authorize(creds)
                self._sh_cache.clear()
                logger.info("Successfully authorized with Google Sheets API.")
            except Exception as e:
                logger.error(f"gspread authorization failed: {e}")

    def _open_spreadsheet(self, sheet_id: str) -> Any:
        """
        複用 Spreadsheet 控點，大幅降低每次操作重複查詢試算表 Metadata 的 Read Quota 消耗
        """
        if sheet_id not in self._sh_cache:
            self._sh_cache[sheet_id] = self.client.open_by_key(sheet_id)
        return self._sh_cache[sheet_id]

    def _with_retry(self, fn: Callable[[], Any], retries: int = 3) -> Any:
        """
        自動重連與防限流重試：解決 Vercel 休眠容器 Keep-Alive 斷線與瞬時 429 Quota 問題
        """
        last_exc = None
        for attempt in range(1, retries + 1):
            try:
                res = fn()
                self.last_error = None
                return res
            except Exception as e:
                last_exc = e
                err_str = str(e)
                logger.warning(f"Google Sheets API call failed (attempt {attempt}/{retries}): {e}")
                self._sh_cache.clear()
                self._init_client()
                if attempt < retries:
                    wait_sec = 1.5 * attempt if "429" in err_str else 0.6 * attempt
                    time.sleep(wait_sec)
        self.last_error = str(last_exc) if last_exc else "Unknown Google Sheets error"
        raise last_exc

    def _get_cached(self, key: str, ttl_sec: float, loader_fn: Callable[[], Any]) -> Any:
        """
        短效記憶體快取：避免連續點擊按鈕時在數秒內重複讀取相同工作表而觸發 60 reads/min 限制；
        若重試後仍失敗但有舊快取，優先退守舊快取防止誤判為未綁定。
        """
        now = time.time()
        if key in self._cache:
            ts, val = self._cache[key]
            if now - ts < ttl_sec:
                self.last_error = None
                return val

        try:
            fresh_val = self._with_retry(loader_fn)
            self._cache[key] = (now, fresh_val)
            self._stale_cache[key] = fresh_val
            return fresh_val
        except Exception as e:
            if key in self._stale_cache:
                logger.warning(f"Serving stale cache for '{key}' due to Sheets API error: {e}")
                self.last_error = None
                return self._stale_cache[key]
            raise

    def _invalidate_cache(self, *keys: str):
        for k in keys:
            self._cache.pop(k, None)

    def fetch_operations_data(self) -> List[Dict[str, Any]]:
        """
        讀取最新場站營運數據
        """
        if not self.client or not self.spreadsheet_id:
            logger.warning("Sheets client not configured, cannot fetch operations data.")
            return []

        try:
            def _load():
                sh = self._open_spreadsheet(self.spreadsheet_id)
                worksheet = sh.worksheet(self.sheet_name_data)
                records = worksheet.get_all_records()
                return [{str(k).strip(): v for k, v in r.items()} for r in records]
            return self._with_retry(_load)
        except Exception as e:
            logger.error(f"Error reading operations worksheet: {e}")
            return []

    def fetch_tenant_permissions(self) -> List[Dict[str, Any]]:
        """
        讀取多租戶權限表 (20 秒快取，綁定/解綁時立即刷新)
        """
        if not self.client or not self.spreadsheet_id:
            logger.warning("Sheets client not configured, cannot fetch tenant permissions.")
            return []

        try:
            def _load():
                sh = self._open_spreadsheet(self.spreadsheet_id)
                worksheet = sh.worksheet(self.sheet_name_tenants)
                records = worksheet.get_all_records()
                return [{str(k).strip(): v for k, v in r.items()} for r in records]
            return self._get_cached("tenant_permissions", 20.0, _load)
        except Exception as e:
            logger.error(f"Error reading tenant permissions worksheet: {e}")
            self.last_error = str(e)
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

            now_str = _tw_now().strftime("%Y-%m-%d %H:%M:%S")
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
            now_str = _tw_now().strftime("%Y-%m-%d %H:%M:%S")

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
        支援個人/租戶隔離鍵 (scope_id, station_name, alert_category) 與全域鍵 (station_name, alert_category)
        """
        if not self.client or not self.spreadsheet_id:
            return {}
        try:
            from datetime import datetime
            def _load():
                sh = self._open_spreadsheet(self.spreadsheet_id)
                ws = sh.worksheet(self.sheet_name_cooldown)
                return ws.get_all_records()

            records = self._with_retry(_load)
            history = {}
            for r in records:
                clean_r = {str(k).strip(): v for k, v in r.items()}
                sname = str(clean_r.get("station_name", "")).strip()
                scope_id = str(clean_r.get("company_id") or clean_r.get("line_user_id") or "").strip()
                atype = str(clean_r.get("alert_category") or clean_r.get("alert_type") or "").strip().upper()
                dt_str = str(clean_r.get("last_triggered_at", "")).strip()
                if sname and atype and dt_str:
                    try:
                        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                        if scope_id:
                            history[(scope_id, sname, atype)] = dt
                        if not scope_id or scope_id == "GLOBAL":
                            history[(sname, atype)] = dt
                    except Exception:
                        pass
            return history
        except Exception as e:
            logger.warning(f"Error reading cooldown history worksheet: {e}")
            return {}

    def record_alert_triggers(self, triggered_items: List[Dict[str, Any]]):
        """
        記錄推播警報時間至「最後警報紀錄」(對應 4 欄: station_name, company_id[或line_user_id], alert_category, last_triggered_at)
        """
        if not triggered_items or not self.client or not self.spreadsheet_id:
            return
        try:
            from datetime import datetime
            def _write():
                sh = self._open_spreadsheet(self.spreadsheet_id)
                ws = sh.worksheet(self.sheet_name_cooldown)
                all_values = ws.get_all_values()
                now_str = _tw_now().strftime("%Y-%m-%d %H:%M:%S")

                existing_map = {}
                row_count = len(all_values) if all_values else 1
                if all_values and len(all_values) > 1:
                    for idx, row in enumerate(all_values[1:], start=2):
                        if len(row) >= 3:
                            existing_map[(row[1].strip(), row[0].strip(), row[2].strip().upper())] = idx

                for item in triggered_items:
                    sname = str(item.get("station_name", "")).strip()
                    scope_id = str(item.get("line_user_id") or item.get("company_id") or "GLOBAL").strip()
                    atype = str(item.get("category", "")).strip().upper()
                    if not sname or not atype:
                        continue

                    key = (scope_id, sname, atype)
                    if key in existing_map:
                        row_idx = existing_map[key]
                        ws.update_cell(row_idx, 4, now_str)
                    else:
                        ws.append_row([sname, scope_id, atype, now_str], value_input_option="RAW")
                        row_count += 1
                        existing_map[key] = row_count

            self._with_retry(_write)
            logger.info(f"Recorded {len(triggered_items)} alert timestamps into cooldown log.")
        except Exception as e:
            logger.error(f"Error recording alert triggers: {e}")

    def fetch_enterprise_auth_map(self) -> Dict[str, Dict[str, Any]]:
        """
        讀取車車戰情室主檔「企業名單」，取得企業授權碼對照表 (60 秒快取)
        從雲端主檔即時動態比對，落實 ISO27001 不寫死任何真實授權碼
        """
        if not self.client or not self.master_spreadsheet_id:
            logger.warning("Master sheets client not configured, cannot fetch enterprise auth map.")
            return {}
        try:
            def _load():
                m_sh = self._open_spreadsheet(self.master_spreadsheet_id)
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

            return self._get_cached("enterprise_auth_map", 60.0, _load)
        except Exception as e:
            logger.error(f"Error reading enterprise auth map: {e}")
            self.last_error = str(e)
            return {}

    def bind_user_to_company(self, line_user_id: str, auth_code: str) -> Optional[Dict[str, Any]]:
        """
        使用企業授權碼綁定使用者，寫入「多租戶權限表」並立即刷新權限快取
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
            def _write():
                sh = self._open_spreadsheet(self.spreadsheet_id)
                ws = sh.worksheet(self.sheet_name_tenants)
                all_values = ws.get_all_values()
                if not all_values or len(all_values) < 2:
                    return

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
                    ws.append_row([target_cid, target_cname, "管理員 (甲+乙)", "ALL", line_user_id, "TRUE"], value_input_option="RAW")

            self._with_retry(_write)
            self._invalidate_cache("tenant_permissions")
            self._stale_cache.pop("tenant_permissions", None)
            return ent_info
        except Exception as e:
            logger.error(f"Error binding user to company: {e}")
            return ent_info

    def unbind_user(self, line_user_id: str) -> bool:
        """
        解除使用者綁定 (方式 A 主動求助式):
        1. 從「多租戶權限表」的所有列中移除該 line_user_id
        2. 從「個人監控場站表」中清空該使用者的自選場站 (ISO27001 跨企業資料清理)
        """
        clean_uid = str(line_user_id).strip()
        if not self.client or not self.spreadsheet_id:
            return False

        try:
            def _write():
                sh = self._open_spreadsheet(self.spreadsheet_id)

                # 1. 清理「多租戶權限表」
                ws_t = sh.worksheet(self.sheet_name_tenants)
                t_values = ws_t.get_all_values()
                if t_values and len(t_values) >= 2:
                    header = [h.strip() for h in t_values[0]]
                    if "authorized_line_user_ids" in header:
                        col_uids = header.index("authorized_line_user_ids")
                        for idx, row in enumerate(t_values[1:], start=2):
                            current_uids = str(row[col_uids] if col_uids < len(row) else "").strip()
                            uid_list = [u.strip() for u in current_uids.replace(";", ",").split(",") if u.strip()]
                            if clean_uid in uid_list:
                                uid_list.remove(clean_uid)
                                ws_t.update_cell(idx, col_uids + 1, ",".join(uid_list))
                                logger.info(f"Removed user {clean_uid} from row {idx} in {self.sheet_name_tenants}")

                # 2. 清理「個人監控場站表」中該使用者的資料
                ws_s = sh.worksheet(self.sheet_name_user_subs)
                s_values = ws_s.get_all_values()
                if s_values and len(s_values) >= 2:
                    s_header = [h.strip() for h in s_values[0]]
                    if "line_user_id" in s_header:
                        col_uid = s_header.index("line_user_id")
                        remaining_rows = [s_values[0]]
                        for row in s_values[1:]:
                            r_uid = str(row[col_uid] if col_uid < len(row) else "").strip()
                            if r_uid != clean_uid:
                                remaining_rows.append(row)
                        ws_s.clear()
                        col_letter = chr(64 + len(s_header)) if len(s_header) <= 26 else "J"
                        ws_s.update(
                            range_name=f"A1:{col_letter}{len(remaining_rows)}",
                            values=remaining_rows,
                            value_input_option="RAW"
                        )
                        logger.info(f"Cleared subscriptions for user {clean_uid}")

            self._with_retry(_write)
            self._invalidate_cache("tenant_permissions", "user_subs_all")
            self._stale_cache.pop("tenant_permissions", None)
            self._stale_cache.pop("user_subs_all", None)
            return True
        except Exception as e:
            logger.error(f"Error unbinding user {clean_uid}: {e}")
            return False

    def _fetch_master_site_list_raw(self) -> List[Dict[str, Any]]:
        """
        讀取主檔 site list 並快取 120 秒，避免連續新增場站時重複打爆 Master Sheet API
        """
        def _load():
            m_sh = self._open_spreadsheet(self.master_spreadsheet_id)
            ws = m_sh.worksheet("site list")
            return ws.get_all_records()
        return self._get_cached("master_site_list", 120.0, _load)

    def fetch_candidate_sites(self, company_id: str, line_user_id: str) -> List[Dict[str, Any]]:
        """
        從車車戰情室主檔讀取該公司有權限的所有候選場站 (已依序號編列 [01]~[99])
        並標記哪些已被該使用者加入個人監控
        """
        if not self.client or not self.master_spreadsheet_id:
            return []

        try:
            records = self._fetch_master_site_list_raw()
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

            for idx, c in enumerate(candidate_list, start=1):
                c["station_code"] = f"{idx:02d}"

            return candidate_list
        except Exception as e:
            logger.error(f"Error fetching candidate sites: {e}")
            self.last_error = str(e)
            return []

    def get_effective_max_stations(self, company_id: str) -> int:
        """
        取得該企業身分對應的最大可監控場站上限 (超級管理者 50 座，一般管理員依全域參數)
        """
        if str(company_id).strip() in ["0", "ROOT"]:
            return 50
        configs = self.fetch_system_configs()
        try:
            return int(configs.get("max_stations_per_user", 10))
        except (ValueError, TypeError):
            return 10

    def fetch_all_user_subscriptions(self) -> List[Dict[str, Any]]:
        """
        讀取所有使用者的個人監控清單 (5 秒快取，任何增刪改立即刷新)
        """
        if not self.client or not self.spreadsheet_id:
            return []
        try:
            def _load():
                sh = self._open_spreadsheet(self.spreadsheet_id)
                ws = sh.worksheet(self.sheet_name_user_subs)
                records = ws.get_all_records()
                cleaned = []
                for r in records:
                    item = {str(k).strip(): v for k, v in r.items()}
                    raw_c = str(item.get("station_code", "")).strip()
                    if raw_c.isdigit():
                        item["station_code"] = f"{int(raw_c):02d}"
                    cleaned.append(item)
                return cleaned
            return self._get_cached("user_subs_all", 5.0, _load)
        except Exception as e:
            logger.error(f"Error fetching all subscriptions: {e}")
            self.last_error = str(e)
            return []

    def fetch_user_subscriptions(self, line_user_id: str) -> List[Dict[str, Any]]:
        """
        讀取該使用者在「個人監控場站表」中自選的場站
        """
        all_records = self.fetch_all_user_subscriptions()
        clean_uid = str(line_user_id).strip()
        return [dict(r) for r in all_records if str(r.get("line_user_id", "")).strip() == clean_uid]

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
        if self.last_error:
            return {"success": False, "error": "雲端試算表連線忙碌中，請稍候幾秒再點擊重試。"}

        # 1. 取得全域上限與預設值 (走快取，0 額外讀取)
        configs = self.fetch_system_configs()
        effective_max = self.get_effective_max_stations(company_id)

        if len(current_subs) >= effective_max:
            return {
                "success": False,
                "error": f"您目前已監控 {len(current_subs)} 座場站（已達上限 {effective_max} 座），請先移除不需要的場站後再新增。"
            }

        # 2. 搜尋候選場站 (Master site list 走 120s 快取)
        candidates = self.fetch_candidate_sites(company_id, clean_uid)
        matched = None
        q = station_query.strip().lower()
        q_code = f"{int(q):02d}" if q.isdigit() else q

        for c in candidates:
            if c["station_code"] == q_code or c["station_name"].lower() == q:
                matched = c
                break
        if not matched and not q.isdigit():
            for c in candidates:
                if q in c["station_name"].lower():
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

        # 4. 寫入個人監控場站表並立即刷新快取
        try:
            from datetime import datetime
            now_str = _tw_now().strftime("%Y-%m-%d %H:%M:%S")
            def_low = str(configs.get("default_space_lower", "5"))
            def_s_cool = str(configs.get("default_space_cooldown", "6"))
            def_o_cool = str(configs.get("default_offline_cooldown", "24"))

            new_row = [
                clean_uid,
                str(company_id),
                matched["station_code"],
                matched["station_name"],
                matched["station_ip"],
                def_low,
                "無",
                def_s_cool,
                def_o_cool,
                now_str
            ]

            def _write():
                sh = self._open_spreadsheet(self.spreadsheet_id)
                ws = sh.worksheet(self.sheet_name_user_subs)
                ws.append_row(new_row, value_input_option="RAW")

            self._with_retry(_write)
            self._invalidate_cache("user_subs_all")
            self._stale_cache.pop("user_subs_all", None)

            new_count = len(current_subs) + 1
            logger.info(f"Added subscription for user {clean_uid}: {matched['station_name']} (Total: {new_count}/{effective_max})")
            return {
                "success": True,
                "station_name": matched["station_name"],
                "station_code": matched["station_code"],
                "station_ip": matched["station_ip"],
                "current_count": new_count,
                "max_count": effective_max,
                "default_lower": def_low,
                "default_space_cooldown": def_s_cool,
                "default_offline_cooldown": def_o_cool
            }
        except Exception as e:
            logger.error(f"Error adding user subscription: {e}")
            return {"success": False, "error": f"寫入失敗，請稍候重試 ({e})"}

    def remove_user_subscription(self, line_user_id: str, station_query: str, company_id: str = "1") -> Dict[str, Any]:
        """
        管理者移除個人監控場站
        """
        clean_uid = str(line_user_id).strip()
        q = station_query.strip().lower()
        q_code = f"{int(q):02d}" if q.isdigit() else q

        if not self.client or not self.spreadsheet_id:
            return {"success": False, "error": "Sheets client not configured"}

        try:
            def _remove():
                sh = self._open_spreadsheet(self.spreadsheet_id)
                ws = sh.worksheet(self.sheet_name_user_subs)
                all_values = ws.get_all_values()
                if not all_values or len(all_values) < 2:
                    return None, 0

                header = [h.strip() for h in all_values[0]]
                col_uid = header.index("line_user_id")
                col_code = header.index("station_code")
                col_stn = header.index("station_name")

                target_row_idx = None
                removed_name = ""

                user_rows = []
                for idx, row in enumerate(all_values[1:], start=2):
                    r_uid = str(row[col_uid] if col_uid < len(row) else "").strip()
                    if r_uid == clean_uid:
                        r_code_raw = str(row[col_code] if col_code < len(row) else "").strip()
                        r_code = f"{int(r_code_raw):02d}" if r_code_raw.isdigit() else r_code_raw
                        r_stn = str(row[col_stn] if col_stn < len(row) else "").strip()
                        user_rows.append((idx, r_code, r_stn))

                if not user_rows:
                    return "EMPTY", 0

                for idx, r_code, r_stn in user_rows:
                    if r_code == q_code or r_stn.lower() == q:
                        target_row_idx = idx
                        removed_name = r_stn
                        break
                if not target_row_idx and not q.isdigit():
                    for idx, r_code, r_stn in user_rows:
                        if q in r_stn.lower():
                            target_row_idx = idx
                            removed_name = r_stn
                            break

                if not target_row_idx:
                    return None, len(user_rows)

                ws.delete_rows(target_row_idx)
                return removed_name, len(user_rows) - 1

            removed_name, remaining_cnt = self._with_retry(_remove)
            self._invalidate_cache("user_subs_all")
            self._stale_cache.pop("user_subs_all", None)

            if removed_name == "EMPTY":
                return {"success": False, "error": "您目前尚未監控任何場站。"}
            if not removed_name:
                return {
                    "success": False,
                    "error": f"在您的個人監控清單中找不到「{station_query}」。請輸入 /查詢 查看您目前的監控場站。"
                }

            effective_max = self.get_effective_max_stations(company_id)
            return {
                "success": True,
                "station_name": removed_name,
                "remaining_count": remaining_cnt,
                "max_count": effective_max
            }
        except Exception as e:
            logger.error(f"Error removing user subscription: {e}")
            return {"success": False, "error": str(e)}

    def clear_user_subscriptions(self, line_user_id: str, company_id: str = "1") -> Dict[str, Any]:
        """
        一次清空該使用者名下所有個人監控場站（僅操作「個人監控場站表」，完整保留「多租戶權限表」之企業綁定身份）
        """
        clean_uid = str(line_user_id).strip()
        if not self.client or not self.spreadsheet_id:
            return {"success": False, "error": "Sheets client not configured"}

        try:
            def _clear():
                sh = self._open_spreadsheet(self.spreadsheet_id)
                ws = sh.worksheet(self.sheet_name_user_subs)
                all_values = ws.get_all_values()
                if not all_values or len(all_values) < 2:
                    return []

                header = [h.strip() for h in all_values[0]]
                col_uid = header.index("line_user_id")
                col_code = header.index("station_code") if "station_code" in header else -1
                col_stn = header.index("station_name")

                remaining_rows = [all_values[0]]
                removed_labels: List[str] = []

                for row in all_values[1:]:
                    r_uid = str(row[col_uid] if col_uid < len(row) else "").strip()
                    if r_uid == clean_uid:
                        r_code_raw = str(row[col_code] if 0 <= col_code < len(row) else "").strip()
                        r_code = f"{int(r_code_raw):02d}" if r_code_raw.isdigit() else r_code_raw
                        r_stn = str(row[col_stn] if col_stn < len(row) else "").strip()
                        removed_labels.append(f"[{r_code}] {r_stn}" if r_code else r_stn)
                    else:
                        remaining_rows.append(row)

                if not removed_labels:
                    return []

                ws.clear()
                col_letter = chr(64 + len(header)) if len(header) <= 26 else "J"
                ws.update(
                    range_name=f"A1:{col_letter}{len(remaining_rows)}",
                    values=remaining_rows,
                    value_input_option="RAW",
                )
                return removed_labels

            removed_list = self._with_retry(_clear)
            self._invalidate_cache("user_subs_all")
            self._stale_cache.pop("user_subs_all", None)

            if not removed_list:
                return {"success": False, "error": "您目前尚未監控任何場站，無需執行清空。"}

            effective_max = self.get_effective_max_stations(company_id)
            logger.info(f"Cleared all {len(removed_list)} subscriptions for user {clean_uid}")
            return {
                "success": True,
                "removed_stations": removed_list,
                "removed_count": len(removed_list),
                "remaining_count": 0,
                "max_count": effective_max,
            }
        except Exception as e:
            logger.error(f"Error clearing all user subscriptions: {e}")
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
            now_str = _tw_now().strftime("%Y-%m-%d %H:%M:%S")

            def _update():
                sh = self._open_spreadsheet(self.spreadsheet_id)
                ws = sh.worksheet(self.sheet_name_user_subs)
                all_values = ws.get_all_values()
                if not all_values or len(all_values) < 2:
                    return "EMPTY"

                header = [h.strip() for h in all_values[0]]
                col_uid = header.index("line_user_id")
                col_code = header.index("station_code")
                col_stn = header.index("station_name")
                col_low = header.index("space_lower")
                col_up = header.index("space_upper")
                col_upd = header.index("updated_at")

                target_row_idx = None
                matched_name = ""

                user_rows = []
                for idx, row in enumerate(all_values[1:], start=2):
                    r_uid = str(row[col_uid] if col_uid < len(row) else "").strip()
                    if r_uid == clean_uid:
                        r_code_raw = str(row[col_code] if col_code < len(row) else "").strip()
                        r_code = f"{int(r_code_raw):02d}" if r_code_raw.isdigit() else r_code_raw
                        r_stn = str(row[col_stn] if col_stn < len(row) else "").strip()
                        user_rows.append((idx, r_code, r_stn))

                for idx, r_code, r_stn in user_rows:
                    if r_code == q_code or r_stn.lower() == q:
                        target_row_idx = idx
                        matched_name = r_stn
                        break
                if not target_row_idx and not q.isdigit():
                    for idx, r_code, r_stn in user_rows:
                        if q in r_stn.lower():
                            target_row_idx = idx
                            matched_name = r_stn
                            break

                if not target_row_idx:
                    return None

                ws.update_cell(target_row_idx, col_low + 1, str(lower))
                ws.update_cell(target_row_idx, col_up + 1, str(upper))
                ws.update_cell(target_row_idx, col_upd + 1, now_str)
                return matched_name

            matched_name = self._with_retry(_update)
            self._invalidate_cache("user_subs_all")
            self._stale_cache.pop("user_subs_all", None)

            if matched_name == "EMPTY" or not matched_name:
                return {
                    "success": False,
                    "error": f"在您的監控清單中找不到「{station_query}」。請先輸入 /新增 將其加入監控。"
                }

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
            now_str = _tw_now().strftime("%Y-%m-%d %H:%M:%S")

            def _update():
                sh = self._open_spreadsheet(self.spreadsheet_id)
                ws = sh.worksheet(self.sheet_name_user_subs)
                all_values = ws.get_all_values()
                if not all_values or len(all_values) < 2:
                    return "EMPTY"

                header = [h.strip() for h in all_values[0]]
                col_uid = header.index("line_user_id")
                col_code = header.index("station_code")
                col_stn = header.index("station_name")
                target_col_name = "space_cooldown" if "車位" in alert_type else "offline_cooldown"
                col_cool = header.index(target_col_name)
                col_upd = header.index("updated_at")

                updated = []
                for idx, row in enumerate(all_values[1:], start=2):
                    r_uid = str(row[col_uid] if col_uid < len(row) else "").strip()
                    r_code_raw = str(row[col_code] if col_code < len(row) else "").strip()
                    r_code = f"{int(r_code_raw):02d}" if r_code_raw.isdigit() else r_code_raw
                    r_stn = str(row[col_stn] if col_stn < len(row) else "").strip()

                    if r_uid == clean_uid:
                        if is_all or (r_code == q_code or r_stn.lower() == q or (not q.isdigit() and q in r_stn.lower())):
                            ws.update_cell(idx, col_cool + 1, str(hours))
                            ws.update_cell(idx, col_upd + 1, now_str)
                            updated.append(r_stn)
                            if not is_all:
                                break
                return updated

            updated_stations = self._with_retry(_update)
            self._invalidate_cache("user_subs_all")
            self._stale_cache.pop("user_subs_all", None)

            if updated_stations == "EMPTY":
                return {"success": False, "error": "您目前尚未監控任何場站。"}
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

    def fetch_system_configs(self) -> Dict[str, str]:
        """
        讀取「系統全域參數」分頁 (20 秒快取，修改參數時立即刷新)，支援換月自動將推播次數歸零
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
            def _load():
                sh = self._open_spreadsheet(self.spreadsheet_id)
                ws = sh.worksheet(self.sheet_name_config)
                records = ws.get_all_records()
                cfgs = {}
                for r in records:
                    k = str(r.get("config_key") or "").strip()
                    v = str(r.get("config_value") or "").strip()
                    if k:
                        cfgs[k] = v
                return cfgs

            configs = dict(self._get_cached("system_configs", 20.0, _load))

            cur_month = _tw_now().strftime("%Y-%m")
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
        更新系統全域參數並立即刷新快取
        """
        if not self.client or not self.spreadsheet_id:
            return False
        try:
            from datetime import datetime
            now_str = _tw_now().strftime("%Y-%m-%d %H:%M:%S")
            target_key = key.strip()

            def _write():
                sh = self._open_spreadsheet(self.spreadsheet_id)
                ws = sh.worksheet(self.sheet_name_config)
                all_values = ws.get_all_values()
                if not all_values or len(all_values) < 2:
                    return False

                for idx, row in enumerate(all_values[1:], start=2):
                    if row and row[0].strip() == target_key:
                        ws.update_cell(idx, 3, str(value))
                        ws.update_cell(idx, 5, now_str)
                        logger.info(f"Updated system config: {target_key} = {value}")
                        return True

                ws.append_row([target_key, target_key, str(value), "動態新增參數", now_str], value_input_option="RAW")
                return True

            ok = self._with_retry(_write)
            self._invalidate_cache("system_configs")
            self._stale_cache.pop("system_configs", None)
            return ok
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
            cid_val = p.get("company_id")
            if cid_val is None or str(cid_val).strip() == "":
                cid_val = p.get("tenant_id", "")
            cid = str(cid_val).strip()
            if cid in ["0", "ROOT"]:
                raw_ids = str(p.get("authorized_line_user_ids", "")).replace(";", ",")
                uids = [u.strip() for u in raw_ids.split(",") if u.strip()]
                super_uids.extend(uids)
        return list(set(super_uids))
