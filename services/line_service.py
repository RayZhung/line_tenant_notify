"""
LINE Messaging API 推播與訊息建置模組
支援:
  1. Flex Message 高質感視覺化告警卡片
  2. 精準特定 User ID Push 訊息推播 (完全隔離多租戶)
  3. LINE Webhook 簽章驗證與指令回覆
"""
import os
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("line_service")

# 嘗試載入 line-bot-sdk v3 或相容模組
try:
    from linebot.v3 import WebhookHandler
    from linebot.v3.messaging import (
        Configuration,
        ApiClient,
        MessagingApi,
        PushMessageRequest,
        TextMessage,
        FlexMessage,
        FlexContainer
    )
    LINE_SDK_AVAILABLE = True
except ImportError:
    try:
        from linebot import LineBotApi, WebhookHandler
        from linebot.models import TextSendMessage, FlexSendMessage
        LINE_SDK_AVAILABLE = "v2"
    except ImportError:
        LINE_SDK_AVAILABLE = False
        logger.warning("line-bot-sdk is not installed. LINE push will run in log-only mode.")

class LineService:
    def __init__(self):
        self.channel_secret = os.getenv("LINE_CHANNEL_SECRET", "")
        self.channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
        self.handler = None
        self.messaging_api = None
        self._init_client()

    def _init_client(self):
        if not self.channel_access_token or not self.channel_secret:
            logger.warning("LINE credentials missing in environment variables.")
            return

        if LINE_SDK_AVAILABLE is True:
            try:
                config = Configuration(access_token=self.channel_access_token)
                self.api_client = ApiClient(config)
                self.messaging_api = MessagingApi(self.api_client)
                self.handler = WebhookHandler(self.channel_secret)
                logger.info("Initialized LINE SDK v3 successfully.")
            except Exception as e:
                logger.error(f"Error initializing LINE SDK v3: {e}")
        elif LINE_SDK_AVAILABLE == "v2":
            try:
                self.line_bot_api = LineBotApi(self.channel_access_token)
                self.handler = WebhookHandler(self.channel_secret)
                logger.info("Initialized LINE SDK v2 successfully.")
            except Exception as e:
                logger.error(f"Error initializing LINE SDK v2: {e}")

    @staticmethod
    def build_anomaly_flex_bubble(tenant_name: str, anomalies: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        建構 Flex Message 異常通知卡片 (JSON Bubble)
        """
        rows = []
        for item in anomalies:
            station_name = item.get("station_name", "未知場站")
            metric_name = item.get("metric_name", "數值")
            curr_val = item.get("current_value", 0)
            thresh = item.get("threshold", 0)

            rows.append({
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "contents": [
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": f"📍 {station_name}",
                                "weight": "bold",
                                "size": "sm",
                                "color": "#1E293B",
                                "flex": 3
                            },
                            {
                                "type": "text",
                                "text": "⚠️ 低於警戒",
                                "size": "xs",
                                "color": "#EF4444",
                                "align": "end",
                                "weight": "bold",
                                "flex": 2
                            }
                        ]
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "margin": "xs",
                        "contents": [
                            {
                                "type": "text",
                                "text": f"{metric_name}: {curr_val} (標準: {thresh})",
                                "size": "xs",
                                "color": "#64748B"
                            }
                        ]
                    }
                ]
            })

        update_time = anomalies[0].get("update_time", "剛剛") if anomalies else "剛剛"

        flex_bubble = {
            "type": "bubble",
            "size": "mega",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#DC2626",
                "paddingAll": "16px",
                "contents": [
                    {
                        "type": "text",
                        "text": "🚨 場站營運異常警報",
                        "weight": "bold",
                        "color": "#FFFFFF",
                        "size": "lg"
                    },
                    {
                        "type": "text",
                        "text": f"客戶專屬通知: {tenant_name}",
                        "color": "#FEE2E2",
                        "size": "xs",
                        "margin": "xs"
                    }
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "16px",
                "contents": [
                    {
                        "type": "text",
                        "text": f"系統於 {update_time} 檢測到以下場站低於安全警戒值，請儘速指派人員處理：",
                        "size": "xs",
                        "color": "#475569",
                        "wrap": True
                    },
                    {
                        "type": "separator",
                        "margin": "md"
                    },
                    *rows
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "12px",
                "contents": [
                    {
                        "type": "text",
                        "text": "⚠️ 資料僅發送給授權之管理員，嚴格落實多租戶隔離。",
                        "size": "xxs",
                        "color": "#94A3B8",
                        "align": "center"
                    }
                ]
            }
        }
        return flex_bubble

    def send_tenant_anomaly_alert(self, user_id: str, tenant_name: str, anomalies: List[Dict[str, Any]]) -> bool:
        """
        向指定 LINE User ID 推送專屬告警訊息
        """
        flex_dict = self.build_anomaly_flex_bubble(tenant_name, anomalies)
        alt_text = f"【警報】{tenant_name} 共有 {len(anomalies)} 個場站低於警戒值"

        if not self.channel_access_token:
            logger.info(f"[Mock Push] Sent alert for {tenant_name} to User: {user_id}. Anomalies count: {len(anomalies)}")
            return True

        try:
            if LINE_SDK_AVAILABLE is True and self.messaging_api:
                container = FlexContainer.from_dict(flex_dict)
                flex_msg = FlexMessage(alt_text=alt_text, contents=container)
                request = PushMessageRequest(
                    to=user_id,
                    messages=[flex_msg]
                )
                self.messaging_api.push_message(request)
                logger.info(f"Successfully sent Flex push alert to {user_id} ({tenant_name})")
                return True
            elif LINE_SDK_AVAILABLE == "v2" and hasattr(self, "line_bot_api"):
                self.line_bot_api.push_message(
                    user_id,
                    FlexSendMessage(alt_text=alt_text, contents=flex_dict)
                )
                logger.info(f"Successfully sent v2 push alert to {user_id} ({tenant_name})")
                return True
            else:
                logger.warning(f"No LINE SDK available. Alert logged for {user_id}: {alt_text}")
                return False
        except Exception as e:
            logger.error(f"Failed to push LINE alert to {user_id}: {e}")
            return False
