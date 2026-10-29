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
    def build_auth_request_flex_bubble(user_id: str) -> Dict[str, Any]:
        """
        建構未授權使用者提示輸入「企業授權碼」的 Flex 卡片
        """
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
                        "text": "🔒 歡迎加入場站監控中心",
                        "weight": "bold",
                        "color": "#FFFFFF",
                        "size": "lg"
                    },
                    {
                        "type": "text",
                        "text": "初次使用身分驗證",
                        "color": "#94A3B8",
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
                        "text": "為了落實 ISO27001 企業資安隔離與資料保護，請在對話框中直接輸入您的【企業授權碼】以完成綁定：",
                        "size": "sm",
                        "color": "#334155",
                        "wrap": True
                    },
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "md",
                        "paddingAll": "10px",
                        "backgroundColor": "#F1F5F9",
                        "cornerRadius": "6px",
                        "contents": [
                            {
                                "type": "text",
                                "text": "💡 授權碼說明：",
                                "size": "xs",
                                "weight": "bold",
                                "color": "#475569"
                            },
                            {
                                "type": "text",
                                "text": "請向貴公司系統管理員索取專屬授權代碼，直接於此對話框中輸入（虛擬格式範例：sample_auth_888，英文大小寫需相符）。",
                                "size": "xxs",
                                "color": "#64748B",
                                "wrap": True,
                                "margin": "xs"
                            }
                        ]
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "10px",
                "contents": [
                    {
                        "type": "text",
                        "text": f"您的 LINE User ID:\n{user_id}",
                        "size": "xxs",
                        "color": "#94A3B8",
                        "align": "center",
                        "wrap": True
                    }
                ]
            }
        }
        return bubble

    @staticmethod
    def build_auth_success_flex_bubble(company_name: str, role_name: str, user_id: str) -> Dict[str, Any]:
        """
        建構授權綁定成功的歡迎 Flex 卡片
        """
        bubble = {
            "type": "bubble",
            "size": "mega",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#16A34A",
                "paddingAll": "16px",
                "contents": [
                    {
                        "type": "text",
                        "text": "🎉 企業身份綁定成功！",
                        "weight": "bold",
                        "color": "#FFFFFF",
                        "size": "lg"
                    },
                    {
                        "type": "text",
                        "text": f"🏢 授權企業: {company_name} ｜ 👤 角色: {role_name}",
                        "color": "#DCFCE7",
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
                        "text": f"歡迎您！您已獲取【{company_name}】之場站管理權限。每個管理者最高可自主挑選 10 座場站 加入個人監控：",
                        "size": "xs",
                        "color": "#334155",
                        "wrap": True
                    },
                    {"type": "separator", "margin": "md"},
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": "🚀 快速開始指南：",
                                "size": "sm",
                                "weight": "bold",
                                "color": "#0F172A"
                            },
                            {
                                "type": "text",
                                "text": "1. 輸入 /可選場站 瀏覽可加入的名單\n2. 輸入 /新增 01 將代號 01 場站加入監控\n3. 輸入 /車位 01 5 30 調整車位警報標準\n4. 隨時輸入 /? 查看所有指令手冊",
                                "size": "xs",
                                "color": "#475569",
                                "wrap": True,
                                "margin": "xs"
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
                        "color": "#16A34A",
                        "height": "sm",
                        "action": {
                            "type": "message",
                            "label": "📋 查看可加入的場站",
                            "text": "/可選場站"
                        }
                    }
                ]
            }
        }
        return bubble

    @staticmethod
    def build_candidate_sites_flex_bubble(
        company_name: str,
        candidates: List[Dict[str, Any]],
        current_count: int,
        max_count: int = 10
    ) -> Dict[str, Any]:
        """
        建構 /可選場站 清單卡片
        """
        rows = []
        for c in candidates[:15]:  # 顯示前 15 場
            code = c.get("station_code", "00")
            sname = c.get("station_name", "未知")
            is_sub = c.get("is_subscribed", False)

            status_tag = "已監控" if is_sub else "可新增"
            tag_color = "#94A3B8" if is_sub else "#16A34A"

            rows.append({
                "type": "box",
                "layout": "horizontal",
                "margin": "sm",
                "paddingAll": "6px",
                "backgroundColor": "#F8FAFC",
                "cornerRadius": "4px",
                "contents": [
                    {
                        "type": "text",
                        "text": f"[{code}] {sname}",
                        "size": "xs",
                        "weight": "bold",
                        "color": "#0F172A",
                        "flex": 4
                    },
                    {
                        "type": "text",
                        "text": status_tag,
                        "size": "xxs",
                        "color": tag_color,
                        "weight": "bold",
                        "align": "end",
                        "flex": 1
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
                        "text": "🏢 可選監控場站清單",
                        "weight": "bold",
                        "color": "#FFFFFF",
                        "size": "lg"
                    },
                    {
                        "type": "text",
                        "text": f"所屬企業: {company_name} ｜ 目前監控配額: {current_count} / {max_count} 場",
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
                        "text": f"請挑選您要監控的場站代號，輸入「/新增 代號」即可完成綁定：",
                        "size": "xs",
                        "color": "#475569",
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
                        "text": "💡 範例：輸入「/新增 01」或「/移除 01」",
                        "size": "xxs",
                        "color": "#64748B",
                        "align": "center"
                    }
                ]
            }
        }
        return bubble

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
                    # 指令 1: 選站與新增
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": "1️⃣ 挑選與新增監控場站 (上限 10 場)",
                                "weight": "bold",
                                "size": "sm",
                                "color": "#0F172A"
                            },
                            {
                                "type": "text",
                                "text": "👉 /可選場站：列出所有可加入的場站\n👉 /新增 [代號或名稱]：加入個人監控 (例：/新增 01)\n👉 /移除 [代號或名稱]：取消監控 (例：/移除 01)",
                                "size": "xs",
                                "color": "#2563EB",
                                "weight": "bold",
                                "margin": "xs",
                                "wrap": True
                            }
                        ]
                    },
                    {"type": "separator", "margin": "sm"},
                    # 指令 2: 查詢
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": "2️⃣ 查詢我的監控清單",
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
                                "text": "查看您正在監控的場站 (X/10 場) 與各場即時門檻。",
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
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": "4️⃣ 警報冷卻防洗版設定",
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
    def build_query_settings_flex_bubble(company_name: str, settings: List[Dict[str, Any]], max_count: int = 10) -> Dict[str, Any]:
        """
        建構 /查詢 個人監控場站卡片 (上限 10 場)
        """
        rows = []
        if not settings:
            rows.append({
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "paddingAll": "12px",
                "backgroundColor": "#F8FAFC",
                "cornerRadius": "6px",
                "contents": [
                    {
                        "type": "text",
                        "text": "您目前尚未加入任何監控場站（最多可自選 10 座）。",
                        "size": "xs",
                        "color": "#64748B",
                        "wrap": True
                    },
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#0284C7",
                        "height": "sm",
                        "margin": "md",
                        "action": {
                            "type": "message",
                            "label": "📋 查看可選場站清單",
                            "text": "/可選場站"
                        }
                    }
                ]
            })
        else:
            for s in settings[:10]:
                code = s.get("station_code") or ""
                code_prefix = f"[{code}] " if code else ""
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
                            "text": f"🅿️ {code_prefix}{sname}",
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
                        "text": "📋 我的個人監控場站清單",
                        "weight": "bold",
                        "color": "#FFFFFF",
                        "size": "lg"
                    },
                    {
                        "type": "text",
                        "text": f"🏢 企業: {company_name} ｜ 監控中: {len(settings)} / {max_count} 座場站",
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
                        "text": f"以下為您自選監控的場站與告警門檻 (上限 {max_count} 場)：",
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

    @staticmethod
    def build_emergency_push_warning_flex_bubble(
        current_count: int,
        warning_threshold: int = 195,
        max_limit: int = 200
    ) -> Dict[str, Any]:
        """
        建構每月推播則數瀕臨 195 則上限的超級管理者緊急告警卡片 (附一鍵靜音按鈕)
        """
        bubble = {
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
                        "text": "🚨 LINE 推播用量瀕危警告",
                        "weight": "bold",
                        "color": "#FFFFFF",
                        "size": "lg"
                    },
                    {
                        "type": "text",
                        "text": f"⚠️ 本月累積推播已達 {current_count} / {max_limit} 則！",
                        "color": "#FEF2F2",
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
                        "text": "本系統運作於 LINE 官方帳號免費方案 (Light Plan)，每月免費推播訊息上限為 200 則。為避免衍生超額扣款或額度耗盡，系統特此發出高警戒通知！",
                        "size": "xs",
                        "color": "#475569",
                        "wrap": True
                    },
                    {"type": "separator", "margin": "md"},
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "md",
                        "backgroundColor": "#FEF2F2",
                        "cornerRadius": "8px",
                        "paddingAll": "12px",
                        "contents": [
                            {
                                "type": "text",
                                "text": "📊 當前額度狀態：",
                                "weight": "bold",
                                "size": "xs",
                                "color": "#991B1B"
                            },
                            {
                                "type": "text",
                                "text": f"• 已發送：{current_count} 則\n• 剩餘免費用量：僅剩 {max(0, max_limit - current_count)} 則\n• 建議措施：立即啟動全系統靜音，停止警報推播",
                                "size": "xs",
                                "color": "#B91C1C",
                                "margin": "xs",
                                "wrap": True
                            }
                        ]
                    },
                    {
                        "type": "text",
                        "text": "💡 提示：靜音後各場站仍會持續執行巡檢與紀錄，唯暫停主動發送 LINE 推播。管理者仍可隨時在對話框中手動查詢各站狀態。",
                        "size": "xxs",
                        "color": "#64748B",
                        "margin": "md",
                        "wrap": True
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#DC2626",
                        "height": "sm",
                        "action": {
                            "type": "message",
                            "label": "🔕 一鍵啟動全系統靜音 (防超支)",
                            "text": "/靜音"
                        }
                    },
                    {
                        "type": "button",
                        "style": "secondary",
                        "height": "sm",
                        "action": {
                            "type": "message",
                            "label": "⚙️ 檢視全域參數設定",
                            "text": "/參數"
                        }
                    }
                ]
            }
        }
        return bubble

    @staticmethod
    def build_system_configs_flex_bubble(configs: Dict[str, str]) -> Dict[str, Any]:
        """
        建構超級管理者檢視與管理「系統全域參數」的 Flex Message 卡片
        """
        max_stn = configs.get("max_stations_per_user", "10")
        m_mode = configs.get("maintenance_mode", "OFF").upper()
        p_count = configs.get("monthly_push_count", "0")
        p_warn = configs.get("monthly_push_warning", "195")
        cur_m = configs.get("current_count_month", "-")

        mode_badge = "🔴 靜音中 (已暫停推播)" if m_mode == "ON" else "🟢 正常監控推播中"
        mode_btn_text = "🔔 解除靜音 (恢復推播)" if m_mode == "ON" else "🔕 啟動全系統靜音"
        mode_btn_cmd = "/解除靜音" if m_mode == "ON" else "/靜音"
        mode_btn_color = "#16A34A" if m_mode == "ON" else "#DC2626"

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
                        "text": "⚙️ 系統全域參數控制台",
                        "weight": "bold",
                        "color": "#FFFFFF",
                        "size": "lg"
                    },
                    {
                        "type": "text",
                        "text": f"👑 超級管理者權限 ｜ 狀態: {mode_badge}",
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
                        "text": "以下為「系統全域參數」目前配置，修改將即時同步至 Google 試算表：",
                        "size": "xs",
                        "color": "#475569",
                        "wrap": True
                    },
                    {"type": "separator", "margin": "sm"},
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "md",
                        "spacing": "xs",
                        "contents": [
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "每人最大監控場站數", "size": "xs", "color": "#64748B", "flex": 3},
                                    {"type": "text", "text": f"{max_stn} 座", "size": "xs", "weight": "bold", "color": "#0F172A", "flex": 2, "align": "end"}
                                ]
                            },
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "全系統靜音狀態", "size": "xs", "color": "#64748B", "flex": 3},
                                    {"type": "text", "text": f"{m_mode}", "size": "xs", "weight": "bold", "color": "#DC2626" if m_mode=="ON" else "#16A34A", "flex": 2, "align": "end"}
                                ]
                            },
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "本月累積推播則數", "size": "xs", "color": "#64748B", "flex": 3},
                                    {"type": "text", "text": f"{p_count} / 200 則", "size": "xs", "weight": "bold", "color": "#2563EB", "flex": 2, "align": "end"}
                                ]
                            },
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "推播瀕危警告門檻", "size": "xs", "color": "#64748B", "flex": 3},
                                    {"type": "text", "text": f"{p_warn} 則", "size": "xs", "weight": "bold", "color": "#0F172A", "flex": 2, "align": "end"}
                                ]
                            },
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "計數月份紀錄", "size": "xs", "color": "#64748B", "flex": 3},
                                    {"type": "text", "text": f"{cur_m}", "size": "xs", "color": "#94A3B8", "flex": 2, "align": "end"}
                                ]
                            }
                        ]
                    },
                    {"type": "separator", "margin": "md"},
                    {
                        "type": "text",
                        "text": "📝 修改參數方式：\n輸入「/參數 [參數名稱或鍵值] [新數值]」\n例如：\n• /參數 最大場站數 15\n• /參數 警告門檻 190\n• /參數 推播計數 0",
                        "size": "xxs",
                        "color": "#64748B",
                        "margin": "md",
                        "wrap": True
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": mode_btn_color,
                        "height": "sm",
                        "action": {
                            "type": "message",
                            "label": mode_btn_text,
                            "text": mode_btn_cmd
                        }
                    }
                ]
            }
        }
        return bubble

    def push_flex(self, user_id: str, flex_dict: Dict[str, Any], alt_text: str = "系統重要通知") -> bool:
        """
        向指定 LINE User ID 單獨推送 Flex 訊息 (推播 API)
        """
        if not self.channel_access_token:
            logger.info(f"[Mock Push Flex] Target: {user_id}, Alt: {alt_text}")
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
                logger.info(f"Successfully pushed flex message to {user_id}")
                return True
            elif LINE_SDK_AVAILABLE == "v2" and hasattr(self, "line_bot_api"):
                self.line_bot_api.push_message(
                    user_id,
                    FlexSendMessage(alt_text=alt_text, contents=flex_dict)
                )
                logger.info(f"Successfully pushed v2 flex message to {user_id}")
                return True
            else:
                logger.warning(f"No LINE SDK available to push flex to {user_id}")
                return False
        except Exception as e:
            logger.error(f"Failed to push LINE flex to {user_id}: {e}")
            return False

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
