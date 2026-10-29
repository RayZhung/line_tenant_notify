"""
多租戶與角色權限隔離服務 (Tenant & Role RBAC Isolation Service)
核心原則:
  1. 企業授權碼驗證綁定 (首次使用強制輸入授權碼，比對車車戰情室企業名單)
  2. 管理者自主挑選監控場站 (必須在 site list 內，且每位管理者上限 <= 10 座)
  3. 企業資料絕對隔離 (公司 A 絕對看不到公司 B 的資料與場站)
  4. 個人化警報門檻與冷卻防洗版 (6, 12, 18, 24, 36 小時)
  5. 雙軌自然語言與指令解析 (/?、/可選場站、/新增、/移除、/查詢、/車位、/冷卻)
"""
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

from services.nlp_service import NLPService

logger = logging.getLogger("tenant_service")

CATEGORY_MAP = {
    "CONNECTIVITY": "🔌 場站連線狀態",
    "CAPACITY": "🚗 車流車位狀況"
}

class TenantService:
    @staticmethod
    def _extract_company_id(obj: Dict[str, Any], keys: Tuple[str, ...] = ("company_id", "tenant_id", "授權企業")) -> str:
        """
        安全提取 company_id，避免整數 0 被 or 判斷為 False 而轉成空字串
        """
        for k in keys:
            v = obj.get(k)
            if v is not None and str(v).strip() != "":
                return str(v).strip()
        return ""

    @staticmethod
    def filter_anomalies(
        operations_data: List[Dict[str, Any]],
        alert_settings: Optional[List[Dict[str, Any]]] = None,
        cooldown_history: Optional[Dict[Tuple[str, str], datetime]] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        篩選出當前異常的場站數據
        回傳: (active_anomalies, newly_triggered_for_cooldown)
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
                company_id = TenantService._extract_company_id(row)
                company_name = str(row.get("company_name") or row.get("公司名稱") or "").strip()
                category = str(row.get("category") or "").strip().upper()
                metric_name = str(row.get("metric_name") or "").strip()
                status_val = str(row.get("status") or "").strip().upper()
                update_time = str(row.get("update_time") or "剛剛").strip()

                # 忽略 HARDWARE (發票剩餘數量警告已依需求全面移除)
                if category == "HARDWARE" or "發票" in metric_name:
                    continue

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
                        if str(space_lower_raw).strip() not in ["無", "NONE", "", "-"]:
                            lower_thresh = float(space_lower_raw)
                            if cur_spaces < lower_thresh:
                                is_anomaly = True
                                anomaly_desc = f"剩餘車位僅 {int(cur_spaces)} 格 (低於警戒下限 {int(lower_thresh)} 格)"

                        if str(space_upper_raw).strip() not in ["無", "NONE", "", "-"]:
                            upper_thresh = float(space_upper_raw)
                            if cur_spaces > upper_thresh:
                                is_anomaly = True
                                anomaly_desc = f"剩餘車位達 {int(cur_spaces)} 格 (高於警戒上限 {int(upper_thresh)} 格)"
                    except (ValueError, TypeError):
                        pass

                if not is_anomaly or not detected_category:
                    continue

                # 3. 檢查全域冷卻時間
                history_key = (station_name, detected_category)
                last_triggered_at = cooldowns.get(history_key)
                if last_triggered_at:
                    elapsed = (now - last_triggered_at).total_seconds()
                    limit_sec = cooldown_hours * 3600
                    if elapsed < limit_sec:
                        remaining_min = int((limit_sec - elapsed) / 60)
                        logger.info(f"Suppressed {station_name} [{detected_category}] (Cooldown remaining: {remaining_min} mins)")
                        continue

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
    def map_anomalies_to_users(
        anomalies: List[Dict[str, Any]],
        all_subscriptions: List[Dict[str, Any]],
        tenant_permissions: List[Dict[str, Any]],
        cooldown_history: Optional[Dict[Tuple[str, str], datetime]] = None
    ) -> List[Dict[str, Any]]:
        """
        個人化精準派發：
        依據「個人監控場站表」比對各管理者 (LINE User ID) 自行挑選的 <= 10 座場站，
        並依據該使用者自訂的車位上下限與冷卻門檻執行派發。
        """
        now = datetime.now()
        cooldowns = cooldown_history or {}
        user_plans = []
        user_grouped = {}

        for sub in all_subscriptions:
            uid = str(sub.get("line_user_id", "")).strip()
            if uid:
                user_grouped.setdefault(uid, []).append(sub)

        for uid, subs in user_grouped.items():
            profile = TenantService.get_user_tenant_profile(uid, tenant_permissions)
            cname = profile.get("company_name", "微程式") if profile else "微程式"
            role_name = profile.get("role_name", "管理員") if profile else "管理員"

            user_station_map = {str(s.get("station_name", "")).strip(): s for s in subs}
            user_anomalies = []

            for item in anomalies:
                sname = str(item.get("station_name", "")).strip()
                if sname not in user_station_map:
                    continue

                sub_cfg = user_station_map[sname]
                cat = item.get("category", "")

                # 依該使用者在個人監控表自訂的冷卻時數判定
                if cat == "CONNECTIVITY":
                    cd_hours = float(sub_cfg.get("offline_cooldown", 24) or 24)
                else:
                    cd_hours = float(sub_cfg.get("space_cooldown", 6) or 6)

                history_key = (sname, cat)
                last_trig = cooldowns.get(history_key)
                if last_trig:
                    elapsed = (now - last_trig).total_seconds()
                    if elapsed < cd_hours * 3600:
                        continue

                user_anomalies.append(item)

            if user_anomalies:
                user_plans.append({
                    "line_user_id": uid,
                    "company_name": cname,
                    "role_name": role_name,
                    "anomalies": user_anomalies
                })

        return user_plans

    @staticmethod
    def map_anomalies_to_roles(
        anomalies: List[Dict[str, Any]], 
        tenant_permissions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        備援全域派發（若無個人訂閱清單時的退守方案）
        """
        dispatch_plans = []

        for rule in tenant_permissions:
            cid = TenantService._extract_company_id(rule)
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

                item_cids = [c.strip() for c in str(item_cid).split(",") if c.strip()]
                company_matched = (cid == "ALL") or (cid in item_cids)
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
                    "company_id": TenantService._extract_company_id(rule),
                    "company_name": str(rule.get("company_name") or rule.get("tenant_name") or "").strip(),
                    "role_name": str(rule.get("role_name") or "管理員").strip(),
                    "authorized_line_user_ids": uids
                }
        return None

    @staticmethod
    def _resolve_max_stations(company_id: str, sheets_service: Any) -> int:
        """
        動態解析該企業身分之個人場站監控上限 (超級管理者 50 場，一般企業讀取系統全域參數)
        """
        if str(company_id).strip() in ["0", "ROOT"]:
            return 50
        if hasattr(sheets_service, "get_effective_max_stations"):
            val = sheets_service.get_effective_max_stations(company_id)
            if isinstance(val, int):
                return val
        try:
            cfgs = sheets_service.fetch_system_configs()
            if isinstance(cfgs, dict):
                return int(cfgs.get("max_stations_per_user", 10))
        except Exception:
            pass
        return 10

    @staticmethod
    def parse_and_execute_command(
        user_id: str,
        text: str,
        sheets_service: Any
    ) -> Dict[str, Any]:
        """
        解析並執行管理員指令 (支援初次企業授權碼綁定、動態場站上限、候選清單選取、自然語言)
        """
        raw_cmd = text.strip()
        permissions = sheets_service.fetch_tenant_permissions()
        profile = TenantService.get_user_tenant_profile(user_id, permissions)

        # ----------------------------------------------------
        # 1. 尚未綁定身份的使用者 (必須先輸入企業授權碼)
        # ----------------------------------------------------
        if not profile:
            # 檢查是否輸入合法的企業授權碼
            bind_info = sheets_service.bind_user_to_company(user_id, raw_cmd)
            if bind_info:
                bound_cid = str(bind_info.get("company_id", "1")).strip()
                return {
                    "action": "auth_success",
                    "company_id": bound_cid,
                    "company_name": bind_info["company_name"],
                    "role_name": "系統超級管理者" if bound_cid in ["0", "ROOT"] else "管理員",
                    "user_id": user_id,
                    "max_count": TenantService._resolve_max_stations(bound_cid, sheets_service)
                }
            else:
                return {
                    "action": "auth_required",
                    "user_id": user_id,
                    "text": raw_cmd
                }

        # ----------------------------------------------------
        # 2. 已綁定身份的使用者 (正規化全形符號與斜線後空白，如 ／？、/ ? -> /?)
        # ----------------------------------------------------
        company_id = profile["company_id"]
        company_name = profile["company_name"]
        norm_cmd = raw_cmd.replace("／", "/").replace("？", "?").replace("　", " ").strip()
        if norm_cmd.startswith("/"):
            norm_cmd = "/" + norm_cmd[1:].lstrip()

        # (0) 重新綁定企業授權碼 /重新綁定 (方式 A 主動求助式)
        if norm_cmd in ["/重新綁定", "重新綁定", "/切換身分", "切換身分", "/重新輸入", "重新輸入", "/換企業", "/rebind"]:
            sheets_service.unbind_user(user_id)
            return {
                "action": "auth_required",
                "user_id": user_id,
                "text": raw_cmd
            }

        # (1) 教學手冊 /?
        if norm_cmd in ["/?", "/help", "help", "?", "說明", "功能"]:
            return {
                "action": "help",
                "profile": profile,
                "user_id": user_id,
                "max_count": TenantService._resolve_max_stations(company_id, sheets_service)
            }

        # (2) 列出可選候選場站 /可選場站
        if norm_cmd in ["/可選場站", "可選場站", "/場站列表", "場站列表", "/候選場站", "有哪些場站可以選", "選場站", "/sites"]:
            candidates = sheets_service.fetch_candidate_sites(company_id, user_id)
            current_subs = sheets_service.fetch_user_subscriptions(user_id)
            return {
                "action": "list_candidates",
                "company_name": company_name,
                "candidates": candidates,
                "current_count": len(current_subs),
                "max_count": TenantService._resolve_max_stations(company_id, sheets_service)
            }

        # (3) 新增場站至個人監控 /新增 [代號或名稱]
        if norm_cmd.startswith("/新增") or norm_cmd.startswith("新增") or norm_cmd.startswith("/加入") or norm_cmd.startswith("加入"):
            parts = re.split(r"\s+", norm_cmd, maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                return {
                    "action": "error",
                    "error": "請指定要新增的場站代號或名稱！\n例如：/新增 01 或 /新增 松山撫遠場"
                }
            query_arg = parts[1].strip()
            res = sheets_service.add_user_subscription(user_id, company_id, query_arg)
            return {
                "action": "add_subscription",
                "company_name": company_name,
                "result": res
            }

        # (4) 移除個人監控場站 /移除 [代號或名稱]
        if norm_cmd.startswith("/移除") or norm_cmd.startswith("移除") or norm_cmd.startswith("/刪除") or norm_cmd.startswith("刪除"):
            parts = re.split(r"\s+", norm_cmd, maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                return {
                    "action": "error",
                    "error": "請指定要移除的場站代號或名稱！\n例如：/移除 01 或 /移除 撫遠"
                }
            query_arg = parts[1].strip()
            res = sheets_service.remove_user_subscription(user_id, query_arg, company_id=company_id)
            return {
                "action": "remove_subscription",
                "company_name": company_name,
                "result": res
            }

        # (5) 查詢個人監控場站 /查詢 或 /設定
        if norm_cmd in ["/查詢", "查詢", "/設定", "設定", "/list", "我的場站", "/我的場站"]:
            subs = sheets_service.fetch_user_subscriptions(user_id)
            return {
                "action": "query",
                "company_name": company_name,
                "company_id": company_id,
                "settings": subs,
                "max_count": TenantService._resolve_max_stations(company_id, sheets_service)
            }

        # (6) 車位設定指令 /車位 [代號或場站] [下限] [上限]
        if norm_cmd.startswith("/車位") or norm_cmd.startswith("車位"):
            parts = re.split(r"\s+", norm_cmd)
            if len(parts) < 3:
                return {
                    "action": "error",
                    "error": "車位指令格式：\n/車位 [代號或名稱] [下限] [上限]\n例如：/車位 01 5 30 或 /車位 01 5 無"
                }

            station_query = parts[1]
            lower_arg = parts[2]
            upper_arg = parts[3] if len(parts) >= 4 else "無"

            if lower_arg in ["關閉", "停用", "OFF", "off"]:
                lower_arg = "無"
                upper_arg = "無"

            res = sheets_service.update_user_station_space(
                line_user_id=user_id,
                station_query=station_query,
                lower=lower_arg,
                upper=upper_arg
            )
            return {
                "action": "space_update",
                "company_name": company_name,
                "result": res
            }

        # (7) 冷卻設定指令 /冷卻 [代號或全部] [車位|斷線] [6|12|18|24|36]
        if norm_cmd.startswith("/冷卻") or norm_cmd.startswith("冷卻"):
            parts = re.split(r"\s+", norm_cmd)
            if len(parts) < 4:
                return {
                    "action": "error",
                    "error": "冷卻指令格式：\n/冷卻 [代號或全部] [車位|斷線] [6|12|18|24|36]\n例如：/冷卻 01 車位 12"
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

            res = sheets_service.update_user_station_cooldown(
                line_user_id=user_id,
                station_query=station_query,
                alert_type="車位" if "車位" in alert_type_arg else "斷線",
                hours=hours
            )
            return {
                "action": "cooldown_update",
                "company_name": company_name,
                "result": res
            }

        # (8) 超級管理者專屬控制指令: /靜音、/解除靜音、/參數
        is_super = (str(company_id).strip() in ["0", "ROOT"])

        if norm_cmd in ["/靜音", "靜音", "/全系統靜音", "全系統靜音", "/mute"]:
            if not is_super:
                return {
                    "action": "error",
                    "error": "⛔ 權限不足：僅有超級管理者 (企業代號 0) 可以啟動全系統靜音！"
                }
            sheets_service.update_system_config("maintenance_mode", "ON")
            return {
                "action": "system_mute",
                "message": "🔕 全系統靜音已啟動！\n系統已暫停所有場站的 LINE 異常警報推播（包含車位與斷線），以防超支或額度耗盡。\n\n如需恢復推播，請輸入「/解除靜音」。"
            }

        if norm_cmd in ["/解除靜音", "解除靜音", "/恢復通知", "恢復通知", "/unmute"]:
            if not is_super:
                return {
                    "action": "error",
                    "error": "⛔ 權限不足：僅有超級管理者 (企業代號 0) 可以解除全系統靜音！"
                }
            sheets_service.update_system_config("maintenance_mode", "OFF")
            return {
                "action": "system_unmute",
                "message": "🔔 全系統靜音已解除！\n系統已恢復常態異常巡檢與推播發送。"
            }

        if norm_cmd.startswith("/參數") or norm_cmd.startswith("參數") or norm_cmd.startswith("/config"):
            if not is_super:
                return {
                    "action": "error",
                    "error": "⛔ 權限不足：僅有超級管理者 (企業代號 0) 可以檢視或修改系統全域參數！"
                }
            parts = re.split(r"\s+", norm_cmd)
            if len(parts) == 1:
                configs = sheets_service.fetch_system_configs()
                return {
                    "action": "list_configs",
                    "configs": configs
                }
            
            if len(parts) < 3:
                return {
                    "action": "error",
                    "error": "參數修改指令格式：\n/參數 [參數名稱] [數值]\n例如：\n• /參數 最大場站數 15\n• /參數 警告門檻 190\n• /參數 靜音 ON"
                }
            
            p_key_raw = parts[1].strip()
            p_val = parts[2].strip()

            key_alias_map = {
                "最大場站數": "max_stations_per_user",
                "最大場站": "max_stations_per_user",
                "場站上限": "max_stations_per_user",
                "max_stations_per_user": "max_stations_per_user",
                "max_stations": "max_stations_per_user",
                "靜音": "maintenance_mode",
                "維護模式": "maintenance_mode",
                "maintenance_mode": "maintenance_mode",
                "推播計數": "monthly_push_count",
                "本月推播": "monthly_push_count",
                "monthly_push_count": "monthly_push_count",
                "警告門檻": "monthly_push_warning",
                "警戒門檻": "monthly_push_warning",
                "monthly_push_warning": "monthly_push_warning",
                "預設車位下限": "default_space_lower",
                "default_space_lower": "default_space_lower",
                "預設車位冷卻": "default_space_cooldown",
                "default_space_cooldown": "default_space_cooldown",
                "預設斷線冷卻": "default_offline_cooldown",
                "default_offline_cooldown": "default_offline_cooldown"
            }

            target_key = key_alias_map.get(p_key_raw, p_key_raw)
            if target_key == "maintenance_mode":
                p_val = "ON" if p_val.upper() in ["ON", "TRUE", "開啟", "啟動", "靜音"] else "OFF"

            ok = sheets_service.update_system_config(target_key, p_val)
            if ok:
                new_configs = sheets_service.fetch_system_configs()
                return {
                    "action": "config_updated",
                    "target_key": target_key,
                    "target_value": p_val,
                    "configs": new_configs
                }
            else:
                return {
                    "action": "error",
                    "error": f"更新全域參數「{p_key_raw}」失敗，請檢查試算表連線狀態。"
                }

        # (9) 自然語言對話解析 (方案 C 雙軌 AI 引擎)
        user_subs = sheets_service.fetch_user_subscriptions(user_id)
        user_station_names = [s.get("station_name") for s in user_subs if s.get("station_name")]

        nlp_res = NLPService.understand_user_message(norm_cmd, allowed_stations=user_station_names)
        intent = nlp_res.get("intent")
        eff_max = TenantService._resolve_max_stations(company_id, sheets_service)

        if intent == "help":
            return {"action": "help", "profile": profile, "user_id": user_id, "max_count": eff_max}
        elif intent == "query":
            return {"action": "query", "company_name": company_name, "company_id": company_id, "settings": user_subs, "max_count": eff_max}
        elif intent == "set_space":
            stn = nlp_res.get("station_name")
            low = nlp_res.get("lower", "無")
            up = nlp_res.get("upper", "無")
            res = sheets_service.update_user_station_space(
                line_user_id=user_id,
                station_query=stn,
                lower=low,
                upper=up
            )
            return {"action": "space_update", "company_name": company_name, "result": res, "is_nlp": True}
        elif intent == "set_cooldown":
            stn = nlp_res.get("station_name", "全部")
            atype = nlp_res.get("alert_type", "車位")
            hrs = nlp_res.get("cooldown_hours", 12)
            res = sheets_service.update_user_station_cooldown(
                line_user_id=user_id,
                station_query=stn,
                alert_type=atype,
                hours=hrs
            )
            return {"action": "cooldown_update", "company_name": company_name, "result": res, "is_nlp": True}

        # (10) 未知口語引導
        return {
            "action": "unknown",
            "user_id": user_id,
            "profile": profile,
            "text": text,
            "sub_count": len(user_subs),
            "max_count": eff_max
        }
