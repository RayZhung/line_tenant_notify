"""
自然語言意圖理解與多場站實體萃取服務 (NLP & AI Intent Service - 方案 B 雙軌制)
架構：
  - 第 1 軌：內建極速中文語意規則與多場站模糊比對引擎 (< 1ms 零外部依賴、零金鑰)
  - 第 2 軌：Google Gemini Interactions API (gemini-3.5-flash-lite / gemini-3.8-flash) 結構化 JSON 備援
"""
import os
import re
import json
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("nlp_service")

TIME_SEMANTIC_MAP = {
    "一天半": 36,
    "半天": 12,
    "一天": 24,
    "整天": 24,
    "一日": 24,
    "36小時": 36,
    "24小時": 24,
    "18小時": 18,
    "12小時": 12,
    "6小時": 6,
    "36h": 36,
    "24h": 24,
    "18h": 18,
    "12h": 12,
    "6h": 6,
}

# 避免將通用指令詞誤判為 2 字場站簡稱
STOP_SUBSTRINGS = {
    "停車", "車位", "空位", "場站", "車站", "監控", "管理", "警報",
    "通知", "冷卻", "靜音", "上限", "下限", "新增", "移除", "加入",
    "取消", "查詢", "設定", "全部", "所有", "清單", "名單", "追蹤",
    "訂閱", "退訂", "目前", "小時", "斷線", "離線", "連線", "系統",
}

NLU_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "add_subscription",
                "remove_subscription",
                "list_candidates",
                "query",
                "set_space",
                "set_cooldown",
                "help",
                "rebind",
                "chat",
            ],
            "description": "使用者口語對應的操作意圖",
        },
        "stations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "目標場站完整名稱陣列（須對應候選場站清單；若指全部場站則填 ['全部']）",
        },
        "lower": {
            "type": "string",
            "description": "車位下限整數（如 '5'）或 '無'；未提及車位門檻時填 ''",
        },
        "upper": {
            "type": "string",
            "description": "車位上限整數（如 '30'）或 '無'；未提及車位門檻時填 ''",
        },
        "alert_type": {
            "type": "string",
            "enum": ["車位", "斷線", ""],
            "description": "冷卻警報類別（車位或斷線），未提及時填 ''",
        },
        "cooldown_hours": {
            "type": "integer",
            "description": "冷卻時數，僅限 0(未提及) 或 6, 12, 18, 24, 36",
        },
    },
    "required": ["intent", "stations"],
}


def _strip_station_suffix(name: str) -> str:
    """去除場站常見尾綴（保留至少 2 個核心字元）"""
    clean = re.sub(r"^\[\s*\d+\s*\]\s*", "", name.strip())
    for suffix in ("立體停車場", "地下停車場", "平面停車場", "停車場", "停車站", "場", "站"):
        if clean.endswith(suffix) and len(clean) - len(suffix) >= 2:
            return clean[: -len(suffix)]
    return clean


def _normalize_station_pool(candidate_stations: Optional[List[Any]]) -> List[Dict[str, str]]:
    """
    將字串清單或候選場站 Dict 清單統一轉為標準結構:
    [{"code": "36", "name": "前金成功場", "core": "前金成功"}]
    """
    if not candidate_stations:
        return []

    pool: List[Dict[str, str]] = []
    seen = set()

    for item in candidate_stations:
        code = ""
        name = ""
        if isinstance(item, dict):
            raw_code = str(item.get("station_code", "")).strip()
            if raw_code.isdigit():
                code = f"{int(raw_code):02d}"
            else:
                code = raw_code
            name = str(item.get("station_name", "")).strip()
        elif isinstance(item, str):
            raw_s = item.strip()
            m = re.match(r"^\[\s*(\d{1,3})\s*\]\s*(.+)$", raw_s)
            if m:
                code = f"{int(m.group(1)):02d}"
                name = m.group(2).strip()
            else:
                name = raw_s

        if not name or name in seen:
            continue
        seen.add(name)
        pool.append({
            "code": code,
            "name": name,
            "core": _strip_station_suffix(name),
        })

    return pool


def _mask_non_station_numbers(text: str) -> str:
    """
    在比對場站數字代號前，先將「車位格數」與「冷卻小時數」等數值等長遮罩為空白，
    防止『車位少於 5 格』或『靜音 12 小時』的 5 / 12 被誤判為 [05] / [12] 號場站。
    """
    patterns = [
        r"(?:少於|小於|低於|剩不到|剩下|下限|不足|剩餘|多於|大於|高於|超過|上限|達|滿|不到|<|＜|>|＞|<=|>=)\s*\d+\s*(?:格|位|個|台|輛)?",
        r"\d+\s*(?:格|位|個車位|個空位|車位|空位|小時|h|hr|hrs|分鐘|分|天|日)",
        r"(?:車位數|車位|空位|間隔|冷卻|靜音)\s*(?:改為|設為|調整為|改成|設成|改|設)?\s*\d+",
    ]
    masked = text
    for pat in patterns:
        masked = re.sub(pat, lambda m: " " * len(m.group(0)), masked, flags=re.IGNORECASE)
    return masked


def extract_stations(
    text: str,
    candidate_stations: Optional[List[Any]],
    allow_bare_codes: bool = False
) -> List[str]:
    """
    多場站智能萃取引擎 (保留使用者口語提及順序並自動去重)
    支援：
      1. 完整場站名稱 / 去尾綴核心名稱 (如「前金成功場」、「前金成功」、「青埔二」)
      2. 場站代號 (如「[36]」、「36號」、「代號 03」，以及新增/移除語境下之「36 37」)
      3. 具辨識度之 2 字以上簡稱 (如「撫遠」->「松山撫遠場」、「水源」->「樹林水源場」)
    """
    pool = _normalize_station_pool(candidate_stations)
    if not pool or not text:
        return []

    working = text
    hits: List[Tuple[int, str]] = []
    matched_names = set()

    # Pass 1: 完整名稱與去尾綴核心名稱 (由長到短優先匹配並等長遮罩)
    pass1_candidates: List[Tuple[str, str]] = []
    for entry in pool:
        pass1_candidates.append((entry["name"], entry["name"]))
        if entry["core"] != entry["name"] and len(entry["core"]) >= 2:
            pass1_candidates.append((entry["core"], entry["name"]))
    pass1_candidates.sort(key=lambda x: len(x[0]), reverse=True)

    for token, full_name in pass1_candidates:
        if not token:
            continue
        pos = working.find(token)
        if pos != -1:
            if full_name not in matched_names:
                hits.append((pos, full_name))
                matched_names.add(full_name)
            working = working[:pos] + (" " * len(token)) + working[pos + len(token):]

    # Pass 2: 場站代號匹配 (先遮罩門檻與冷卻時數數字)
    code_map: Dict[str, str] = {}
    for entry in pool:
        if entry["code"]:
            code_map[entry["code"]] = entry["name"]

    if code_map:
        masked_for_codes = _mask_non_station_numbers(working)
        explicit_code_patterns = [
            r"\[\s*(\d{1,3})\s*\]",
            r"(?:代號|編號|第)\s*(\d{1,3})(?!\d)",
            r"(?<!\d)(\d{1,3})\s*(?:號場站|號停車場|號場|號站|號)",
        ]
        if allow_bare_codes:
            explicit_code_patterns.append(r"(?<!\d)(\d{1,3})(?!\d)")

        for pat in explicit_code_patterns:
            for m in re.finditer(pat, masked_for_codes):
                raw_num = m.group(1)
                norm_code = f"{int(raw_num):02d}"
                if norm_code in code_map:
                    full_name = code_map[norm_code]
                    if full_name not in matched_names:
                        hits.append((m.start(), full_name))
                        matched_names.add(full_name)
                    span_s, span_e = m.span()
                    masked_for_codes = (
                        masked_for_codes[:span_s]
                        + (" " * (span_e - span_s))
                        + masked_for_codes[span_e:]
                    )
                    working = (
                        working[:span_s]
                        + (" " * (span_e - span_s))
                        + working[span_e:]
                    )

    # Pass 3: 具唯一性或 >= 3 字之核心子片段比對 (如「撫遠」->「松山撫遠場」)
    sub_owner_map: Dict[str, List[str]] = {}
    for entry in pool:
        core = entry["core"]
        c_len = len(core)
        for length in range(c_len, 1, -1):
            for i in range(c_len - length + 1):
                sub = core[i:i + length]
                if sub in STOP_SUBSTRINGS:
                    continue
                owners = sub_owner_map.setdefault(sub, [])
                if entry["name"] not in owners:
                    owners.append(entry["name"])

    # 只採用「唯一對應單一場站」的簡稱詞，或長度 >= 3 的片段 (由長到短)
    unique_subs: List[Tuple[str, str]] = []
    for sub, owners in sub_owner_map.items():
        if len(owners) == 1:
            unique_subs.append((sub, owners[0]))
    unique_subs.sort(key=lambda x: len(x[0]), reverse=True)

    for sub, full_name in unique_subs:
        if full_name in matched_names:
            continue
        pos = working.find(sub)
        if pos != -1:
            hits.append((pos, full_name))
            matched_names.add(full_name)
            working = working[:pos] + (" " * len(sub)) + working[pos + len(sub):]

    hits.sort(key=lambda x: x[0])
    return [name for _, name in hits]


def match_station_name(text: str, allowed_stations: Optional[List[Any]]) -> Optional[str]:
    """
    向下相容單一場站匹配介面：回傳口語中提及的第一座場站名稱
    """
    matched = extract_stations(text, allowed_stations, allow_bare_codes=False)
    return matched[0] if matched else None


class NLPService:
    @staticmethod
    def _extract_space_thresholds(clean_text: str, is_add_or_remove: bool = False) -> Optional[Dict[str, str]]:
        """
        從口語中萃取車位上下限門檻 (支援『少於 5 格』、『超過 30 格』、『滿場』、『關閉車位警報』)
        """
        has_space_word = bool(re.search(r"(車位|停車位|空位|剩餘|剩不到|滿場|客滿|沒車位|沒位子|\d+\s*格)", clean_text))
        if not has_space_word:
            return None

        # 關閉車位警報 (需排除「取消監控場站」本身)
        if not is_add_or_remove and re.search(r"(關閉|停用|不要警報|不警報|取消)", clean_text):
            return {"lower": "無", "upper": "無"}

        lower_val = "無"
        upper_val = "無"

        # 滿場語意：剩餘車位 < 1 (即 0 格) 時觸發
        if re.search(r"(滿場|客滿|沒車位|沒位子|車位為\s*0|剩\s*0\s*格)", clean_text):
            lower_val = "1"

        low_match = re.search(r"(?:少於|小於|低於|剩不到|剩下|下限|不足|剩餘|不到|<|＜)\s*(\d+)", clean_text)
        if low_match:
            lower_val = low_match.group(1)
        elif lower_val == "無":
            below_match = re.search(r"(\d+)\s*(?:格|位|個)?\s*(?:以下|以內)", clean_text)
            if below_match:
                lower_val = below_match.group(1)

        up_match = re.search(r"(?:多於|大於|高於|超過|上限|達|>|＞)\s*(\d+)", clean_text)
        if up_match:
            upper_val = up_match.group(1)
        else:
            above_match = re.search(r"(\d+)\s*(?:格|位|個)?\s*以上", clean_text)
            if above_match:
                upper_val = above_match.group(1)

        if lower_val == "無" and upper_val == "無":
            single_match = re.search(r"(?:車位數|車位|空位)\D{0,6}?(\d+)\s*(?:格|位|個)?", clean_text)
            if single_match:
                lower_val = single_match.group(1)

        if lower_val != "無" or upper_val != "無":
            return {"lower": lower_val, "upper": upper_val}
        return None

    @staticmethod
    def _extract_cooldown_params(clean_text: str) -> Optional[Dict[str, Any]]:
        """
        從口語中萃取防洗版冷卻類別與時數 (6/12/18/24/36 小時)
        """
        if not re.search(r"(冷卻|靜音|不要吵|安靜|間隔|暫停通知|勿擾)", clean_text):
            return None

        alert_type = "斷線" if re.search(r"(斷線|連線|離線|網路)", clean_text) else "車位"
        hours = None

        for sem_word, val in TIME_SEMANTIC_MAP.items():
            if sem_word in clean_text:
                hours = val
                break

        if not hours:
            num_match = re.search(r"(\d+)\s*(?:小時|h|hr|hrs)", clean_text, re.IGNORECASE)
            if not num_match:
                num_match = re.search(r"(?:冷卻|靜音|間隔|安靜)\D{0,6}?(\d+)", clean_text)
            if num_match:
                raw_num = int(num_match.group(1))
                valid_hours = [6, 12, 18, 24, 36]
                hours = min(valid_hours, key=lambda x: abs(x - raw_num))

        if hours in [6, 12, 18, 24, 36]:
            return {"alert_type": alert_type, "cooldown_hours": hours}
        return None

    @staticmethod
    def parse_with_regex(
        text: str,
        allowed_stations: Optional[List[Any]] = None,
        candidate_stations: Optional[List[Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        第 1 軌：本機端極速中文語意與多場站萃取 (< 1ms)
        """
        clean_text = text.strip()
        if not clean_text:
            return None

        # 合併完整場站詞庫 (優先使用企業全部可選候選場站，兼容舊版單傳 allowed_stations)
        full_pool: List[Any] = []
        if candidate_stations:
            full_pool.extend(candidate_stations)
        if allowed_stations:
            full_pool.extend(allowed_stations)
        sub_pool = allowed_stations if allowed_stations else full_pool

        # 0. 重新綁定 / 切換企業意圖
        if re.search(r"(重新綁定|切換企業|換企業|換公司|重新輸入授權碼|解除企業綁定)", clean_text):
            return {"intent": "rebind"}

        # 1. 教學 / 說明意圖
        if re.search(r"^(說明|教學|怎麼用|如何使用|功能介紹|指令手冊|有哪些指令|怎麼操作|\?|？|help)$", clean_text, re.IGNORECASE) or \
           re.search(r"(怎麼設定|怎麼使用|教我用|指令說明|操作教學)", clean_text):
            return {"intent": "help"}

        # 2. 列出可選候選場站清單意圖
        if re.search(
            r"(可選場站|候選場站|有哪些場站可以選|有哪些站可以選|有哪些站能選|可以選哪些|能選哪些|有哪些停車場可以|可監控清單|全部有哪些場站|所有可選|給我場站名單|看可選|選場站清單)",
            clean_text,
        ):
            return {"intent": "list_candidates"}

        # 3. 移除監控場站意圖 (須排除「取消車位警報」這類門檻關閉句)
        is_space_cancel = bool(re.search(r"(車位|空位|門檻|上限|下限)", clean_text)) and not bool(
            re.search(r"(取消監控|移除監控|退出監控|停止監控|從監控)", clean_text)
        )
        has_remove_verb = bool(
            re.search(
                r"(取消監控|取消追蹤|取消訂閱|退訂|退出監控|停止監控|不要監控|不用監控|不再監控|不要追蹤|移出監控|從監控移除|從清單移除|移除監控|刪除監控)",
                clean_text,
            )
        ) or (
            not is_space_cancel
            and bool(re.search(r"(移除|刪除|刪掉|拿掉|退訂|取消追蹤|取消訂閱)", clean_text))
        )

        if has_remove_verb:
            rm_stations = extract_stations(clean_text, sub_pool, allow_bare_codes=True)
            if not rm_stations and full_pool:
                rm_stations = extract_stations(clean_text, full_pool, allow_bare_codes=True)
            if rm_stations:
                return {
                    "intent": "remove_subscription",
                    "stations": rm_stations,
                    "station_name": rm_stations[0],
                }

        # 4. 新增 / 列入監控場站意圖 (支援多場站 + 複合車位門檻 / 冷卻設定)
        has_add_verb = bool(
            re.search(
                r"(新增|加入|列入|納入|加進|加到|放進|訂閱|追蹤|關注|開啟監控|開始監控|幫我盯|幫我看著|我想監控|我要監控|幫忙監控|幫我監控|納入監控|列入追蹤)",
                clean_text,
            )
        )
        if has_add_verb:
            add_stations = extract_stations(clean_text, full_pool, allow_bare_codes=True)
            if add_stations:
                result: Dict[str, Any] = {
                    "intent": "add_subscription",
                    "stations": add_stations,
                    "station_name": add_stations[0],
                }
                space_cfg = NLPService._extract_space_thresholds(clean_text, is_add_or_remove=True)
                if space_cfg:
                    result["space_setting"] = space_cfg
                cool_cfg = NLPService._extract_cooldown_params(clean_text)
                if cool_cfg:
                    result["cooldown_setting"] = cool_cfg
                return result

        # 5. 查詢已監控清單 / 設定總覽意圖
        if re.search(r"^(查|查詢|看|列出|有哪些場站|場站清單|目前設定|門檻設定|設定總覽|狀態|我的監控|我的場站)$", clean_text) or \
           re.search(r"(查一下|幫我看|目前的設定|有哪些站|我監控了哪些|目前已監控|我的監控清單|查看監控|看我的場站)", clean_text):
            return {"intent": "query"}

        # 6. 冷卻防洗版設定意圖 (支援單場、多場或全部)
        cool_cfg = NLPService._extract_cooldown_params(clean_text)
        if cool_cfg:
            if re.search(r"(全部|所有|每場|每一場|全場站)", clean_text):
                target_stations = ["全部"]
            else:
                target_stations = extract_stations(clean_text, full_pool, allow_bare_codes=False)
                if not target_stations:
                    target_stations = ["全部"]
            return {
                "intent": "set_cooldown",
                "stations": target_stations,
                "station_name": target_stations[0],
                "alert_type": cool_cfg["alert_type"],
                "cooldown_hours": cool_cfg["cooldown_hours"],
            }

        # 7. 車位上下限設定意圖 (支援單場或多場；若指定之場站尚未訂閱但屬於合法候選清單，交由上層自動訂閱並套用)
        space_cfg = NLPService._extract_space_thresholds(clean_text, is_add_or_remove=False)
        if space_cfg:
            target_stations = extract_stations(clean_text, full_pool, allow_bare_codes=False)
            if target_stations:
                return {
                    "intent": "set_space",
                    "stations": target_stations,
                    "station_name": target_stations[0],
                    "lower": space_cfg["lower"],
                    "upper": space_cfg["upper"],
                }

        return None

    @staticmethod
    def parse_with_gemini(
        text: str,
        candidate_stations: List[Any],
        gemini_api_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        第 2 軌：Google Gemini Interactions API (google-genai >= 2.3.0) 結構化 JSON 意圖解析
        遵循 ISO27001：金鑰僅由環境變數注入 SDK，且 store=False 不留存外部對話紀錄
        """
        if not gemini_api_key:
            return None

        pool = _normalize_station_pool(candidate_stations)
        stations_str = "、".join(
            [f"[{p['code']}]{p['name']}" if p["code"] else p["name"] for p in pool[:99]]
        ) if pool else "（無限定場站）"

        model_name = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip() or "gemini-3.5-flash-lite"

        prompt = (
            "你是一個專業的台灣停車場管理 LINE Bot 自然語言解析器。\n"
            "請將管理員的繁體中文口語需求（可能含錯別字、簡稱、多座場站或複合門檻設定）解析為指定 JSON 格式。\n\n"
            f"【該企業合法候選場站清單】：\n{stations_str}\n\n"
            "【解析規則】：\n"
            "1. 新增/列入/加入/訂閱/追蹤監控場站 -> intent='add_subscription'，stations 填入對應的完整『場站名稱』（不含 [代號] 前綴，可多座）。若同句還要求設定車位上下限或冷卻時數，一併填入 lower/upper/alert_type/cooldown_hours。\n"
            "2. 取消監控/移除/退訂/不要監控場站 -> intent='remove_subscription'，stations 填入對應完整場站名稱（可多座）。\n"
            "3. 詢問有哪些場站可以挑選/候選清單 -> intent='list_candidates'。\n"
            "4. 查詢自己目前已監控的場站或門檻設定 -> intent='query'。\n"
            "5. 設定車位上下限門檻（如少於5格、滿場設 lower='1'、關閉門檻設 lower='無', upper='無'）-> intent='set_space'。\n"
            "6. 設定防洗版冷卻間隔（僅限 6, 12, 18, 24, 36 小時；半天=12、一天=24、一天半=36）-> intent='set_cooldown'。\n"
            "7. 詢問教學或使用方式 -> intent='help'；要求更換或重新輸入企業授權碼 -> intent='rebind'；無關閒聊 -> intent='chat'。\n\n"
            f"【使用者輸入】：{text}"
        )

        try:
            from google import genai
            client = genai.Client(api_key=gemini_api_key)
            interaction = client.interactions.create(
                model=model_name,
                input=prompt,
                store=False,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": NLU_JSON_SCHEMA,
                },
            )
            raw_json = (interaction.output_text or "").strip()
            if not raw_json:
                return None

            parsed = json.loads(raw_json)
            intent = parsed.get("intent", "chat")
            raw_stations = parsed.get("stations") or []

            # 將模型回傳的場站名稱對齊回合法候選池標準名稱
            clean_stations: List[str] = []
            for s in raw_stations:
                s_clean = re.sub(r"^\[\s*\d+\s*\]\s*", "", str(s).strip())
                if s_clean == "全部":
                    clean_stations.append("全部")
                    continue
                matched = extract_stations(s_clean, pool, allow_bare_codes=True)
                if matched:
                    for m_name in matched:
                        if m_name not in clean_stations:
                            clean_stations.append(m_name)
                elif s_clean and s_clean not in clean_stations:
                    clean_stations.append(s_clean)

            parsed["stations"] = clean_stations
            if clean_stations:
                parsed["station_name"] = clean_stations[0]

            low = str(parsed.get("lower") or "").strip()
            up = str(parsed.get("upper") or "").strip()
            cd_hrs = parsed.get("cooldown_hours") or 0
            atype = str(parsed.get("alert_type") or "").strip()

            if intent == "add_subscription":
                if low or up:
                    parsed["space_setting"] = {
                        "lower": low if low else "無",
                        "upper": up if up else "無",
                    }
                if cd_hrs in [6, 12, 18, 24, 36]:
                    parsed["cooldown_setting"] = {
                        "alert_type": atype if atype in ["車位", "斷線"] else "車位",
                        "cooldown_hours": int(cd_hrs),
                    }
            elif intent == "set_space":
                parsed["lower"] = low if low else "無"
                parsed["upper"] = up if up else "無"
            elif intent == "set_cooldown":
                parsed["alert_type"] = atype if atype in ["車位", "斷線"] else "車位"
                if cd_hrs not in [6, 12, 18, 24, 36]:
                    parsed["cooldown_hours"] = 12

            logger.info(f"NLP Track 2 (Gemini {model_name}) parsed intent: {parsed}")
            return parsed
        except Exception as e:
            logger.error(f"Error calling Gemini Interactions API in NLP Track 2: {e}")
            return None

    @classmethod
    def understand_user_message(
        cls,
        text: str,
        allowed_stations: Optional[List[Any]] = None,
        candidate_stations: Optional[List[Any]] = None,
        gemini_api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        方案 B 雙軌融合入口：
          1. 優先執行第 1 軌 (< 1ms 本機極速中文語意引擎)
          2. 未命中且環境變數具備 GEMINI_API_KEY 時，自動啟用第 2 軌 Gemini 結構化意圖解析
        """
        rule_res = cls.parse_with_regex(
            text,
            allowed_stations=allowed_stations,
            candidate_stations=candidate_stations,
        )
        if rule_res:
            logger.info(f"NLP Track 1 (Local <1ms) matched: {rule_res}")
            return rule_res

        api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
        if api_key:
            merged_pool: List[Any] = []
            if candidate_stations:
                merged_pool.extend(candidate_stations)
            if allowed_stations:
                merged_pool.extend(allowed_stations)
            ai_res = cls.parse_with_gemini(text, merged_pool, api_key)
            valid_intents = {
                "add_subscription",
                "remove_subscription",
                "list_candidates",
                "query",
                "set_space",
                "set_cooldown",
                "help",
                "rebind",
            }
            if ai_res and ai_res.get("intent") in valid_intents:
                return ai_res

        return {"intent": "chat"}
