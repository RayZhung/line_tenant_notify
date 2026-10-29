"""
自然語言意圖理解與實體萃取服務 (NLP & AI Intent Service)
架構：
  - 第 1 軌：內建中文語意規則引擎 (Regex & Semantic Keyword Parser) - 0ms 零外部依賴
  - 第 2 軌：Google Gemini 2.0 Flash AI 結構化意圖理解 (處理口語、錯字、倒裝句)
"""
import os
import re
import json
import logging
from typing import Dict, Any, List, Optional

import requests

logger = logging.getLogger("nlp_service")

def match_station_name(text: str, allowed_stations: Optional[List[str]]) -> Optional[str]:
    """
    智能比對場站名稱 (支援簡稱、去後綴與子字串比對，例如「撫遠」比對「松山撫遠場」)
    """
    if not allowed_stations:
        return None

    # 1. 精準比對
    for s in allowed_stations:
        if s in text:
            return s

    # 2. 去後綴比對 (去掉「場」、「站」、「停車場」)
    for s in allowed_stations:
        clean_s = s.replace("停車場", "").replace("場", "").replace("站", "")
        if clean_s and clean_s in text:
            return s

    # 3. 雙向子片段比對 (例如「松山撫遠」中的「撫遠」或「松山」出現在 text 中)
    best_match = None
    max_len = 0
    for s in allowed_stations:
        clean_s = s.replace("停車場", "").replace("場", "").replace("站", "")
        # 分割成 2 個字以上的詞比對
        for i in range(len(clean_s) - 1):
            sub = clean_s[i:i+2]
            if sub in text:
                if len(sub) > max_len:
                    max_len = len(sub)
                    best_match = s

    return best_match

TIME_SEMANTIC_MAP = {
    "半天": 12,
    "一天": 24,
    "整天": 24,
    "一日": 24,
    "一天半": 36,
    "6小時": 6,
    "12小時": 12,
    "18小時": 18,
    "24小時": 24,
    "36小時": 36,
    "6h": 6,
    "12h": 12,
    "18h": 18,
    "24h": 24,
    "36h": 36
}

class NLPService:
    @staticmethod
    def parse_with_regex(
        text: str,
        allowed_stations: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        第 1 軌：本機端快速中文語意規則萃取
        """
        clean_text = text.strip()

        # 1. 教學 / 說明意圖
        if re.search(r"^(說明|教學|怎麼用|如何使用|功能介紹|\?|？|help)$", clean_text, re.IGNORECASE):
            return {"intent": "help"}

        # 2. 查詢 / 總覽意圖
        if re.search(r"^(查|查詢|看|列出|有哪些場站|場站清單|目前設定|門檻設定|設定總覽|狀態)", clean_text) or \
           re.search(r"(查一下|幫我看|目前的設定|有哪些站)", clean_text):
            return {"intent": "query"}

        # 3. 冷卻防洗版意圖
        # 範例：「撫遠場斷線先靜音 12 小時」、「把全部場站的斷線冷卻改為 24 小時」、「安和站車位警報安靜半天」
        cooldown_kw = re.search(r"(冷卻|靜音|不要吵|安靜|間隔|暫停通知|勿擾)", clean_text)
        if cooldown_kw:
            # 判斷警報類型 (斷線 vs 車位)
            alert_type = "斷線" if ("斷線" in clean_text or "連線" in clean_text or "離線" in clean_text) else "車位"

            # 判斷時數
            hours = None
            for sem_word, val in TIME_SEMANTIC_MAP.items():
                if sem_word in clean_text:
                    hours = val
                    break

            if not hours:
                # 抓取數字小時
                num_match = re.search(r"(\d+)\s*(小時|h|hr)?", clean_text)
                if num_match:
                    raw_num = int(num_match.group(1))
                    # 智能對齊到最近的合法區間 (6, 12, 18, 24, 36)
                    valid_hours = [6, 12, 18, 24, 36]
                    hours = min(valid_hours, key=lambda x: abs(x - raw_num))

            # 判斷場站名稱
            target_station = "全部"
            if not ("全部" in clean_text or "所有" in clean_text or "每場" in clean_text):
                stn = match_station_name(clean_text, allowed_stations)
                if stn:
                    target_station = stn

            if hours in [6, 12, 18, 24, 36]:
                return {
                    "intent": "set_cooldown",
                    "station_name": target_station,
                    "alert_type": alert_type,
                    "cooldown_hours": hours
                }

        # 4. 車位門檻設定意圖
        # 範例：「幫我把撫遠場車位少於 5 個時警報」、「復興場車位小於 3 或大於 30 通知我」、「安和站車位剩不到 10 格就提醒，上限不用設」
        if "車位" in clean_text or "停車位" in clean_text or "格" in clean_text:
            # 停用/關閉判斷
            if re.search(r"(關閉|停用|不要警報|不警報|取消)", clean_text):
                matched_stn = None
                if allowed_stations:
                    for s in allowed_stations:
                        short_name = s.replace("場", "").replace("站", "").replace("停車場", "")
                        if short_name and short_name in clean_text:
                            matched_stn = s
                            break
                if matched_stn:
                    return {
                        "intent": "set_space",
                        "station_name": matched_stn,
                        "lower": "無",
                        "upper": "無"
                    }

            # 擷取下限與上限
            lower_val = "無"
            upper_val = "無"

            # 下限特徵 (少於/小於/低於/剩不到/剩下 X 格)
            low_match = re.search(r"(少於|小於|低於|剩不到|剩下|下限|不足|剩餘)\s*(\d+)", clean_text)
            if low_match:
                lower_val = low_match.group(2)

            # 上限特徵 (多於/大於/高於/超過/上限 X 格)
            up_match = re.search(r"(多於|大於|高於|超過|上限|達|滿)\s*(\d+)", clean_text)
            if up_match:
                upper_val = up_match.group(2)

            # 單一數字處理 (例：「撫遠車位 5」預設為下限 5)
            if lower_val == "無" and upper_val == "無":
                single_match = re.search(r"(?:車位|車位數)\D*(\d+)\s*(?:格|位)?", clean_text)
                if single_match:
                    lower_val = single_match.group(1)

            # 匹配場站
            matched_stn = match_station_name(clean_text, allowed_stations)

            if matched_stn and (lower_val != "無" or upper_val != "無"):
                return {
                    "intent": "set_space",
                    "station_name": matched_stn,
                    "lower": lower_val,
                    "upper": upper_val
                }

        return None

    @staticmethod
    def parse_with_gemini(
        text: str,
        allowed_stations: List[str],
        gemini_api_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        第 2 軌：Google Gemini 2.0 Flash 智能意圖解析
        """
        if not gemini_api_key:
            return None

        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_api_key}"

        stations_str = "、".join(allowed_stations[:30]) if allowed_stations else "（無限定場站）"

        system_prompt = f"""
你是一個專業的停車場管理 LINE Bot 自然語言助理。
使用者會用各種口語中文提出場站設定需求。請將其轉換為標準 JSON 格式。

【使用者可管理的場站清單】：
{stations_str}

【支援的意圖 intent】：
1. "set_space"：設定場站可用車位警報門檻。
   參數：
   - "station_name": 場站名稱 (必須最貼近上述清單中的名稱)
   - "lower": 車位下限整數，或 "無" (例如小於/剩不到 5 格即為 5)
   - "upper": 車位上限整數，或 "無" (例如大於/超過 30 格即為 30)
2. "set_cooldown"：設定場站警報防洗版冷卻時間。
   參數：
   - "station_name": 場站名稱，或是 "全部"
   - "alert_type": "車位" 或 "斷線"
   - "cooldown_hours": 必須是 6, 12, 18, 24, 36 其中之一（若說半天對應12，一天對應24，一天半對應36）
3. "query"：查詢場站目前設定。
4. "help"：詢問使用說明或教學。
5. "chat"：無法辨識為上述管理指令之一般問答。

請直接回傳純 JSON，不要包含任何 markdown 標記。
"""
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": f"系統指令：\n{system_prompt}\n\n使用者輸入：{text}"}
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.1
            }
        }

        try:
            resp = requests.post(endpoint, json=payload, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                text_out = data["candidates"][0]["content"]["parts"][0]["text"]
                clean_json_str = re.sub(r"^```json\s*|\s*```$", "", text_out.strip())
                parsed = json.loads(clean_json_str)
                logger.info(f"Gemini parsed intent successfully: {parsed}")
                return parsed
            else:
                logger.warning(f"Gemini API returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Error calling Gemini API: {e}")

        return None

    @classmethod
    def understand_user_message(
        cls,
        text: str,
        allowed_stations: Optional[List[str]] = None,
        gemini_api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        雙軌融合入口：
        優先使用本機高速第 1 軌，若未命中則自動啟用第 2 軌 Gemini AI
        """
        # 第 1 軌：快速語意規則
        rule_res = cls.parse_with_regex(text, allowed_stations=allowed_stations)
        if rule_res:
            logger.info(f"NLP Track 1 (Rule-based) matched: {rule_res}")
            return rule_res

        # 第 2 軌：Gemini 2.0 Flash
        api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
        if api_key and allowed_stations:
            ai_res = cls.parse_with_gemini(text, allowed_stations, api_key)
            if ai_res and ai_res.get("intent") in ["set_space", "set_cooldown", "query", "help"]:
                logger.info(f"NLP Track 2 (Gemini AI) matched: {ai_res}")
                return ai_res

        return {"intent": "chat"}
