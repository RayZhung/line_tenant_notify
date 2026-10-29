"""
LINE Messaging API 推播與訊息建置模組
支援:
  1. 多租戶 (Company) 與 角色 (Role 甲/乙) 專屬 Flex Message 視覺化告警卡片
  2. 管理員互動指令 (/? 指令手冊、/查詢 門檻設定、/車位 門檻調整、/冷卻 間隔設定)
  3. 精準特定 User ID Push 訊息推播 (完全隔離多租戶與角色職責)
  4. LINE Webhook 簽章驗證與雙向訊息回覆
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
        ReplyMessageRequest,
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

    def reply_flex(self, reply_token: str, flex_dict: Dict[str, Any], alt_text: str = "訊息回覆") -> bool:
        """
        使用 reply_token 回覆 Flex Message
        """
        if not self.channel_access_token:
            logger.info(f"[Mock Reply Flex] {alt_text}")
            return True

        try:
            if LINE_SDK_AVAILABLE is True and self.messaging_api:
                container = FlexContainer.from_json(json.dumps(flex_dict))
                flex_msg = FlexMessage(alt_text=alt_text, contents=container)
                self.messaging_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[flex_msg]
                    )
                )
                return True
            elif LINE_SDK_AVAILABLE == "v2" and hasattr(self, "line_bot_api"):
                self.line_bot_api.reply_message(
                    reply_token,
                    FlexSendMessage(alt_text=alt_text, contents=flex_dict)
                )
                return True
        except Exception as e:
            logger.error(f"Failed to reply Flex message: {e}")
            return False

    def reply_text(self, reply_token: str, text: str) -> bool:
        """
        使用 reply_token 回覆純文字訊息
        """
        if not self.channel_access_token:
            logger.info(f"[Mock Reply Text] {text}")
            return True

        try:
            if LINE_SDK_AVAILABLE is True and self.messaging_api:
                self.messaging_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(text=text)]
                    )
                )
                return True
            elif LINE_SDK_AVAILABLE == "v2" and hasattr(self, "line_bot_api"):
                self.line_bot_api.reply_message(
                    reply_token,
                    TextSendMessage(text=text)
                )
                return True
        except Exception as e:
            logger.error(f"Failed to reply text: {e}")
            return False

    @staticmethod
    def build_help_flex_bubble(user_id: str, profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        建構 /? 指令教學手冊 Flex Message 卡片
        """
        company_name = profile.get("company_name", "未授權企業") if profile else "未授權企業"
        role_name = profile.get("role_name", "訪客") if profile else "訪客"

        bubble = {
            "type": "bubble",
            "size": "mega",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#1E293B",
                "paddingAll": "16px",
                "contents": [
                    {
                        "type": "text",
                        "text": "📖 場站管理員指令手冊",
                        "weight": "bold",
                        "color": "#FFFFFF",
                        "size": "lg"
                    },
                    {
                        "type": "text",
                        "text": f"🏢 企業: {company_name} ｜ 👤 角色: {role_name}",
                        "color": "#94A3B8",
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
                        "text": "您可直接在對話框輸入以下指令進行管理：",
                        "size": "xs",
                        "color": "#475569",
                        "wrap": True
                    },
                    {"type": "separator", "margin": "sm"},
                    # 指令 1: 查詢
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": "1️⃣ 查詢場站設定",
                                "weight": "bold",
                                "size": "sm",
                                "color": "#0F172A"
                            },
                            {
                                "type": "text",
                                "text": "👉 /查詢 或 /設定",
                                "size": "xs",
                                "color": "#2563EB",
                                "weight": "bold",
                                "margin": "xs"
                            },
                            {
                                "type": "text",
                                "text": "列出您所屬公司所有場站之車位上下限與冷卻時數。",
                                "size": "xxs",
                                "color": "#64748B",
                                "wrap": True
                            }
                        ]
                    },
                    {"type": "separator", "margin": "sm"},
                    # 指令 2: 車位門檻
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": "2️⃣ 車位警報門檻設定",
                                "weight": "bold",
                                "size": "sm",
                                "color": "#0F172A"
                            },
                            {
                                "type": "text",
                                "text": "👉 /車位 [場站名稱] [下限] [上限]",
                                "size": "xs",
                                "color": "#2563EB",
                                "weight": "bold",
                                "margin": "xs"
                            },
                            {
                                "type": "text",
                                "text": "• 例：/車位 復興場 5 30 (低於5格或高於30格警報)\n• 例：/車位 復興場 5 無 (僅設下限5格，上限不設)\n• 例：/車位 復興場 關閉 (停用該場車位警報)",
                                "size": "xxs",
                                "color": "#64748B",
                                "wrap": True
                            }
                        ]
                    },
                    {"type": "separator", "margin": "sm"},
                    # 指令 3: 冷卻時間
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": "3️⃣ 警報冷卻防洗版設定",
                                "weight": "bold",
                                "size": "sm",
                                "color": "#0F172A"
                            },
                            {
                                "type": "text",
                                "text": "👉 /冷卻 [場站或全部] [車位|斷線] [時數]",
                                "size": "xs",
                                "color": "#2563EB",
                                "weight": "bold",
                                "margin": "xs"
                            },
                            {
                                "type": "text",
                                "text": "支援時數：6, 12, 18, 24, 36 小時\n預設值：車位 6 小時、斷線 24 小時\n• 例：/冷卻 復興場 車位 12\n• 例：/冷卻 全部 斷線 36",
                                "size": "xxs",
                                "color": "#64748B",
                                "wrap": True
                            }
                        ]
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "12px",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#2563EB",
                        "height": "sm",
                        "action": {
                            "type": "message",
                            "label": "📋 查詢我的場站設定",
                            "text": "/查詢"
                        }
                    },
                    {
                        "type": "text",
                        "text": f"您的 LINE User ID:\n{user_id}",
                        "size": "xxs",
                        "color": "#94A3B8",
                        "align": "center",
                        "margin": "sm",
                        "wrap": True
                    }
                ]
            }
        }
        return bubble

    @staticmethod
    def build_query_settings_flex_bubble(company_name: str, settings: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        建構 /查詢 結果卡片
        """
        rows = []
        for s in settings[:12]:  # LINE Flex 限制每卡片內容數量，超過可分頁
            sname = s.get("station_name", "未知")
            s_low = s.get("space_lower", "5")
            s_up = s.get("space_upper", "無")
            s_cool = s.get("space_cooldown", "6")
            off_cool = s.get("offline_cooldown", "24")

            rows.append({
                "type": "box",
                "layout": "vertical",
                "margin": "sm",
                "paddingAll": "8px",
                "backgroundColor": "#F8FAFC",
                "cornerRadius": "6px",
                "contents": [
                    {
                        "type": "text",
                        "text": f"🅿️ {sname}",
                        "weight": "bold",
                        "size": "sm",
                        "color": "#0F172A"
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "margin": "xs",
                        "contents": [
                            {
                                "type": "text",
                                "text": f"車位門檻: 下限 {s_low} / 上限 {s_up}",
                                "size": "xs",
                                "color": "#475569",
                                "flex": 3
                            },
                            {
                                "type": "text",
                                "text": f"冷卻: {s_cool}h",
                                "size": "xs",
                                "color": "#2563EB",
                                "align": "end",
                                "flex": 1
                            }
                        ]
                    },
                    {
                        "type": "text",
                        "text": f"斷線冷卻: {off_cool} 小時",
                        "size": "xxs",
                        "color": "#64748B",
                        "margin": "xxs"
                    }
                ]
            })

        bubble = {
            "type": "bubble",
            "size": "mega",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#0284C7",
                "paddingAll": "16px",
                "contents": [
                    {
                        "type": "text",
                        "text": "📋 轄下場站警報設定總覽",
                        "weight": "bold",
                        "color": "#FFFFFF",
                        "size": "lg"
                    },
                    {
                        "type": "text",
                        "text": f"🏢 企業: {company_name} ｜ 共 {len(settings)} 個場站",
                        "color": "#E0F2FE",
                        "size": "xs",
                        "margin": "xs",
                        "weight": "bold"
                    }
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "14px",
                "contents": [
                    {
                        "type": "text",
                        "text": f"以下為目前生效中的車位門檻與防洗版冷卻設定 (顯示前 {min(len(settings), 12)} 場)：",
                        "size": "xs",
                        "color": "#64748B",
                        "wrap": True
                    },
                    {"type": "separator", "margin": "sm"},
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
                        "text": "💡 提示：輸入 /? 可隨時查看調整門檻與冷卻時數之指令。",
                        "size": "xxs",
                        "color": "#94A3B8",
                        "align": "center"
                    }
                ]
            }
        }
        return bubble

    @staticmethod
    def build_command_result_flex_bubble(
        title: str,
        message: str,
        details: List[Dict[str, str]],
        is_success: bool = True
    ) -> Dict[str, Any]:
        """
        建構指令執行結果回饋卡片
        """
        header_color = "#16A34A" if is_success else "#DC2626"
        icon = "✅" if is_success else "❌"

        detail_boxes = []
        for d in details:
            detail_boxes.append({
                "type": "box",
                "layout": "horizontal",
                "margin": "xs",
                "contents": [
                    {
                        "type": "text",
                        "text": d.get("label", ""),
                        "size": "xs",
                        "color": "#64748B",
                        "flex": 2
                    },
                    {
                        "type": "text",
                        "text": d.get("value", ""),
                        "size": "xs",
                        "weight": "bold",
                        "color": "#0F172A",
                        "flex": 3
                    }
                ]
            })

        bubble = {
            "type": "bubble",
            "size": "mega",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": header_color,
                "paddingAll": "14px",
                "contents": [
                    {
                        "type": "text",
                        "text": f"{icon} {title}",
                        "weight": "bold",
                        "color": "#FFFFFF",
                        "size": "md"
                    }
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "14px",
                "contents": [
                    {
                        "type": "text",
                        "text": message,
                        "size": "xs",
                        "color": "#334155",
                        "wrap": True
                    },
                    {"type": "separator", "margin": "sm"},
                    *detail_boxes
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "10px",
                "contents": [
                    {
                        "type": "button",
                        "style": "secondary",
                        "height": "sm",
                        "action": {
                            "type": "message",
                            "label": "📋 查詢最新設定",
                            "text": "/查詢"
                        }
                    }
                ]
            }
        }
        return bubble

    @staticmethod
    def build_role_anomaly_flex_bubble(
        company_name: str, 
        role_name: str, 
        anomalies: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        建構多租戶角色專屬 Flex Message 異常通知卡片
        甲角色 (運維): 突出 IP 位址與斷線警報 (紅黑警戒色)
        乙角色 (營運): 突出車位數量與上下限門檻 (藍靛色)
        """
        has_connectivity = any(item.get("category") == "CONNECTIVITY" for item in anomalies)
        header_bg = "#DC2626" if has_connectivity else "#4F46E5"
        header_title = "🚨 場站斷線維修警報" if has_connectivity else "🚗 可用車位異常警報"

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
                            "color": "#EF4444" if item.get("category") == "CONNECTIVITY" else "#6366F1",
                            "align": "end",
                            "weight": "bold",
                            "flex": 2
                        }
                    ]
                }
            ]

            # 運維人員特別標註 IP 位址
            if station_ip and station_ip != "-":
                row_contents.append({
                    "type": "text",
                    "text": f"🌐 IP位址: {station_ip}",
                    "size": "xxs",
                    "color": "#2563EB",
                    "margin": "xs",
                    "weight": "bold"
                })

            # 數值資訊
            row_contents.append({
                "type": "text",
                "text": f"⚠️ {metric_name}: {curr_val} (門檻: {thresh})",
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
                "paddingAll": "10px",
                "backgroundColor": "#F8FAFC",
                "cornerRadius": "6px",
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
                        "color": "#FEE2E2" if has_connectivity else "#E0E7FF",
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
                        "text": "🔒 企業與角色雙重隔離保護・冷卻防洗版中",
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
        向指定 LINE User ID 推送專屬告警訊息
        """
        if not anomalies:
            return True

        flex_dict = self.build_role_anomaly_flex_bubble(company_name, role_name, anomalies)
        alt_text = f"【警報】{company_name} ({role_name}) 共有 {len(anomalies)} 項場站異常"

        if not self.channel_access_token:
            logger.info(f"[Mock Push] Sent alert for {company_name} ({role_name}) to User: {user_id}. Count: {len(anomalies)}")
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
