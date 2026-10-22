# 多租戶 LINE Bot 異常通知系統 (Multi-Tenant LINE Bot Notification System)

[![Version](https://img.shields.io/badge/version-1.0.6-blue.svg)](version.py)
[![ISO27001](https://img.shields.io/badge/Security-ISO27001%20Compliant-green.svg)](.env.example)
[![Platform](https://img.shields.io/badge/Deployment-Vercel%20%7C%20Ubuntu-black.svg)](vercel.json)

採「**爬蟲與通知分離**」的微服務架構，將耗時且具阻斷風險的外部資料抓取，與輕量、無冷啟動延遲的即時推播徹底解耦。

---

## 系統架構圖 (Architecture)

```text
[ 外部營運網站 ]
       │ (隨機延遲防禦 / 每 30 分鐘)
       ▼
┌──────────────────────────────────────────────┐
│  模組一：背景資料爬蟲 (Ubuntu 主機)           │
│  - Linux crontab 定時排程                    │
│  - flock 防撞鎖 (確保單一實例執行)             │
│  - 資料清洗與 Google Sheets API 寫入          │
└──────────────────────┬───────────────────────┘
                       │ HTTP POST (帶 X-Trigger-Token 密鑰)
                       ▼
┌──────────────────────────────────────────────┐
│  模組二：權限判斷與推播 (Vercel Serverless)   │
│  - Python FastAPI 輕量極速啟動                │
│  - 讀取 Google Sheet 比對異常閥值             │
│  - 多租戶隔離權限表比對 (Tenant Isolation)    │
│  - 呼叫 LINE Messaging API 推送 Flex 卡片     │
└──────────────────────┬───────────────────────┘
                       │
       ┌───────────────┴───────────────┐
       ▼                               ▼
[ 客戶 A 授權人員 ]             [ 客戶 B 授權人員 ]
 (僅收到客戶 A 之異常)           (僅收到客戶 B 之異常)
```

---

## 目錄結構

```text
line_tenant_notify/
├── api/
│   └── index.py              # 模組二：Vercel Serverless Function 進入點 (FastAPI)
├── services/
│   ├── sheets_service.py     # Google Sheets API 串接與數據讀取
│   ├── tenant_service.py     # 多租戶隔離與 LINE User ID 權限比對
│   └── line_service.py       # LINE Messaging API 推播 (Flex Message)
├── crawler/
│   ├── crawler.py            # 模組一：爬蟲、清洗、寫入試算表與喚醒模組二
│   └── run_crawler.sh        # Linux flock 防撞鎖與 crontab 啟動腳本
├── vercel.json               # Vercel Serverless 路由設定
├── requirements.txt          # Python 相依套件
├── version.py                # 系統版本號管理 (v1.0.0)
├── .env.example              # ISO27001 資安規範環境變數範本
└── .gitignore                # 排除敏感檔案與日誌
```

---

## ISO27001 資安規範遵行說明

- **絕無 Hardcode**：所有系統密鑰、權限 Token、LINE 金鑰與 Google 服務帳戶皆嚴格由環境變數讀取。
- **機密傳輸保護**：
  - 模組一喚醒模組二時，強制檢驗 `X-Trigger-Token` 標頭。
  - LINE Webhook 嚴格校驗 `X-Line-Signature`。
- **租戶隔離保證**：透過 `TenantService` 嚴格過濾，確保客戶間的營運數據絕不交叉外洩。

---

## 部署與使用指引

### 1. 模組二：Vercel 部署 (LINE Bot 伺服器)
1. 將本專案 Push 至 GitHub。
2. 前往 Vercel Dashboard，點選 **Import** 導入此 Repository。
3. 在 Vercel 專案設定中新增以下 **Environment Variables**：
   - `LINE_CHANNEL_SECRET`
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `NOTIFY_TRIGGER_SECRET` (自訂隨機強密碼)
   - `GOOGLE_SPREADSHEET_ID`
   - `GOOGLE_SERVICE_ACCOUNT_BASE64` (將 Google 服務帳戶 JSON 轉為 Base64 字串貼入)
4. 點選 **Deploy**，取得部署網址（例如 `https://your-app.vercel.app`）。

### 2. 模組一：Ubuntu 主機部署 (背景爬蟲)
1. 將專案 Clone 至主機：
   ```bash
   git clone https://github.com/RayZhung/line_tenant_notify.git
   cd line_tenant_notify
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. 設定主機上的 `.env`（填入 `VERCEL_NOTIFY_URL` 與 `NOTIFY_TRIGGER_SECRET`）。
3. 給予腳本執行權限：
   ```bash
   chmod +x crawler/run_crawler.sh
   ```
4. 設定 crontab 每 30 分鐘自動執行：
   ```bash
   crontab -e
   # 加入以下排程
   */30 * * * * /path/to/line_tenant_notify/crawler/run_crawler.sh
   ```
