"""
模組二：權限判斷與推播服務 (Vercel Serverless FastAPI 進入點)
特點:
  - 輕量化、瞬間啟動、無冷啟動延遲
  - ISO27001 資安規範遵循 (金鑰保護、請求驗證)
  - 多租戶絕對數據隔離
"""
import os
import sys
import logging
from typing import Dict, Any, Optional

from fastapi import FastAPI, Request, Header, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# 確保當前目錄與上層模組路徑正確
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from version import VERSION, APP_NAME
from services.sheets_service import SheetsService
from services.tenant_service import TenantService
from services.line_service import LineService

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("vercel_api")

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

# 依賴服務實例
sheets_service = SheetsService()
line_service = LineService()

@app.get("/")
@app.get("/api")
@app.get("/api/")
@app.get("/api/index.py")
def read_root():
    return {
        "status": "online",
        "app": APP_NAME,
        "version": VERSION,
        "architecture": "Vercel Serverless (FastAPI) + Ubuntu Crawler"
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
    接收模組一 (爬蟲) 發送的喚醒訊號，比對試算表數值並執行多租戶隔離推播。
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

    # 1. 抓取最新營運數據與租戶權限表
    operations_data = sheets_service.fetch_operations_data()
    tenant_permissions = sheets_service.fetch_tenant_permissions()

    # 2. 條件判斷：篩選低於警戒標準的異常場站
    default_thresh = float(os.getenv("DEFAULT_ALERT_THRESHOLD", 10.0))
    anomalies = TenantService.filter_anomalies(operations_data, default_threshold=default_thresh)

    if not anomalies:
        logger.info("All stations operating within normal threshold. No alerts needed.")
        return {
            "status": "success",
            "message": "All station metrics are normal.",
            "total_stations_checked": len(operations_data),
            "anomalies_count": 0,
            "alerts_dispatched": 0
        }

    # 3. 多租戶隔離：比對權限表，分流不同客戶的異常
    targets = TenantService.map_anomalies_to_tenants(anomalies, tenant_permissions)

    # 4. 精準推播：針對各客戶授權的 LINE User ID 推送專屬警示
    dispatched_count = 0
    tenant_reports = []

    for tenant_id, data in targets.items():
        tenant_name = data["tenant_name"]
        line_users = data["line_user_ids"]
        tenant_anomalies = data["anomalies"]

        delivered_users = []
        for uid in line_users:
            success = line_service.send_tenant_anomaly_alert(uid, tenant_name, tenant_anomalies)
            if success:
                delivered_users.append(uid)
                dispatched_count += 1

        tenant_reports.append({
            "tenant_id": tenant_id,
            "tenant_name": tenant_name,
            "anomalies_count": len(tenant_anomalies),
            "delivered_users": delivered_users
        })

    return {
        "status": "success",
        "message": f"Evaluated {len(operations_data)} stations. Found {len(anomalies)} anomalies across {len(targets)} tenants.",
        "total_stations_checked": len(operations_data),
        "total_anomalies": len(anomalies),
        "total_alerts_dispatched": dispatched_count,
        "tenant_breakdown": tenant_reports
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

    # 若未配置金鑰則記錄日誌並返回 OK
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

