"""
多租戶 LINE Bot 異常通知系統 - 主應用服務 (Vercel Serverless Function)
支援:
  1. 模組二交接觸發端點 (/api/v1/trigger)
  2. 企業授權碼驗證綁定 (首次進線強制驗證)
  3. 管理者自主挑選監控場站 (<= 10 場)
  4. 多租戶 (Company) 與個人化隔離推播
  5. 自訂車位門檻、斷線警報與 6/12/18/24/36 小時冷卻防洗版
  6. LINE 官方 Webhook 回呼與互動指令 (/可選場站、/新增、/移除、/查詢、/車位、/冷卻、/?)
"""
import os
import sys
import logging
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Request, Header, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from version import VERSION, APP_NAME
from services.sheets_service import SheetsService
from services.tenant_service import TenantService
from services.line_service import LineService

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("api_main")

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="多租戶 LINE Bot 異常通知系統 - Vercel Serverless 微服務"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def vercel_path_rewrite_middleware(request: Request, call_next):
    raw_path = request.query_params.get("__raw_path")
    if raw_path:
        normalized_path = raw_path if raw_path.startswith("/") else f"/{raw_path}"
        request.scope["path"] = normalized_path
    response = await call_next(request)
    return response

# 依賴服務實例
sheets_service = SheetsService()
line_service = LineService()

# 註冊 LINE 訊息處理器
if line_service.handler:
    try:
        from linebot.v3.webhooks import MessageEvent, TextMessageContent

        @line_service.handler.add(MessageEvent, message=TextMessageContent)
        def handle_line_text_message(event):
            user_id = getattr(event.source, "user_id", "UNKNOWN")
            text = str(event.message.text or "").strip()
            reply_token = event.reply_token

            logger.info(f"Received LINE message from {user_id}: '{text}'")
            cmd_res = TenantService.parse_and_execute_command(user_id, text, sheets_service)
            action = cmd_res.get("action")

            # 1. 尚未授權 -> 提示輸入企業授權碼
            if action == "auth_required":
                bubble = line_service.build_auth_request_flex_bubble(user_id)
                line_service.reply_flex(reply_token, bubble, alt_text="🔒 請輸入企業授權碼")

            # 2. 授權碼驗證成功
            elif action == "auth_success":
                bubble = line_service.build_auth_success_flex_bubble(
                    cmd_res["company_name"], 
                    cmd_res.get("role_name", "管理員"), 
                    user_id,
                    max_count=cmd_res.get("max_count", 10)
                )
                line_service.reply_flex(reply_token, bubble, alt_text="🎉 企業身份綁定成功！")

            # 3. 列出可選候選場站 /可選場站 (完整垂直分段列表 + 右側點擊新增/移除)
            elif action == "list_candidates":
                bubbles = line_service.build_candidate_sites_flex_bubbles(
                    company_name=cmd_res["company_name"],
                    candidates=cmd_res.get("candidates", []),
                    current_count=cmd_res.get("current_count", 0),
                    max_count=cmd_res.get("max_count", 10)
                )
                line_service.reply_flex_list(reply_token, bubbles, alt_text="🏢 可選監控場站清單")

            # 4. 新增場站至個人監控 /新增
            elif action == "add_subscription":
                r = cmd_res.get("result", {})
                quick_nav_buttons = [
                    {"label": "🔄 返回可選場站清單", "text": "/可選場站", "style": "primary", "color": "#0284C7"},
                    {"label": "📋 查看我的監控清單", "text": "/查詢", "style": "secondary"}
                ]
                if r.get("success"):
                    def_low = r.get("default_lower", "5")
                    def_sc = r.get("default_space_cooldown", "6")
                    def_oc = r.get("default_offline_cooldown", "24")
                    details = [
                        {"label": "新增場站", "value": f"[{r.get('station_code')}] {r.get('station_name')}"},
                        {"label": "目前監控配額", "value": f"{r.get('current_count')} / {r.get('max_count')} 座場站"},
                        {"label": "預設車位門檻", "value": f"< {def_low} 格"},
                        {"label": "預設冷卻時間", "value": f"車位 {def_sc}h / 斷線 {def_oc}h"}
                    ]
                    bubble = line_service.build_command_result_flex_bubble(
                        "場站加入監控成功",
                        f"已成功將 [{r.get('station_code')}] {r.get('station_name')} 加入您的個人監控清單。",
                        details,
                        is_success=True,
                        footer_buttons=quick_nav_buttons
                    )
                else:
                    bubble = line_service.build_command_result_flex_bubble(
                        "無法加入監控",
                        r.get("error", "未知錯誤"),
                        [],
                        is_success=False,
                        footer_buttons=quick_nav_buttons
                    )
                line_service.reply_flex(reply_token, bubble, alt_text="新增監控結果")

            # 5. 移除個人監控場站 /移除
            elif action == "remove_subscription":
                r = cmd_res.get("result", {})
                quick_nav_buttons = [
                    {"label": "🔄 返回可選場站清單", "text": "/可選場站", "style": "primary", "color": "#0284C7"},
                    {"label": "📋 查看我的監控清單", "text": "/查詢", "style": "secondary"}
                ]
                if r.get("success"):
                    details = [
                        {"label": "移除場站", "value": r.get("station_name", "")},
                        {"label": "剩餘監控數", "value": f"{r.get('remaining_count')} / {r.get('max_count')} 座場站"}
                    ]
                    bubble = line_service.build_command_result_flex_bubble(
                        "已移除監控場站",
                        "已為您釋出配額名額，可點擊下方按鈕返回場站清單繼續挑選。",
                        details,
                        is_success=True,
                        footer_buttons=quick_nav_buttons
                    )
                else:
                    bubble = line_service.build_command_result_flex_bubble(
                        "移除失敗",
                        r.get("error", "未知錯誤"),
                        [],
                        is_success=False,
                        footer_buttons=quick_nav_buttons
                    )
                line_service.reply_flex(reply_token, bubble, alt_text="移除監控結果")

            # 6. 教學手冊 /?
            elif action == "help":
                bubble = line_service.build_help_flex_bubble(
                    user_id,
                    cmd_res.get("profile"),
                    max_count=cmd_res.get("max_count", 10)
                )
                line_service.reply_flex(reply_token, bubble, alt_text="📖 管理員指令教學手冊")

            # 7. 查詢個人監控場站 /查詢
            elif action == "query":
                bubbles = line_service.build_query_settings_flex_bubbles(
                    cmd_res["company_name"], 
                    cmd_res.get("settings", []),
                    max_count=cmd_res.get("max_count", 10)
                )
                line_service.reply_flex_list(reply_token, bubbles, alt_text="📋 我的個人監控場站清單")

            # 8. 車位門檻設定 /車位
            elif action == "space_update":
                r = cmd_res.get("result", {})
                if r.get("success"):
                    details = [
                        {"label": "場站名稱", "value": r.get("station_name", "")},
                        {"label": "車位下限", "value": f"< {r.get('lower')} 格" if r.get('lower') != "無" else "不設限"},
                        {"label": "車位上限", "value": f"> {r.get('upper')} 格" if r.get('upper') != "無" else "不設限"},
                        {"label": "更新時間", "value": r.get("updated_at", "")}
                    ]
                    bubble = line_service.build_command_result_flex_bubble(
                        "車位警報門檻更新成功",
                        f"已為場站「{r.get('station_name')}」更新車位監控標準。",
                        details,
                        is_success=True
                    )
                else:
                    bubble = line_service.build_command_result_flex_bubble(
                        "車位設定失敗",
                        r.get("error", "未知錯誤"),
                        [],
                        is_success=False
                    )
                line_service.reply_flex(reply_token, bubble, alt_text="車位設定結果")

            # 9. 冷卻設定 /冷卻
            elif action == "cooldown_update":
                r = cmd_res.get("result", {})
                if r.get("success"):
                    details = [
                        {"label": "警報類別", "value": f"{r.get('alert_type')}警報"},
                        {"label": "冷卻時數", "value": f"{r.get('hours')} 小時"},
                        {"label": "生效場站", "value": f"共 {r.get('updated_count')} 個場站"},
                        {"label": "更新時間", "value": r.get("updated_at", "")}
                    ]
                    bubble = line_service.build_command_result_flex_bubble(
                        "冷卻時間設定成功",
                        f"已將 {r.get('alert_type')} 警報間隔調整為 {r.get('hours')} 小時。",
                        details,
                        is_success=True
                    )
                else:
                    bubble = line_service.build_command_result_flex_bubble(
                        "冷卻設定失敗",
                        r.get("error", "未知錯誤"),
                        [],
                        is_success=False
                    )
                line_service.reply_flex(reply_token, bubble, alt_text="冷卻設定結果")

            # 10. 超級管理者指令: 檢視全域參數 /參數
            elif action == "list_configs":
                configs = cmd_res.get("configs", {})
                bubble = line_service.build_system_configs_flex_bubble(configs)
                line_service.reply_flex(reply_token, bubble, alt_text="⚙️ 系統全域參數控制台")

            # 11. 超級管理者指令: 修改全域參數成功
            elif action == "config_updated":
                t_key = cmd_res.get("target_key", "")
                t_val = cmd_res.get("target_value", "")
                configs = cmd_res.get("configs", {})
                bubble = line_service.build_system_configs_flex_bubble(configs)
                line_service.reply_flex(reply_token, bubble, alt_text=f"⚙️ 參數 {t_key} 已更新為 {t_val}")

            # 12. 超級管理者指令: 全系統靜音 /靜音
            elif action == "system_mute":
                msg = cmd_res.get("message", "🔕 全系統靜音已啟動！")
                line_service.reply_text(reply_token, msg)

            # 13. 超級管理者指令: 解除靜音 /解除靜音
            elif action == "system_unmute":
                msg = cmd_res.get("message", "🔔 全系統靜音已解除！")
                line_service.reply_text(reply_token, msg)

            elif action == "error":
                line_service.reply_text(reply_token, f"⚠️ {cmd_res.get('error')}")

            else:
                profile = cmd_res.get("profile")
                cname = profile.get("company_name", "微程式") if profile else "微程式"
                role = profile.get("role_name", "管理員") if profile else "管理員"
                cid = str(profile.get("company_id", "")).strip() if profile else ""
                is_super = (cid in ["0", "ROOT"] or "超級" in role or "超級" in cname)
                sub_cnt = cmd_res.get("sub_count", 0)
                max_cnt = cmd_res.get("max_count", 50 if is_super else 10)

                super_hint = (
                    "👑 超級管理者專屬指令：\n"
                    "• /參數 (開啟全域參數控制台)\n"
                    "• /靜音 ｜ /解除靜音 (全系統推播開關)\n\n"
                ) if is_super else ""

                line_service.reply_text(
                    reply_token,
                    f"👋 您好！我是「場站監控中心機器人」。\n\n"
                    f"🏢 身份：{cname} ({role})\n"
                    f"📊 目前個人監控：{sub_cnt} / {max_cnt} 座場站\n\n"
                    f"{super_hint}"
                    f"🤖 常用指令：\n"
                    f"• /可選場站 (完整列出名單，可點擊右側新增/移除)\n"
                    f"• /查詢 (查看我的監控與門檻)\n"
                    f"• /車位 [代號] [下限] [上限] (設定車位警報)\n"
                    f"• 亦可直接說「撫遠場車位少於5個時警報」\n\n"
                    f"💡 輸入「/?」即可開啟專屬詳細指令手冊！"
                )
    except Exception as e:
        logger.error(f"Failed to bind custom LINE message handler: {e}")

@app.get("/")
@app.get("/api")
@app.get("/api/")
@app.get("/api/index.py")
def read_root(request: Request):
    return {
        "status": "online",
        "app": APP_NAME,
        "version": VERSION,
        "url_path": str(request.url.path),
        "headers": dict(request.headers)
    }

@app.get("/health")
@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "version": VERSION,
        "sheets_connected": sheets_service.client is not None,
        "has_sa_b64": bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_BASE64")),
        "has_sa_file": os.path.exists("service_account.json"),
        "has_spreadsheet_id": bool(sheets_service.spreadsheet_id),
        "has_master_spreadsheet_id": bool(sheets_service.master_spreadsheet_id)
    }

@app.post("/api/v1/trigger")
@app.post("/v1/trigger")
@app.post("/trigger")
async def trigger_anomaly_notification(
    request: Request,
    x_trigger_token: Optional[str] = Header(None, alias="X-Trigger-Token")
):
    """
    交接觸發端點：
    接收模組一 (爬蟲) 發送的喚醒訊號，比對試算表數值並執行個人化與多租戶隔離推播。
    """
    expected_secret = os.getenv("NOTIFY_TRIGGER_SECRET", "")
    if expected_secret:
        if not x_trigger_token or x_trigger_token != expected_secret:
            logger.warning("Unauthorized trigger attempt detected: invalid or missing X-Trigger-Token.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing X-Trigger-Token authentication header."
            )

    logger.info("Trigger received. Starting anomaly evaluation and personal dispatch...")

    # 0. 檢查全系統靜音狀態 (Maintenance Mode 防超支保護開關)
    sys_configs = sheets_service.fetch_system_configs()
    if str(sys_configs.get("maintenance_mode", "OFF")).strip().upper() == "ON":
        logger.warning("Maintenance mode is ON (全系統靜音中). Suppressing all alert push notifications.")
        return {
            "status": "muted",
            "message": "全系統靜音中 (maintenance_mode: ON)，已略過所有異常推播發送以防止額度超支。",
            "total_stations_checked": 0,
            "total_anomalies": 0,
            "total_alerts_dispatched": 0,
            "delivery_breakdown": []
        }

    # 1. 抓取最新營運數據、場站警報設定、最後警報冷卻紀錄
    operations_data = sheets_service.fetch_operations_data()
    alert_settings = sheets_service.fetch_alert_settings()
    cooldown_history = sheets_service.fetch_cooldown_history()

    # 2. 條件判斷：篩選車位與斷線異常
    anomalies, newly_triggered = TenantService.filter_anomalies(
        operations_data=operations_data,
        alert_settings=alert_settings,
        cooldown_history=cooldown_history
    )

    if not anomalies:
        logger.info("All stations operating within normal threshold or suppressed by cooldown. No alerts needed.")
        return {
            "status": "success",
            "message": "All station metrics are normal or suppressed by cooldown.",
            "total_stations_checked": len(operations_data),
            "anomalies_count": 0,
            "alerts_dispatched": 0
        }

    # 3. 個人化派發：比對「個人監控場站表」
    all_subs = sheets_service.fetch_all_user_subscriptions()
    tenant_permissions = sheets_service.fetch_tenant_permissions()

    dispatched_count = 0
    delivery_reports = []

    if all_subs:
        user_plans = TenantService.map_anomalies_to_users(
            anomalies=anomalies,
            all_subscriptions=all_subs,
            tenant_permissions=tenant_permissions,
            cooldown_history=cooldown_history
        )
        for plan in user_plans:
            uid = plan["line_user_id"]
            cname = plan["company_name"]
            role_name = plan["role_name"]
            role_anomalies = plan["anomalies"]

            success = line_service.send_role_anomaly_alert(uid, cname, role_name, role_anomalies)
            if success:
                dispatched_count += 1
                delivery_reports.append({
                    "line_user_id": uid,
                    "company_name": cname,
                    "anomalies_count": len(role_anomalies)
                })
                # 每成功推播 1 則，累加計數並檢查 195 瀕危門檻
                new_count, just_reached = sheets_service.increment_monthly_push_count(1)
                if just_reached:
                    super_uids = sheets_service.fetch_super_admin_user_ids()
                    warn_bubble = line_service.build_emergency_push_warning_flex_bubble(new_count)
                    for s_uid in super_uids:
                        line_service.push_flex(s_uid, warn_bubble, alt_text="🚨 LINE 推播用量瀕危警告 (195/200)")
    else:
        # 備援派發方案
        dispatch_plans = TenantService.map_anomalies_to_roles(anomalies, tenant_permissions)
        for plan in dispatch_plans:
            cname = plan["company_name"]
            role_name = plan["role_name"]
            line_users = plan["line_user_ids"]
            role_anomalies = plan["anomalies"]
            for uid in line_users:
                success = line_service.send_role_anomaly_alert(uid, cname, role_name, role_anomalies)
                if success:
                    dispatched_count += 1
                    new_count, just_reached = sheets_service.increment_monthly_push_count(1)
                    if just_reached:
                        super_uids = sheets_service.fetch_super_admin_user_ids()
                        warn_bubble = line_service.build_emergency_push_warning_flex_bubble(new_count)
                        for s_uid in super_uids:
                            line_service.push_flex(s_uid, warn_bubble, alt_text="🚨 LINE 推播用量瀕危警告 (195/200)")

    # 4. 記錄推播時間到「最後警報紀錄」以實現冷卻週期防洗版
    if newly_triggered and dispatched_count > 0:
        sheets_service.record_alert_triggers(newly_triggered)

    return {
        "status": "success",
        "message": f"Evaluated {len(operations_data)} stations. Dispatched {dispatched_count} alerts.",
        "total_stations_checked": len(operations_data),
        "total_anomalies": len(anomalies),
        "total_alerts_dispatched": dispatched_count,
        "delivery_breakdown": delivery_reports
    }

@app.post("/callback")
@app.post("/api/callback")
async def line_webhook(request: Request):
    """
    LINE 官方 Webhook 回呼端點
    """
    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode("utf-8")

    if not signature:
        return JSONResponse(status_code=400, content={"error": "Missing X-Line-Signature"})

    if not line_service.handler:
        logger.info("Received webhook callback, but handler is not configured.")
        return JSONResponse(status_code=200, content={"status": "received_unconfigured"})

    try:
        line_service.handler.handle(body, signature)
        return JSONResponse(status_code=200, content={"status": "ok"})
    except Exception as e:
        logger.error(f"Error handling webhook signature: {e}")
        return JSONResponse(status_code=400, content={"error": "Invalid signature"})

@app.get("/{fallback_path:path}")
def fallback_catch_all(request: Request, fallback_path: str):
    """
    備援路由：確保任何自訂路徑皆能回傳服務狀態資訊
    """
    return {
        "status": "online",
        "app": APP_NAME,
        "version": VERSION,
        "matched_path": fallback_path,
        "request_url": str(request.url)
    }
