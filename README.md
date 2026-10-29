# 多租戶 LINE Bot 異常通知系統 (Multi-Tenant LINE Bot Notification System)

[![Version](https://img.shields.io/badge/version-1.3.0-blue.svg)](version.py)
[![ISO27001](https://img.shields.io/badge/Security-ISO27001%20Compliant-green.svg)](.env.example)
[![Platform](https://img.shields.io/badge/Deployment-Vercel%20%7C%20GitHub%20Actions-black.svg)](.github/workflows/crawler_schedule.yml)

採「**爬蟲與通知分離**」的微服務架構，結合 **GitHub Actions (每 15 分鐘全雲端免費巡檢)** 與 **Vercel Serverless (企業授權碼身分驗證、自主選站 <= 10 場配額、多租戶隔離推播、雙軌自然語言 AI)**。

---

## 核心功能特色

1. **企業授權碼驗證綁定 (Onboarding)**：
   - 新使用者初次加入 LINE 官方帳號時，強制驗證 **企業授權碼**（比對車車戰情室主檔之「企業名單」）。
   - 驗證成功後自動綁定所屬企業（例如：微程式資訊），解鎖管理員專屬權限。
2. **管理者自主挑選監控場站 (配額限制 <= 10 座)**：
   - **`/可選場站`**：瀏覽該企業有權限監控的所有合法場站清單（自動編排 `[01]`~`[99]` 代號）。
   - **`/新增 [代號或名稱]`**：將指定場站加入個人監控（嚴格防呆限制每人不得超過 10 座場站）。
   - **`/移除 [代號或名稱]`**：從個人監控清單移除，釋出名額供挑選其他場站。
3. **雙軌自然語言對話設定 (方案 C: NLP + Google Gemini AI)**：
   - **自然語言直接對話**：無需死記 `/車位` 或 `/冷卻` 語法，直接打字即可：
     - *「幫我把撫遠場車位少於 5 個時警報」*
     - *「安和站車位剩不到 10 格就提醒，上限不用設」*
     - *「撫遠場斷線先靜音 18 小時」*
     - *「安和站車位警報安靜半天」* (自動換算「半天」= 12 小時)
     - *「查一下我有哪些場站」*
   - **第 1 軌 (極速規則語意)**：0ms 零外部延遲、100% 免費、免 API Key。
   - **第 2 軌 (Google Gemini 2.0 Flash)**：自動處理倒裝句、長句與錯字（可選填 `GEMINI_API_KEY`）。
4. **多租戶與個人化精準派發**：
   - 僅對各管理者所自選的場站進行異常派發，企業間與個人間資料完全物理隔離。
5. **超級管理者全域控制 (Super Admin, 代碼 0)**：
   - **`/參數`**：檢視目前「系統全域參數」（每人監控場站上限、靜音狀態、本月累積推播則數、警戒門檻等）。
   - **`/參數 [名稱] [數值]`**：動態更新全域設定（例：`/參數 最大場站數 15`），即時同步 Google 試算表。
   - **`/靜音` 與 `/解除靜音`**：遇特殊維護或額度逼近時，一鍵凍結或恢復所有異常推播。
6. **推播用量防超支安全網 (195 則瀕危告警)**：
   - 每次成功發送警報自動累加至試算表計數器，支援換月自動重置歸零。
   - 當累積推播達到 **195 則**（免費 200 則額度僅剩 5 則）時，系統立即向超級管理者推送「🚨 瀕危警告 Flex 卡片」，並提供「🔕 一鍵全系統靜音」按鈕，徹底防止超額扣款。
7. **有異常才推播 (無異常靜默保護)**：
   - 全台場站正常時，發送 0 則 LINE 訊息；唯有自選場站異常且超過冷卻時間時才派發 Flex 卡片。

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
