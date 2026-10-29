"""
LINE Messaging API 推播與訊息建置模組
支援:
  1. 多租戶 (Company) 與 角色 (Role 甲/乙) 專屬 Flex Message 高質感視覺化告警卡片
  2. 精準特定 User ID Push 訊息推播 (完全隔離多租戶與角色職責)
  3. LINE Webhook 簽章驗證與指令回覆 (發送任何訊息自動回覆 User ID)
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
                self._register_handlers()
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

    def _register_handlers(self):
        if not self.handler or not self.messaging_api:
            return

        try:
            from linebot.v3.webhooks import MessageEvent, TextMessageContent
            from linebot.v3.messaging import ReplyMessageRequest, TextMessage

            @self.handler.add(MessageEvent, message=TextMessageContent)
            def handle_text_message(event):
                user_id = getattr(event.source, "user_id", "UNKNOWN")
                reply_text = (
                    f"👋 您好！我是「場域訊息通報」機器人。\n\n"
                    f"您的 LINE User ID 為：\n👉 {user_id}\n\n"
                    f"💡 請將此 User ID 提供給系統管理者，填入「多租戶權限表」，"
                    f"即可接收您所屬企業與角色（甲：運維 / 乙：營運）之即時異常推播！"
                )
                try:
                    self.messaging_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=reply_text)]
                        )
                    )
                except Exception as e:
                    logger.error(f"Failed to reply message: {e}")
        except Exception as e:
            logger.warning(f"Could not register message handlers: {e}")

    @staticmethod
    def build_role_anomaly_flex_bubble(
        company_name: str, 
        role_name: str, 
        anomalies: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        建構多租戶角色專屬 Flex Message 異常通知卡片 (JSON Bubble)
        根據角色類別自動調整色系 (運維紅色系 / 營運靛紫色系)
        """
        # 判斷是否為設備運維類別 (甲角色)
        has_hardware_or_conn = any(
            item.get("category") in ["CONNECTIVITY", "HARDWARE"] 
            for item in anomalies
        ) or "運維" in role_name or "甲" in role_name

        header_bg = "#DC2626" if has_hardware_or_conn else "#4F46E5"
        header_title = "🚨 場站設備運維警報" if has_hardware_or_conn else "📊 場站營運車位警報"

        rows = []
        for item in anomalies:
            station_name = item.get("station_name", "未知場站")
            station_ip = item.get("station_ip", "-")
            category_label = item.get("category_label") or item.get("category", "異常")
            metric_name = item.get("metric_name", "數值")
            curr_val = item.get("current_value", 0)
            thresh = item.get("threshold", 0)
            details = item.get("details", "")

            row_contents = [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"📍 {station_name}",
                            "weight": "bold",
                            "size": "sm",
                            "color": "#0F172A",
                            "flex": 3
                        },
                        {
                            "type": "text",
                            "text": f"{category_label}",
                            "size": "xxs",
                            "color": "#EF4444" if has_hardware_or_conn else "#6366F1",
                            "align": "end",
                            "weight": "bold",
                            "flex": 2
                        }
                    ]
                }
            ]

            # 運維人員特別標註 IP
            if station_ip and station_ip != "-":
                row_contents.append({
                    "type": "text",
                    "text": f"🌐 IP位址: {station_ip}",
                    "size": "xxs",
                    "color": "#3B82F6",
                    "margin": "xs"
                })

            # 數值資訊
            row_contents.append({
                "type": "text",
                "text": f"⚠️ {metric_name}: {curr_val} (標準: {thresh})",
                "size": "xs",
                "color": "#334155",
                "margin": "xs",
                "weight": "bold"
            })

            # 詳細狀況
            if details:
                row_contents.append({
                    "type": "text",
                    "text": f"ℹ️ {details}",
                    "size": "xxs",
                    "color": "#64748B",
                    "margin": "xs",
                    "wrap": True
                })

            rows.append({
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "backgroundColor": "#F8FAFC",
                "cornerRadius": "8px",
                "paddingAll": "10px",
                "contents": row_contents
            })

        update_time = anomalies[0].get("update_time", "剛剛") if anomalies else "剛剛"

        flex_bubble = {
            "type": "bubble",
            "size": "mega",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": header_bg,
                "paddingAll": "16px",
                "contents": [
                    {
                        "type": "text",
                        "text": header_title,
                        "weight": "bold",
                        "color": "#FFFFFF",
                        "size": "lg"
                    },
                    {
                        "type": "text",
                        "text": f"🏢 企業: {company_name} ｜ 👤 對象: {role_name}",
                        "color": "#FEE2E2" if has_hardware_or_conn else "#E0E7FF",
                        "size": "xs",
                        "margin": "xs",
                        "weight": "bold"
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
                        "text": f"巡檢時間：{update_time}，檢測到以下異常項目，請儘速指派相關人員處理：",
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
                "paddingAll": "10px",
                "contents": [
                    {
                        "type": "text",
                        "text": "🔒 企業與角色雙重隔離保護・僅授權人員可見",
                        "size": "xxs",
                        "color": "#94A3B8",
                        "align": "center"
                    }
                ]
            }
        }
        return flex_bubble

    def send_role_anomaly_alert(
        self, 
        user_id: str, 
        company_name: str, 
        role_name: str, 
        anomalies: List[Dict[str, Any]]
    ) -> bool:
        """
        向指定 LINE User ID 推送專屬告警訊息 (支援角色客製卡片)
        """
        flex_dict = self.build_role_anomaly_flex_bubble(company_name, role_name, anomalies)
        alt_text = f"【警報】{company_name} ({role_name}) 共有 {len(anomalies)} 項場站異常"

        if not self.channel_access_token:
            logger.info(f"[Mock Push] Sent alert for {company_name} ({role_name}) to User: {user_id}. Anomalies count: {len(anomalies)}")
            return True

        try:
            if LINE_SDK_AVAILABLE is True and self.messaging_api:
                container = FlexContainer.from_json(json.dumps(flex_dict))
                flex_msg = FlexMessage(alt_text=alt_text, contents=container)
                request = PushMessageRequest(
                    to=user_id,
                    messages=[flex_msg]
                )
                self.messaging_api.push_message(request)
                logger.info(f"Successfully sent Flex push alert to {user_id} ({company_name} - {role_name})")
                return True
            elif LINE_SDK_AVAILABLE == "v2" and hasattr(self, "line_bot_api"):
                self.line_bot_api.push_message(
                    user_id,
                    FlexSendMessage(alt_text=alt_text, contents=flex_dict)
                )
                logger.info(f"Successfully sent v2 push alert to {user_id} ({company_name} - {role_name})")
                return True
            else:
                logger.warning(f"No LINE SDK available. Alert logged for {user_id}: {alt_text}")
                return False
        except Exception as e:
            logger.error(f"Failed to push LINE alert to {user_id}: {e}")
            return False

    def send_tenant_anomaly_alert(self, user_id: str, tenant_name: str, anomalies: List[Dict[str, Any]]) -> bool:
        """相容舊版介面"""
        return self.send_role_anomaly_alert(user_id, tenant_name, "管理員", anomalies)
