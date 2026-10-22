"""
多租戶權限與資料隔離服務 (Tenant Isolation Service)
核心原則: 確保不同客戶間的營業與異常數據絕對隔離
"""
import logging
from typing import List, Dict, Any

logger = logging.getLogger("tenant_service")

class TenantService:
    @staticmethod
    def filter_anomalies(operations_data: List[Dict[str, Any]], default_threshold: float = 10.0) -> List[Dict[str, Any]]:
        """
        篩選出當前低於警戒標準的異常場站資料
        """
        anomalies = []
        for row in operations_data:
            try:
                # 取得數值與警戒值
                val_raw = row.get("current_value") or row.get("metric_value", 0)
                current_val = float(val_raw)

                thresh_raw = row.get("threshold", default_threshold)
                threshold = float(thresh_raw) if thresh_raw else default_threshold

                # 判斷是否異常 (低於警戒值)
                if current_val < threshold:
                    anomalies.append({
                        "station_id": str(row.get("station_id", "UNKNOWN")),
                        "station_name": str(row.get("station_name", "未命名場站")),
                        "tenant_id": str(row.get("tenant_id", "")).strip(),
                        "metric_name": str(row.get("metric_name", "指標數值")),
                        "current_value": current_val,
                        "threshold": threshold,
                        "gap": round(threshold - current_val, 2),
                        "update_time": str(row.get("update_time", "剛剛"))
                    })
            except (ValueError, TypeError) as e:
                logger.error(f"Error parsing row data for anomaly detection: {row}, error: {e}")
                continue

        return anomalies

    @staticmethod
    def map_anomalies_to_tenants(
        anomalies: List[Dict[str, Any]], 
        tenant_permissions: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        多租戶隔離映射：
        將異常場站比對權限表，僅歸納入各自客戶群組，並綁定專屬的 LINE User ID 列表。
        
        回傳結構:
        {
          "TENANT_A": {
            "tenant_name": "優質車行股份有限公司",
            "line_user_ids": ["U12345...", "U67890..."],
            "anomalies": [...]
          },
          ...
        }
        """
        # 1. 建立租戶權限索引
        tenant_map = {}
        for tp in tenant_permissions:
            tid = str(tp.get("tenant_id", "")).strip()
            if not tid:
                continue

            # 檢查是否啟用通知
            notify_enabled = str(tp.get("notify_enabled", "True")).lower() in ["true", "1", "yes"]
            if not notify_enabled:
                continue

            # 解析 LINE User IDs (逗號或分號分隔)
            raw_ids = str(tp.get("authorized_line_user_ids", "")).replace(";", ",")
            id_list = [uid.strip() for uid in raw_ids.split(",") if uid.strip()]

            tenant_map[tid] = {
                "tenant_name": str(tp.get("tenant_name", tid)),
                "line_user_ids": id_list,
                "anomalies": []
            }

        # 2. 依據 tenant_id 分流異常，實踐絕對資料隔離
        for anomaly in anomalies:
            tid = anomaly.get("tenant_id", "")
            if tid in tenant_map:
                tenant_map[tid]["anomalies"].append(anomaly)
            else:
                logger.warning(f"Anomaly detected for unmapped or unauthorized tenant_id: '{tid}' (Station: {anomaly.get('station_name')})")

        # 3. 僅保留有異常場站且有綁定 LINE User ID 的租戶
        dispatch_targets = {
            tid: data 
            for tid, data in tenant_map.items() 
            if len(data["anomalies"]) > 0 and len(data["line_user_ids"]) > 0
        }

        return dispatch_targets
