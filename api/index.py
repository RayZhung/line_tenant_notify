"""
多租戶 LINE Bot 異常通知系統 - 主應用服務 (Vercel Serverless Function)
支援:
  1. 模組二交接觸發端點 (/api/v1/trigger)
  2. 多租戶 (Company) 與角色 (甲/乙) 雙重隔離推播
  3. 自訂車位門檻、斷線警報與 6/12/18/24/36 小時冷卻防洗版
  4. LINE 官方 Webhook 回呼與互動指令 (/? 指令手冊、/查詢 門檻設定、/車位 門檻調整、/冷卻 間隔設定)
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

# 註冊 LINE 訊息處理器 (支援 /?、/查詢、/車位、/冷卻 等管理指令)
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

            if action == "help":
                bubble = line_service.build_help_flex_bubble(user_id, cmd_res.get("profile"))
                line_service.reply_flex(reply_token, bubble, alt_text="📖 管理員指令教學手冊")
            elif action == "query":
                bubble = line_service.build_query_settings_flex_bubble(
                    cmd_res["company_name"], 
                    cmd_res.get("settings", [])
                )
                line_service.reply_flex(reply_token, bubble, alt_text="📋 轄下場站警報設定總覽")
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
            elif action == "error":
                line_service.reply_text(reply_token, f"⚠️ {cmd_res.get('error')}")
            elif action == "unauthorized":
                line_service.reply_text(
                    reply_token,
                    f"🔒 存取受限：\n{cmd_res.get('error')}\n\n如需開通管理權限，請提供您的 LINE User ID 給系統管理員。"
                )
            else:
                profile = cmd_res.get("profile")
                cname = profile.get("company_name", "訪客") if profile else "訪客"
                role = profile.get("role_name", "未授權") if profile else "未授權"
                line_service.reply_text(
                    reply_token,
                    f"👋 您好！我是「場站監控推播機器人」。\n\n"
                    f"🏢 身份：{cname} ({role})\n"
                    f"🔑 您的 LINE User ID：\n👉 {user_id}\n\n"
                    f"🤖 您可以直接跟我說話設定場站，例如：\n"
                    f"•「幫我把撫遠場車位少於 5 個時警報」\n"
                    f"•「安和站車位剩不到 10 格就提醒」\n"
                    f"•「撫遠場斷線先靜音 12 小時」\n"
                    f"•「查一下我的場站設定」\n\n"
                    f"💡 亦可隨時輸入「/?」開啟管理員手冊！"
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
    return {"status": "healthy", "version": VERSION}

@app.post("/api/v1/trigger")
@app.post("/v1/trigger")
@app.post("/trigger")
async def trigger_anomaly_notification(
    request: Request,
    x_trigger_token: Optional[str] = Header(None, alias="X-Trigger-Token")
):
    """
    交接觸發端點：
    接收模組一 (爬蟲) 發送的喚醒訊號，比對試算表數值並執行多租戶與角色隔離推播。
    """
    expected_secret = os.getenv("NOTIFY_TRIGGER_SECRET", "")
    if expected_secret:
        if not x_trigger_token or x_trigger_token != expected_secret:
            logger.warning("Unauthorized trigger attempt detected: invalid or missing X-Trigger-Token.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing X-Trigger-Token authentication header."
            )

    logger.info("Trigger received. Starting anomaly evaluation and multi-tenant dispatch...")

    # 1. 抓取最新營運數據、多租戶權限表、場站警報設定、最後警報冷卻紀錄
    operations_data = sheets_service.fetch_operations_data()
    tenant_permissions = sheets_service.fetch_tenant_permissions()
    alert_settings = sheets_service.fetch_alert_settings()
    cooldown_history = sheets_service.fetch_cooldown_history()

    # 2. 條件判斷：篩選車位與斷線異常 (結合冷卻判定與自訂上下限，全面移除發票警報)
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

    # 3. 雙重隔離：比對權限表，分流不同企業與角色 (甲/乙)
    dispatch_plans = TenantService.map_anomalies_to_roles(anomalies, tenant_permissions)

    # 4. 精準推播：針對各企業角色授權的 LINE User ID 推送專屬警示
    dispatched_count = 0
    delivery_reports = []

    for plan in dispatch_plans:
        cid = plan["company_id"]
        cname = plan["company_name"]
        role_name = plan["role_name"]
        line_users = plan["line_user_ids"]
        role_anomalies = plan["anomalies"]

        delivered_users = []
        for uid in line_users:
            success = line_service.send_role_anomaly_alert(uid, cname, role_name, role_anomalies)
            if success:
                delivered_users.append(uid)
                dispatched_count += 1

        delivery_reports.append({
            "company_id": cid,
            "company_name": cname,
            "role_name": role_name,
            "anomalies_count": len(role_anomalies),
            "delivered_users": delivered_users
        })

    # 5. 記錄推播時間到「最後警報紀錄」以實現冷卻週期防洗版
    if newly_triggered and dispatched_count > 0:
        sheets_service.record_alert_triggers(newly_triggered)

    return {
        "status": "success",
        "message": f"Evaluated {len(operations_data)} stations. Found {len(anomalies)} anomalies across {len(dispatch_plans)} role targets.",
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
