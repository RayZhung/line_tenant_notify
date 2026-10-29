# 多租戶 LINE Bot 異常通知系統 (Multi-Tenant LINE Bot Notification System)

[![Version](https://img.shields.io/badge/version-1.2.0-blue.svg)](version.py)
[![ISO27001](https://img.shields.io/badge/Security-ISO27001%20Compliant-green.svg)](.env.example)
[![Platform](https://img.shields.io/badge/Deployment-Vercel%20%7C%20GitHub%20Actions-black.svg)](.github/workflows/crawler_schedule.yml)

採「**爬蟲與通知分離**」的微服務架構，結合 **GitHub Actions (每 15 分鐘全雲端免費巡檢)** 與 **Vercel Serverless (多租戶角色分流、雙軌自然語言 AI 與互動管理)**。

---

## 核心功能特色

1. **雙軌自然語言對話設定 (方案 C: NLP + Google Gemini AI)**：
   - **自然語言直接對話**：無需死記 `/車位` 或 `/冷卻` 語法，直接打字即可：
     - *「幫我把撫遠場車位少於 5 個時警報」*
     - *「安和站車位剩不到 10 格就提醒，上限不用設」*
     - *「撫遠場斷線先靜音 12 小時」*
     - *「安和站車位警報安靜半天」* (自動換算「半天」= 12 小時)
     - *「查一下我有管哪些場站」*
   - **第 1 軌 (極速規則語意)**：0ms 零外部延遲、100% 免費、免 API Key。
   - **第 2 軌 (Google Gemini 2.0 Flash)**：自動處理倒裝句、長句與錯字（可選填 `GEMINI_API_KEY`，免費額度高達 1,500 次/天）。
2. **多租戶與角色職責雙重隔離 (RBAC)**：
   - **甲角色 (設備運維)**：接收場站斷線與連線狀態異常（包含 IP 位址以利遠端檢修）。
   - **乙角色 (場站營運)**：接收即時可用車位過低（即將滿場）或過高（空轉）之警報。
   - 企業 A 與企業 B 之資料完全物理隔離，互不相通。
3. **LINE Bot 管理員動態互動指令 (精準指令模式)**：
   - **`/?`**：隨時開啟圖文指令教學手冊（防誤觸設計）。
   - **`/查詢`**：即時查看所屬公司轄下所有場站之車位門檻與防洗版冷卻設定。
   - **`/車位 [場站名稱] [下限] [上限]`**：自訂場站車位警報門檻。
   - **`/冷卻 [場站或全部] [車位|斷線] [6|12|18|24|36]`**：設定重複警報間隔，防止洗版並守護 LINE 每月 200 則免費額度。
4. **有異常才推播 (無異常靜默保護)**：
   - 每次巡檢皆即時更新 Google 試算表戰情看板。
   - 全台場站正常時，發送 0 則 LINE 訊息；唯有異常且超過冷卻時間時才派發 Flex 卡片。

---

## 系統架構圖 (Architecture)

```text
[ ParkV4 場站後台 (全台 100+ 場) ]
       │ (每 15 分鐘全自動巡檢)
       ▼
┌────────────────────────────────────────────────────────┐
│  模組一：背景資料爬蟲 (GitHub Actions 雲端環境)           │
│  - 每 15 分鐘定時排程 (Cron: */15 * * * *)              │
│  - 唯讀讀取「車車戰情室」主檔 (1oaBHrT8mV...)            │
│  - 巡檢場站連線與可用車位數                             │
│  - 清洗並寫入「場站營運數據」試算表 (1BzVMgiG...)         │
└──────────────────────────┬─────────────────────────────┘
                           │ HTTP POST (帶 X-Trigger-Token 密鑰)
                           ▼
┌────────────────────────────────────────────────────────┐
│  模組二：權限判斷與推播 (Vercel Serverless Function)    │
│  - Python FastAPI 輕量極速架構                          │
│  - 讀取「場站警報設定」與「最後警報紀錄」                 │
│  - 智慧比對車位上下限與 6/12/18/24/36 小時冷卻過濾       │
│  - 呼叫 LINE Messaging API 推送 Flex 角色分流卡片       │
│  - 接收 LINE Webhook 支援 /?、/查詢、/車位、/冷卻 管理  │
└──────────────────────────┬─────────────────────────────┘
                           │
       ┌───────────────────┴───────────────────┐
       ▼                                       ▼
[ 企業 A 授權管理員 (甲 / 乙) ]         [ 企業 B 授權管理員 (甲 / 乙) ]
```

---

## 目錄結構

```text
line_tenant_notify/
├── .github/workflows/
│   └── crawler_schedule.yml  # 模組一：GitHub Actions 每 15 分鐘全自動巡檢排程
├── api/
│   └── index.py              # 模組二：Vercel Serverless Function 進入點 (FastAPI + Webhook)
├── services/
│   ├── sheets_service.py     # Google Sheets API 串接 (營運數據、警報設定、冷卻紀錄)
│   ├── tenant_service.py     # 多租戶隔離、角色分流、警報判定與指令解析
│   └── line_service.py       # LINE Flex Message 警報與教學卡片建置
├── crawler/
│   ├── crawler.py            # 巡檢爬蟲：讀取主檔、巡檢 ParkV4、更新試算表並觸發 Vercel
│   └── run_crawler.sh        # Linux flock 防撞鎖 (本機/VM 備援執行腳本)
├── vercel.json               # Vercel 路由與 URL 重寫配置
├── requirements.txt          # Python 相依套件清單
├── version.py                # 系統版本號管理 (v1.1.0)
├── .env.example              # ISO27001 資安規範環境變數範本
└── .gitignore                # 排除金鑰憑證與日誌檔案
```

---

## GitHub Secrets 設定指引 (模組一全自動執行)

在 GitHub Repository 的 **Settings -> Secrets and variables -> Actions** 中新增以下 Secrets：

| Secret 名稱 | 內容說明 |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_BASE64` | GCP 服務帳號 JSON 金鑰的 Base64 編碼字串 |
| `MASTER_SPREADSHEET_ID` | `1oaBHrT8mVfarrVXYJVwqphJEGk0JKWezUHmlRhAMVAQ` (唯讀車車戰情室主檔) |
| `GOOGLE_SPREADSHEET_ID` | `1BzVMgiG-d7HxeUzbjRooOgvPvWcVH1e_9l1EqWH0HYI` (營運戰情表) |
| `VERCEL_NOTIFY_URL` | `https://line-tenant-notify.vercel.app/api/v1/trigger` |
| `NOTIFY_TRIGGER_SECRET` | 觸發 Vercel 的專用驗證金鑰 |
