"""
多租戶與角色權限隔離服務 (Tenant & Role RBAC Isolation Service)
核心原則:
  1. 企業資料絕對隔離 (公司 A 絕對看不到公司 B 的資料)
  2. 角色職責精準分流 (運維人員【甲】只收設備/連線，營運人員【乙】只收車位)
  3. 智能警報篩選 (僅保留「可用車位警報」與「斷線警報」，移除發票數量警告)
  4. 冷卻時間防洗版 (支援 6, 12, 18, 24, 36 小時冷卻)
  5. LINE Bot 管理指令解析與權限校驗
"""
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

from services.nlp_service import NLPService

logger = logging.getLogger("tenant_service")

# 異常類別定義
CATEGORY_MAP = {
    "CONNECTIVITY": "🔌 場站連線狀態",
    "CAPACITY": "🚗 車流車位狀況"
}

class TenantService:
    @staticmethod
    def filter_anomalies(
        operations_data: List[Dict[str, Any]],
        alert_settings: Optional[List[Dict[str, Any]]] = None,
        cooldown_history: Optional[Dict[Tuple[str, str], datetime]] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        篩選出當前異常的場站數據 (支援自訂車位門檻與冷卻防洗版)
        回傳:
          (active_anomalies, newly_triggered_for_cooldown)
        """
        settings_map = {}
        if alert_settings:
            for s in alert_settings:
                sname = str(s.get("station_name", "")).strip()
                if sname:
                    settings_map[sname] = s

        cooldowns = cooldown_history or {}
        now = datetime.now()

        anomalies = []
        newly_triggered = []

        for row in operations_data:
            try:
                station_name = str(row.get("station_name") or row.get("停車場名稱") or "未知場站").strip()
                station_ip = str(row.get("station_ip") or row.get("IP位址") or row.get("IP") or "-").strip()
                company_id = str(row.get("company_id") or row.get("tenant_id") or row.get("授權企業") or "").strip()
                company_name = str(row.get("company_name") or row.get("公司名稱") or "").strip()
                category = str(row.get("category") or "").strip().upper()
                metric_name = str(row.get("metric_name") or "").strip()
                status_val = str(row.get("status") or "").strip().upper()
                update_time = str(row.get("update_time") or "剛剛").strip()

                # 忽略 HARDWARE (發票剩餘數量警告已依需求全面移除)
                if category == "HARDWARE" or "發票" in metric_name:
                    continue

                # 取得該場站專屬警報設定 (若無則套用預設值)
                stn_setting = settings_map.get(station_name, {})
                space_lower_raw = stn_setting.get("space_lower", "5")
                space_upper_raw = stn_setting.get("space_upper", "無")
                space_cooldown_hours = float(stn_setting.get("space_cooldown", 6) or 6)
                offline_cooldown_hours = float(stn_setting.get("offline_cooldown", 24) or 24)

                val_raw = row.get("current_value") or row.get("metric_value", 0)

                is_anomaly = False
                detected_category = ""
                anomaly_desc = ""
                cooldown_hours = 6

                # 1. 斷線判斷 (CONNECTIVITY)
                if category == "CONNECTIVITY" or status_val in ["OFFLINE", "斷線", "ERROR", "TIMEOUT"]:
                    detected_category = "CONNECTIVITY"
                    cooldown_hours = offline_cooldown_hours
                    is_anomaly = True
                    anomaly_desc = "場站斷線，無法連線回報數據"

                # 2. 車位判斷 (CAPACITY)
                elif category == "CAPACITY" or "車位" in metric_name:
                    detected_category = "CAPACITY"
                    cooldown_hours = space_cooldown_hours
                    try:
                        cur_spaces = float(val_raw)
                        # 檢查下限
                        if str(space_lower_raw).strip() not in ["無", "NONE", "", "-"]:
                            lower_thresh = float(space_lower_raw)
                            if cur_spaces < lower_thresh:
                                is_anomaly = True
                                anomaly_desc = f"剩餘車位僅 {int(cur_spaces)} 格 (低於警戒下限 {int(lower_thresh)} 格)"

                        # 檢查上限
                        if str(space_upper_raw).strip() not in ["無", "NONE", "", "-"]:
                            upper_thresh = float(space_upper_raw)
                            if cur_spaces > upper_thresh:
                                is_anomaly = True
                                anomaly_desc = f"剩餘車位達 {int(cur_spaces)} 格 (高於警戒上限 {int(upper_thresh)} 格)"
                    except (ValueError, TypeError):
                        pass

                if not is_anomaly or not detected_category:
                    continue

                # 3. 檢查冷卻時間 (Cooldown check)
                history_key = (station_name, detected_category)
                last_triggered_at = cooldowns.get(history_key)
                if last_triggered_at:
                    elapsed = (now - last_triggered_at).total_seconds()
                    limit_sec = cooldown_hours * 3600
                    if elapsed < limit_sec:
                        remaining_min = int((limit_sec - elapsed) / 60)
                        logger.info(
                            f"Suppressed {station_name} [{detected_category}] due to cooldown. "
                            f"(Remaining: {remaining_min} mins)"
                        )
                        continue

                # 符合警報條件且超過冷卻時間，加入派發清單
                anomaly_item = {
                    "station_name": station_name,
                    "station_ip": station_ip,
                    "company_id": company_id,
                    "company_name": company_name,
                    "category": detected_category,
                    "category_label": CATEGORY_MAP.get(detected_category, detected_category),
                    "metric_name": "場站連線" if detected_category == "CONNECTIVITY" else "可用車位",
                    "current_value": val_raw,
                    "threshold": f"下限:{space_lower_raw} 上限:{space_upper_raw}" if detected_category == "CAPACITY" else "正常連線",
                    "details": anomaly_desc,
                    "update_time": update_time
                }
                anomalies.append(anomaly_item)
                newly_triggered.append(anomaly_item)

            except Exception as e:
                logger.error(f"Error filtering anomaly row: {row}, error: {e}")
                continue

        return anomalies, newly_triggered

    @staticmethod
    def map_anomalies_to_roles(
        anomalies: List[Dict[str, Any]], 
        tenant_permissions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        雙重隔離映射矩陣：
          第 1 重：比對 company_id 確保企業資料絕對隔離
          第 2 重：比對 subscribed_categories 確保角色職責精準分流 (甲收連線、乙收車位)
        """
        dispatch_plans = []

        for rule in tenant_permissions:
            cid = str(rule.get("company_id") or rule.get("tenant_id") or "").strip()
            cname = str(rule.get("company_name") or rule.get("tenant_name") or cid).strip()
            role_name = str(rule.get("role_name") or "管理員").strip()

            notify_enabled = str(rule.get("notify_enabled", "True")).lower() in ["true", "1", "yes"]
            if not notify_enabled:
                continue

            raw_cats = str(rule.get("subscribed_categories", "ALL")).replace(";", ",").upper()
            subscribed_cats = [c.strip() for c in raw_cats.split(",") if c.strip()]
            allow_all = "ALL" in subscribed_cats or len(subscribed_cats) == 0

            raw_ids = str(rule.get("authorized_line_user_ids", "")).replace(";", ",")
            user_ids = [uid.strip() for uid in raw_ids.split(",") if uid.strip()]
            if not user_ids:
                continue

            matched_anomalies = []
            for item in anomalies:
                item_cid = item.get("company_id", "")
                item_cat = item.get("category", "")

                company_matched = (cid == "ALL") or (cid == item_cid) or (cid in item_cid.split(","))
                category_matched = allow_all or (item_cat in subscribed_cats)

                if company_matched and category_matched:
                    matched_anomalies.append(item)

            if matched_anomalies:
                dispatch_plans.append({
                    "company_id": cid,
                    "company_name": cname,
                    "role_name": role_name,
                    "subscribed_categories": subscribed_cats,
                    "line_user_ids": user_ids,
                    "anomalies": matched_anomalies
                })

        return dispatch_plans

    @staticmethod
    def get_user_tenant_profile(user_id: str, tenant_permissions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        根據 LINE User ID 查詢其所屬企業與角色
        """
        clean_uid = str(user_id).strip()
        for rule in tenant_permissions:
            raw_ids = str(rule.get("authorized_line_user_ids", "")).replace(";", ",")
            uids = [u.strip() for u in raw_ids.split(",") if u.strip()]
            if clean_uid in uids:
                return {
                    "company_id": str(rule.get("company_id") or rule.get("tenant_id") or "").strip(),
                    "company_name": str(rule.get("company_name") or rule.get("tenant_name") or "").strip(),
                    "role_name": str(rule.get("role_name") or "管理員").strip(),
                    "authorized_line_user_ids": uids
                }
        return None

    @staticmethod
    def parse_and_execute_command(
        user_id: str,
        text: str,
        sheets_service: Any
    ) -> Dict[str, Any]:
        """
        解析並執行管理員指令:
          1. /? 或 /help -> 指令教學
          2. /查詢 或 /設定 -> 查詢轄下場站門檻
          3. /車位 [場站] [下限] [上限] -> 更新車位警報門檻
          4. /冷卻 [場站|全部] [車位|斷線] [6|12|18|24|36] -> 更新冷卻時數
        """
        raw_cmd = text.strip()
        permissions = sheets_service.fetch_tenant_permissions()
        profile = TenantService.get_user_tenant_profile(user_id, permissions)

        # 1. 教學指令 /?
        if raw_cmd in ["/?", "/help", "help", "?", "說明"]:
            return {
                "action": "help",
                "profile": profile,
                "user_id": user_id
            }

        # 檢查權限：除教學外的指令，必須具備所屬企業授權
        if not profile:
            return {
                "action": "unauthorized",
                "user_id": user_id,
                "error": f"您的 LINE User ID ({user_id}) 尚未被列入「多租戶權限表」，無權變更系統設定。"
            }

        company_id = profile["company_id"]
        company_name = profile["company_name"]

        # 2. 查詢指令 /查詢 或 /設定
        if raw_cmd in ["/查詢", "查詢", "/設定", "設定", "/list"]:
            settings = sheets_service.fetch_alert_settings(company_id=company_id)
            return {
                "action": "query",
                "company_name": company_name,
                "company_id": company_id,
                "settings": settings
            }

        # 3. 車位設定指令 /車位 [場站] [下限] [上限]
        # 範例: /車位 復興場 5 30 或 /車位 復興場 5 無 或 /車位 復興場 關閉
        if raw_cmd.startswith("/車位") or raw_cmd.startswith("車位"):
            parts = re.split(r"\s+", raw_cmd)
            if len(parts) < 3:
                return {
                    "action": "error",
                    "error": "車位指令格式錯誤！\n正確格式：\n/車位 [場站名稱] [下限] [上限]\n範例：/車位 復興場 5 30 或 /車位 復興場 5 無"
                }

            station_query = parts[1]
            lower_arg = parts[2]
            upper_arg = parts[3] if len(parts) >= 4 else "無"

            # 處理關閉
            if lower_arg in ["關閉", "停用", "OFF", "off"]:
                lower_arg = "無"
                upper_arg = "無"

            res = sheets_service.update_station_space_threshold(
                company_id=company_id,
                station_query=station_query,
                lower=lower_arg,
                upper=upper_arg
            )
            return {
                "action": "space_update",
                "company_name": company_name,
                "result": res
            }

        # 4. 冷卻設定指令 /冷卻 [場站|全部] [車位|斷線] [6|12|18|24|36]
        # 範例: /冷卻 復興場 車位 12 或 /冷卻 全部 斷線 24
        if raw_cmd.startswith("/冷卻") or raw_cmd.startswith("冷卻"):
            parts = re.split(r"\s+", raw_cmd)
            if len(parts) < 4:
                return {
                    "action": "error",
                    "error": "冷卻指令格式錯誤！\n正確格式：\n/冷卻 [場站名稱或全部] [車位|斷線] [6|12|18|24|36]\n範例：/冷卻 復興場 車位 12"
                }

            station_query = parts[1]
            alert_type_arg = parts[2]
            hours_str = parts[3]

            if not ("車位" in alert_type_arg or "斷線" in alert_type_arg):
                return {
                    "action": "error",
                    "error": f"警報種類錯誤：請指定「車位」或「斷線」，您輸入的是「{alert_type_arg}」。"
                }

            try:
                hours = int(hours_str)
                if hours not in [6, 12, 18, 24, 36]:
                    return {
                        "action": "error",
                        "error": f"冷卻時數僅支援 6、12、18、24 或 36 小時，您輸入的是「{hours}」。"
                    }
            except ValueError:
                return {
                    "action": "error",
                    "error": f"時數必須為整數 (6/12/18/24/36)，您輸入的是「{hours_str}」。"
                }

            res = sheets_service.update_station_cooldown(
                company_id=company_id,
                alert_type="車位" if "車位" in alert_type_arg else "斷線",
                hours=hours,
                station_query=station_query
            )
            return {
                "action": "cooldown_update",
                "company_name": company_name,
                "result": res
            }

        # 5. 自然語言對話解析 (方案 C：第 1 軌語意規則 + 第 2 軌 Gemini AI 雙軌引擎)
        settings = sheets_service.fetch_alert_settings(company_id=company_id)
        allowed_stations = [s.get("station_name") for s in settings if s.get("station_name")]

        nlp_res = NLPService.understand_user_message(raw_cmd, allowed_stations=allowed_stations)
        intent = nlp_res.get("intent")

        if intent == "help":
            return {
                "action": "help",
                "profile": profile,
                "user_id": user_id
            }
        elif intent == "query":
            return {
                "action": "query",
                "company_name": company_name,
                "company_id": company_id,
                "settings": settings
            }
        elif intent == "set_space":
            stn = nlp_res.get("station_name")
            low = nlp_res.get("lower", "無")
            up = nlp_res.get("upper", "無")
            res = sheets_service.update_station_space_threshold(
                company_id=company_id,
                station_query=stn,
                lower=low,
                upper=up
            )
            return {
                "action": "space_update",
                "company_name": company_name,
                "result": res,
                "is_nlp": True
            }
        elif intent == "set_cooldown":
            stn = nlp_res.get("station_name", "全部")
            atype = nlp_res.get("alert_type", "車位")
            hrs = nlp_res.get("cooldown_hours", 12)
            res = sheets_service.update_station_cooldown(
                company_id=company_id,
                alert_type=atype,
                hours=hrs,
                station_query=stn
            )
            return {
                "action": "cooldown_update",
                "company_name": company_name,
                "result": res,
                "is_nlp": True
            }

        # 6. 未知指令但已授權
        return {
            "action": "unknown",
            "user_id": user_id,
            "profile": profile,
            "text": text
        }
