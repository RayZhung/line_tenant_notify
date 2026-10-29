"""
多租戶與角色權限隔離服務 (Tenant & Role RBAC Isolation Service)
核心原則:
  1. 企業資料絕對隔離 (公司 A 絕對看不到公司 B 的資料)
  2. 角色職責精準分流 (運維人員【甲】只收設備/連線，營運人員【乙】只收車位/營收)
"""
import logging
from typing import List, Dict, Any

logger = logging.getLogger("tenant_service")

# 異常類別定義
CATEGORY_MAP = {
    "CONNECTIVITY": "🔌 場站連線狀態",
    "HARDWARE": "🖨️ 設備硬體消耗",
    "CAPACITY": "🚗 車流車位狀況",
    "REVENUE": "💰 營收交易異常"
}

class TenantService:
    @staticmethod
    def filter_anomalies(operations_data: List[Dict[str, Any]], default_threshold: float = 10.0) -> List[Dict[str, Any]]:
        """
        篩選出當前異常的場站數據
        支援兩種判斷：
          1. status 欄位明確為 ANOMALY / 異常
          2. current_value 數值低於 threshold 門檻
        """
        anomalies = []
        for row in operations_data:
            try:
                station_name = str(row.get("station_name") or row.get("停車場名稱") or "未知場站").strip()
                station_ip = str(row.get("station_ip") or row.get("IP位址") or row.get("IP") or "-").strip()
                company_id = str(row.get("company_id") or row.get("tenant_id") or row.get("授權企業") or "").strip()
                company_name = str(row.get("company_name") or row.get("公司名稱") or "").strip()
                category = str(row.get("category") or "HARDWARE").strip().upper()
                metric_name = str(row.get("metric_name") or "指標項目").strip()
                details = str(row.get("details") or "").strip()
                status_val = str(row.get("status") or "").strip().upper()
                update_time = str(row.get("update_time") or "剛剛").strip()

                val_raw = row.get("current_value") or row.get("metric_value", 0)
                thresh_raw = row.get("threshold", default_threshold)

                # 判斷是否為異常
                is_anomaly = False
                current_val = val_raw
                threshold = thresh_raw

                if status_val in ["ANOMALY", "ABNORMAL", "ERROR", "OFFLINE", "異常"]:
                    is_anomaly = True
                else:
                    # 數值比對判斷
                    try:
                        c_float = float(val_raw)
                        t_float = float(thresh_raw)
                        current_val = c_float
                        threshold = t_float
                        if c_float < t_float:
                            is_anomaly = True
                    except (ValueError, TypeError):
                        # 字串比對 (例如狀態不符)
                        if str(val_raw).strip().upper() != str(thresh_raw).strip().upper():
                            is_anomaly = True

                if is_anomaly:
                    anomalies.append({
                        "station_name": station_name,
                        "station_ip": station_ip,
                        "company_id": company_id,
                        "company_name": company_name,
                        "category": category,
                        "category_label": CATEGORY_MAP.get(category, category),
                        "metric_name": metric_name,
                        "current_value": current_val,
                        "threshold": threshold,
                        "details": details,
                        "update_time": update_time
                    })
            except Exception as e:
                logger.error(f"Error filtering anomaly row: {row}, error: {e}")
                continue

        return anomalies

    @staticmethod
    def map_anomalies_to_roles(
        anomalies: List[Dict[str, Any]], 
        tenant_permissions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        雙重隔離映射矩陣：
          第 1 重：比對 company_id 確保企業資料絕對隔離
          第 2 重：比對 subscribed_categories 確保角色職責精準分流 (甲收甲的、乙收乙的)
        
        回傳結構:
        [
          {
            "company_id": "1",
            "company_name": "微程式",
            "role_name": "設備運維 (甲)",
            "line_user_ids": ["U42607..."],
            "anomalies": [...]
          },
          ...
        ]
        """
        dispatch_plans = []

        for rule in tenant_permissions:
            cid = str(rule.get("company_id") or rule.get("tenant_id") or "").strip()
            cname = str(rule.get("company_name") or rule.get("tenant_name") or cid).strip()
            role_name = str(rule.get("role_name") or "管理員").strip()

            notify_enabled = str(rule.get("notify_enabled", "True")).lower() in ["true", "1", "yes"]
            if not notify_enabled:
                continue

            # 解析訂閱的類別 (逗號或分號分隔)
            raw_cats = str(rule.get("subscribed_categories", "ALL")).replace(";", ",").upper()
            subscribed_cats = [c.strip() for c in raw_cats.split(",") if c.strip()]
            allow_all = "ALL" in subscribed_cats or len(subscribed_cats) == 0

            # 解析授權的 LINE User IDs
            raw_ids = str(rule.get("authorized_line_user_ids", "")).replace(";", ",")
            user_ids = [uid.strip() for uid in raw_ids.split(",") if uid.strip()]
            if not user_ids:
                continue

            # 篩選同時滿足「公司相符」與「類別相符」的異常項目
            matched_anomalies = []
            for item in anomalies:
                item_cid = item.get("company_id", "")
                item_cat = item.get("category", "")

                # 1. 公司相符判定 (支援單一編號或如 '1,2' 的授權字串)
                company_matched = (cid == "ALL") or (cid == item_cid) or (cid in item_cid.split(","))

                # 2. 角色類別相符判定
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
