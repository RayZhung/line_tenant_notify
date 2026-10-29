---
name: line-bot-nlu
description: >-
  Guides the design, extension, and validation of Traditional Chinese LINE Bot
  natural language understanding (dual-track <1ms regex engine + Gemini
  Interactions API structured output) and LINE Messaging API Flex Message
  schemas. Use this skill whenever adding or debugging natural language
  commands, multi-station intents, compound thresholds, or LINE Flex cards.
---

# LINE Bot Traditional Chinese NLU & Flex Schema Skill

## 1. Dual-Track NLU Architecture (方案 B)

To keep LINE Bot webhook latency under `< 1ms` for 95%+ of user utterances while gracefully handling complex phrasing, typos, and inverted Chinese sentences:

### Track 1: Zero-Key Local Semantic Engine (`< 1ms`)
1. **Candidate Pool Injection**:
   - NEVER restrict `allowed_stations` to already-subscribed stations alone.
   - Always pass the tenant's full `candidate_sites` (`[{"station_code": "36", "station_name": "前金成功場", ...}]`) merged with `user_subscriptions` so unsubscribed stations can be recognized during `add_subscription` or compound commands.
2. **Multi-Station Entity Extraction (`extract_stations`)**:
   - **Mask non-station numbers first**: Before matching numeric `station_code` values (`01`~`99`), strip or mask threshold and duration expressions (e.g., `少於 5 格`, `超過 30 位`, `12 小時`, `24h`) so numbers like `5` or `12` are never misidentified as `[05]` or `[12]`.
   - **3-Pass Hierarchical Matching**:
     1. Exact full `station_name` or suffix-stripped core name (`停車場`/`停車站`/`場`/`站` removed, length >= 2), ordered by longest match first and masked to avoid overlapping sub-matches.
     2. Explicit numeric station codes (`[36]`, `36號`, `代號36`, or whitespace/comma/conjunction-delimited codes in add/remove utterances).
     3. Unique or longest `>= 2` character distinctive core substrings (excluding generic stop-words like `停車`, `車位`, `場站`, `監控`, `管理`).
   - Preserve user mention order and deduplicate results.
3. **Intent Priority Order**:
   - `rebind` (重新綁定 / 切換企業)
   - `help` (教學 / 說明 / 怎麼用)
   - `list_candidates` (有哪些場站可以選 / 候選清單 / 全部停車場)
   - `query` (我的監控 / 目前設定 / 查一下狀態)
   - `remove_subscription` (取消監控 / 移除 / 退訂 / 不要追蹤 — ensure `取消...車位警報` routes to `set_space` with `"無"`, NOT `remove_subscription`)
   - `add_subscription` (列入監控 / 加入 / 訂閱 / 追蹤 / 幫我盯 — supports optional compound `space_setting` and `cooldown_setting` in the same utterance)
   - `set_cooldown` (冷卻 / 靜音 / 間隔 + `6|12|18|24|36` hours or `半天`/`一天`/`一天半`)
   - `set_space` (車位 / 空位 / 滿場 + lower/upper bounds)
4. **Homophone & Connector Tolerance (Chinese IME Guardrails)**:
   - Normalize common Zhuyin/Pinyin homophones before matching: `下線` -> `下限`, `上線` -> `上限` (excluding `主機/設備/網路上線`), and `廠` -> `場` during station matching (e.g., `樹林俊興廠` -> `樹林俊興場`, `市府廠` -> `市府場`).
   - Always allow optional linking connectors between threshold/cooldown keywords and numbers: `(?:設為|改為|改成|設成|調為|調成|調整為|調整到|定為|為|是|設|改|調|到|至|[:：=])?` so phrases like `下線為5上限為15` parse both bounds accurately.
   - Track `has_lower` and `has_upper` flags explicitly: when a user updates only one bound (e.g., `把樹林俊興場上限改為 20` or `把上限取消`), preserve the station's existing value for the unmentioned bound instead of overwriting it with `"無"`.
5. **Timezone Consistency (`UTC+8`)**:
   - Serverless runtimes (Vercel / GitHub Actions) default to `UTC+0`. Always generate naive Taiwan timestamps via `datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)` for user-facing cards, monthly quota rollover, and cooldown comparisons.

### Track 2: Gemini Structured Output Fallback (`google-genai >= 2.3.0`)
- Trigger ONLY when Track 1 returns `None` and `os.getenv("GEMINI_API_KEY")` is configured (ISO27001: never hardcode API keys; never pass API keys in URL query strings).
- Use current models (`gemini-3.5-flash-lite` by default for low webhook latency, or `gemini-3.8-flash` via `GEMINI_MODEL` env var). Never use deprecated `gemini-2.0-*` or `gemini-1.5-*` models.
- Call `client.interactions.create(...)` with `store=False` and `response_format={"type": "text", "mime_type": "application/json", "schema": ...}` returning normalized `intent`, `stations` (`list[str]`), `lower`, `upper`, `alert_type`, and `cooldown_hours`.

---

## 2. Multi-Station & Compound Execution Rules

1. **Batch Add / Remove & Safe Clear-All Confirmation**:
   - When multiple stations are extracted (e.g., `幫我把前金成功場跟青埔二場列入監控` or `/新增 36 37`), process each station sequentially against live quota (`effective_max`), recording succeeded and skipped items (already subscribed, not found, or quota full) into a single consolidated Flex result bubble.
   - When the user asks to remove/clear **ALL** monitored stations (e.g., `解除全部的廠站監控`, `清空監控清單`, `/移除 全部`), NEVER delete immediately: return `action="confirm_clear_all"` (`build_clear_all_confirm_flex_bubble`) with `/確認清空監控` and `/取消清空監控` buttons, and keep enterprise binding (`多租戶權限表`) untouched upon confirmation.
2. **Compound Auto-Subscribe + Configure**:
   - When a user says `幫我把前金成功場列入監控，車位少於 8 格通知我` (or sets thresholds on a valid candidate station while simultaneously requesting monitoring), subscribe the station first (within quota) and immediately apply the requested `space_lower`/`space_upper` and/or `cooldown_hours`.
3. **Group Smart Silent Gate (`is_group=True` & Multi-Group Isolation)**:
   - Use `group_id` (`C...`) or `room_id` (`R...`) as the tenant binding & subscription target in group chats so multiple groups can bind different enterprise auth codes without colliding.
   - In unbound groups, return `action="silent_ignore"` on casual chat; only return `auth_success` on a valid auth code or `auth_required` on `/` commands / `綁定|授權碼` keywords.
   - In bound groups, filter out bare single-word human chat (`?`, `說明`, `查一下`, `換公司`, bare numbers without monitoring context), gate Track 2 (`parse_with_gemini`) behind `NLPService.is_potential_group_command`, and return `action="silent_ignore"` whenever no management intent matches.
   - Account for group push billing via `LineService.get_target_push_cost(target_id)` when incrementing monthly push count.

---

## 3. LINE Messaging API Flex Schema Guardrails

LINE rejects invalid Flex JSON with `HTTP 400 Bad Request` and silently drops the reply in chat. Always follow these rules:

- **`margin` and `spacing` allowed tokens**: `"none"`, `"xs"`, `"sm"`, `"md"`, `"lg"`, `"xl"`, `"xxl"` (or explicit pixel strings like `"4px"`). **NEVER use `"xxs"` on `margin` or `spacing`!**
- **`size` (text) allowed tokens**: `"xxs"`, `"xs"`, `"sm"`, `"md"`, `"lg"`, `"xl"`, `"xxl"`, `"3xl"`, `"4xl"`, `"5xl"`.
- **Carousel limits**: Maximum `12` bubbles per Flex `carousel`; keep vertical station rows at `<= 35` per bubble to stay well below payload size limits.
- **Validation Step**:
  Whenever adding or modifying a Flex builder in `services/line_service.py`, validate the generated bubble/carousel via `POST https://api.line.me/v2/bot/message/validate/reply` using `LINE_CHANNEL_ACCESS_TOKEN` (expect `HTTP 200 {}`) and run `tests/test_super_admin_and_warning.py`.

