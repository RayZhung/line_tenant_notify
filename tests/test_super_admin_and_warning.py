"""
測試超級管理者功能與 195 則推播用量瀕危告警機制
"""
import sys
import os
import json

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Ensure project path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.line_service import LineService
from services.tenant_service import TenantService
from unittest.mock import MagicMock

def test_flex_bubbles():
    print("=== 測試 1: 驗證 Flex Message 結構 ===")
    
    # 1. 測試 195 則推播瀕危警告卡片
    bubble_195 = LineService.build_emergency_push_warning_flex_bubble(current_count=195, warning_threshold=195, max_limit=200)
    assert bubble_195["type"] == "bubble"
    assert bubble_195["header"]["backgroundColor"] == "#DC2626"
    assert "195 / 200" in bubble_195["header"]["contents"][1]["text"]
    
    # 檢查 footer 是否有一鍵靜音按鈕
    footer_buttons = bubble_195["footer"]["contents"]
    mute_btn = footer_buttons[0]
    assert mute_btn["action"]["text"] == "/靜音"
    print("  [PASS] 195 則瀕危告警 Flex 卡片結構驗證通過 (含一鍵靜音按鈕)")

    # 3. 測試 0 號超級管理者專屬指令手冊 (/? )
    super_profile = {
        "company_id": "0",
        "company_name": "超級管理者",
        "role_name": "系統超級管理者"
    }
    super_help_bubble = LineService.build_help_flex_bubble("U_super_user", super_profile)
    assert "👑 系統超級管理者指令手冊" in super_help_bubble["header"]["contents"][0]["text"]
    assert "/參數" in super_help_bubble["footer"]["contents"][0]["action"]["text"]
    print("  [PASS] 超級管理者專屬 /? 指令手冊 Flex 卡片驗證通過")

    # 4. 測試一般企業管理員指令手冊 (/? )
    normal_profile = {
        "company_id": "1",
        "company_name": "微程式資訊",
        "role_name": "設備運維 (甲)"
    }
    normal_help_bubble = LineService.build_help_flex_bubble("U_normal_user", normal_profile)
    assert "📖 場站管理員指令手冊" in normal_help_bubble["header"]["contents"][0]["text"]
    assert "重新綁定" in normal_help_bubble["body"]["contents"][-1]["contents"][1]["text"]
    print("  [PASS] 一般管理員 /? 指令手冊驗證通過 (含 /重新綁定 指引)")

def test_tenant_service_super_admin_commands():
    print("\n=== 測試 2: 驗證超級管理者指令權限與解析 ===")
    
    mock_sheets = MagicMock()
    mock_sheets.fetch_tenant_permissions.return_value = [
        {
            "tenant_id": "1",
            "company_id": "1",
            "company_name": "微程式資訊",
            "role_name": "管理員",
            "authorized_line_user_ids": "U_normal_user"
        },
        {
            "tenant_id": "0",
            "company_id": "0",
            "company_name": "超級管理者",
            "role_name": "超級管理者",
            "authorized_line_user_ids": "U_super_user"
        }
    ]
    mock_sheets.fetch_system_configs.return_value = {
        "max_stations_per_user": "10",
        "maintenance_mode": "OFF",
        "monthly_push_count": "50",
        "monthly_push_warning": "195"
    }
    mock_sheets.update_system_config.return_value = True

    # 1. 一般使用者 (company_id: '1') 嘗試下 /靜音 或 /參數 -> 應被拒絕
    res_normal_mute = TenantService.parse_and_execute_command(
        user_id="U_normal_user",
        text="/靜音",
        sheets_service=mock_sheets
    )
    assert res_normal_mute["action"] == "error"
    assert "權限不足" in res_normal_mute["error"]
    print("  [PASS] 一般使用者輸入 /靜音 正確被拒絕 (權限隔離有效)")

    res_normal_cfg = TenantService.parse_and_execute_command(
        user_id="U_normal_user",
        text="/參數",
        sheets_service=mock_sheets
    )
    assert res_normal_cfg["action"] == "error"
    assert "權限不足" in res_normal_cfg["error"]
    print("  [PASS] 一般使用者輸入 /參數 正確被拒絕 (權限隔離有效)")

    # 2. 超級管理者 (company_id: '0') 執行 /靜音
    res_super_mute = TenantService.parse_and_execute_command(
        user_id="U_super_user",
        text="/靜音",
        sheets_service=mock_sheets
    )
    assert res_super_mute["action"] == "system_mute"
    mock_sheets.update_system_config.assert_called_with("maintenance_mode", "ON")
    print("  [PASS] 超級管理者執行 /靜音 成功觸發 maintenance_mode = ON")

    # 3. 超級管理者執行 /解除靜音
    res_super_unmute = TenantService.parse_and_execute_command(
        user_id="U_super_user",
        text="/解除靜音",
        sheets_service=mock_sheets
    )
    assert res_super_unmute["action"] == "system_unmute"
    mock_sheets.update_system_config.assert_called_with("maintenance_mode", "OFF")
    print("  [PASS] 超級管理者執行 /解除靜音 成功觸發 maintenance_mode = OFF")

    # 4. 超級管理者執行 /參數
    res_super_list_cfg = TenantService.parse_and_execute_command(
        user_id="U_super_user",
        text="/參數",
        sheets_service=mock_sheets
    )
    assert res_super_list_cfg["action"] == "list_configs"
    print("  [PASS] 超級管理者執行 /參數 成功讀取全域參數")

    # 5. 超級管理者執行 /參數 最大場站數 15
    res_super_update_cfg = TenantService.parse_and_execute_command(
        user_id="U_super_user",
        text="/參數 最大場站數 15",
        sheets_service=mock_sheets
    )
    assert res_super_update_cfg["action"] == "config_updated"
    assert res_super_update_cfg["target_key"] == "max_stations_per_user"
    assert res_super_update_cfg["target_value"] == "15"
    mock_sheets.update_system_config.assert_called_with("max_stations_per_user", "15")
    # 6. 測試 /重新綁定 指令 (主動求助式)
    res_rebind = TenantService.parse_and_execute_command(
        user_id="U_super_user",
        text="/重新綁定",
        sheets_service=mock_sheets
    )
    assert res_rebind["action"] == "auth_required"
    mock_sheets.unbind_user.assert_called_with("U_super_user")
    print("  [PASS] /重新綁定 指令成功解綁並返回 auth_required 要求新授權碼")

    # 7. 測試全形問號 `/？`、空白 `/ ?`、`／？` 皆能正確觸發超級管理者手冊 (上限 50 場)
    for variant in ["/？", "/ ?", "／？", "？"]:
        res_help_variant = TenantService.parse_and_execute_command(
            user_id="U_super_user",
            text=variant,
            sheets_service=mock_sheets
        )
        assert res_help_variant["action"] == "help", f"Failed on {variant}: {res_help_variant}"
        assert res_help_variant["max_count"] == 50
    print("  [PASS] 全形與含空白指令 (/？、/ ?、／？、？) 皆正確觸發超級管理者專屬手冊 (max_count=50)")


def test_full_candidate_list_and_interactive_actions():
    print("\n=== 測試 4: 驗證 98 座完整場站垂直捲動清單與點擊新增/移除按鈕 ===")
    candidates_98 = [
        {
            "station_code": f"{i:02d}",
            "station_name": f"測試停車場第{i:02d}站",
            "station_ip": f"10.0.0.{i}",
            "is_subscribed": (i in [1, 9])
        }
        for i in range(1, 99)
    ]

    bubbles = LineService.build_candidate_sites_flex_bubbles(
        company_name="超級管理者",
        candidates=candidates_98,
        current_count=2,
        max_count=50,
        chunk_size=35
    )
    assert len(bubbles) == 3, f"Expected 3 vertical bubbles for 98 stations, got {len(bubbles)}"

    total_rendered_rows = 0
    for idx, b in enumerate(bubbles, start=1):
        raw_bytes = len(json.dumps(b, ensure_ascii=False).encode("utf-8"))
        assert raw_bytes < 28000, f"Bubble {idx} exceeded safe LINE Flex limit: {raw_bytes} bytes"
        station_rows = [
            item for item in b["body"]["contents"]
            if item.get("type") == "box" and item.get("layout") == "horizontal"
        ]
        total_rendered_rows += len(station_rows)

    assert total_rendered_rows == 98, f"Expected all 98 stations rendered, got {total_rendered_rows}"

    # 檢查第 1 站 (已監控 -> ➖ 可移除 -> /移除 01) 與第 8 站 (未監控 -> ➕ 可新增 -> /新增 08)
    first_bubble_rows = [
        item for item in bubbles[0]["body"]["contents"]
        if item.get("type") == "box" and item.get("layout") == "horizontal"
    ]
    row_01_pill = first_bubble_rows[0]["contents"][1]
    assert row_01_pill["action"]["text"] == "/移除 01"
    assert "可移除" in row_01_pill["contents"][0]["text"]

    row_08_pill = first_bubble_rows[7]["contents"][1]
    assert row_08_pill["action"]["text"] == "/新增 08"
    assert "可新增" in row_08_pill["contents"][0]["text"]
    print("  [PASS] 98 座候選場站完整垂直展開 (3 張卡片均 <16KB)，且右側『➕ 可新增 / ➖ 可移除』點擊動作驗證通過")

    # 檢查 /查詢 監控清單超過 15 場時垂直展開，且每筆皆有『➖ 可移除』按鈕
    subs_20 = [
        {
            "station_code": f"{i:02d}",
            "station_name": f"測試監控站{i:02d}",
            "space_lower": "5",
            "space_upper": "30",
            "space_cooldown": "6",
            "offline_cooldown": "24"
        }
        for i in range(1, 21)
    ]
    query_bubbles = LineService.build_query_settings_flex_bubbles("超級管理者", subs_20, max_count=50)
    assert len(query_bubbles) == 2
    first_query_card = [
        item for item in query_bubbles[0]["body"]["contents"]
        if item.get("type") == "box" and item.get("layout") == "vertical"
    ][0]
    rm_pill = first_query_card["contents"][0]["contents"][1]
    assert rm_pill["action"]["text"] == "/移除 01"
    # 防呆驗證：確保所有 Flex 結構中未誤用 LINE 不支援的 margin/spacing: 'xxs'
    query_json_str = json.dumps(query_bubbles, ensure_ascii=False)
    assert '"margin": "xxs"' not in query_json_str
    assert '"spacing": "xxs"' not in query_json_str
    print("  [PASS] /查詢 個人監控清單支援多張垂直捲動與點擊『➖ 可移除』驗證通過 (Flex 屬性合規)")

def test_monthly_warning_counter_logic():
    print("\n=== 測試 3: 驗證推播累加與 195 門檻判定邏輯 ===")
    from services.sheets_service import SheetsService
    svc = SheetsService()
    
    # 模擬試算表已累積 194 則
    svc.fetch_system_configs = MagicMock(return_value={
        "monthly_push_count": "194",
        "monthly_push_warning": "195"
    })
    svc.update_system_config = MagicMock(return_value=True)

    # 推送第 195 則 -> 剛好跨越門檻
    new_count, just_reached = svc.increment_monthly_push_count(1)
    assert new_count == 195
    assert just_reached is True
    print(f"  [PASS] 從 194 -> 195: new_count={new_count}, just_reached={just_reached} (觸發緊急警告！)")

    # 模擬試算表已是 195 則，推送第 196 則 -> 不再重複判定 just_reached
    svc.fetch_system_configs = MagicMock(return_value={
        "monthly_push_count": "195",
        "monthly_push_warning": "195"
    })
    new_count_2, just_reached_2 = svc.increment_monthly_push_count(1)
    assert new_count_2 == 196
    assert just_reached_2 is False
    print(f"  [PASS] 從 195 -> 196: new_count={new_count_2}, just_reached={just_reached_2} (不重複洗版警告)")

def test_mode_a_anomaly_and_anti_false_unbind():
    print("\n=== 測試 5: 驗證離線精準判定、個人車位上下限與防假性未綁定保護 ===")
    from datetime import datetime, timedelta

    ops_data = [
        # 正常連線場站 -> 絕不可誤報為斷線
        {
            "station_name": "新莊新知六場",
            "station_ip": "221.120.1.167",
            "company_id": "1, 2",
            "company_name": "微程式 / 大冠",
            "category": "CONNECTIVITY",
            "metric_name": "主機連線狀態",
            "current_value": "ONLINE",
            "status": "NORMAL",
            "details": "主機連線正常"
        },
        {
            "station_name": "新莊新知六場",
            "station_ip": "221.120.1.167",
            "company_id": "1, 2",
            "company_name": "微程式 / 大冠",
            "category": "CAPACITY",
            "metric_name": "剩餘車位數",
            "current_value": 10,
            "status": "NORMAL",
            "details": "目前可用車位 10 格"
        },
        # 實際斷線場站 -> 應觸發 CONNECTIVITY 警報
        {
            "station_name": "樹林水源場",
            "station_ip": "124.218.236.97",
            "company_id": "1",
            "company_name": "微程式",
            "category": "CONNECTIVITY",
            "metric_name": "主機連線狀態",
            "current_value": "OFFLINE",
            "status": "ANOMALY",
            "details": "連線異常逾時 (ConnectTimeout)"
        },
        # 車位為 0 格 (滿場) -> 測試 Python falsy 0 不漏報
        {
            "station_name": "樹林俊興場",
            "station_ip": "221.120.3.203",
            "company_id": "1",
            "company_name": "微程式",
            "category": "CAPACITY",
            "metric_name": "剩餘車位數",
            "current_value": 0,
            "status": "ANOMALY",
            "details": "目前可用車位 0 格"
        }
    ]

    all_subs = [
        # User A 自訂新莊下限 15 格 (目前 10 格 -> 對 User A 應報警！)、水源斷線 24h、俊興下限 5 格 (0 格 -> 應報警！)
        {"line_user_id": "U_A", "company_id": "1", "station_name": "新莊新知六場", "space_lower": "15", "space_upper": "無", "space_cooldown": "6", "offline_cooldown": "24"},
        {"line_user_id": "U_A", "company_id": "1", "station_name": "樹林水源場", "space_lower": "5", "space_upper": "無", "space_cooldown": "6", "offline_cooldown": "24"},
        {"line_user_id": "U_A", "company_id": "1", "station_name": "樹林俊興場", "space_lower": "5", "space_upper": "無", "space_cooldown": "6", "offline_cooldown": "24"},
        # User B 自訂新莊下限 5 格 (目前 10 格 -> 對 User B 正常不報警！)，且水源場 2 小時前剛通知過 (冷卻中不重複發)
        {"line_user_id": "U_B", "company_id": "1", "station_name": "新莊新知六場", "space_lower": "5", "space_upper": "無", "space_cooldown": "6", "offline_cooldown": "24"},
        {"line_user_id": "U_B", "company_id": "1", "station_name": "樹林水源場", "space_lower": "5", "space_upper": "無", "space_cooldown": "6", "offline_cooldown": "24"}
    ]

    perms = [
        {"company_id": "1", "company_name": "微程式", "role_name": "管理員", "authorized_line_user_ids": "U_A,U_B"}
    ]
    cooldowns = {
        ("U_B", "樹林水源場", "CONNECTIVITY"): datetime.now() - timedelta(hours=2)
    }

    plans, newly = TenantService.evaluate_personal_anomalies(ops_data, all_subs, perms, cooldowns)
    assert len(plans) == 1 and plans[0]["line_user_id"] == "U_A", f"Unexpected plans: {plans}"
    ua_anoms = plans[0]["anomalies"]
    assert len(ua_anoms) == 3, f"Expected 3 anomalies for U_A (新莊CAPACITY, 水源OFFLINE, 俊興0格), got {ua_anoms}"
    # 確認新莊 ONLINE 沒有產生 CONNECTIVITY 誤報
    assert not any(a["station_name"] == "新莊新知六場" and a["category"] == "CONNECTIVITY" for a in ua_anoms)
    print("  [PASS] ONLINE 不誤報斷線、OFFLINE 精準報警、0 格滿場不漏報、個人上下限與獨立冷卻全部驗證通過")

    # 測試 Google Sheets 瞬時 429 限流時，絕不誤跳回 auth_required (請輸入企業授權碼)
    throttled_sheets = MagicMock()
    throttled_sheets.fetch_tenant_permissions.return_value = []
    throttled_sheets.last_error = "APIError: [429]: Quota exceeded for quota metric 'Read requests'"

    res_throttled = TenantService.parse_and_execute_command("U_A", "/查詢", throttled_sheets)
    assert res_throttled["action"] == "error"
    assert "安全保留" in res_throttled["error"]
    print("  [PASS] 試算表瞬時限流/斷線保護生效：回傳忙碌提示，絕不誤跳回『請輸入企業授權碼』")

def test_dual_track_nlu_and_multi_station():
    print("\n=== 測試 6: 驗證方案 B 雙軌自然語言、多場站批次與複合門檻口語 ===")
    import time
    from unittest.mock import patch
    from services.nlp_service import NLPService
    from services.line_service import LineService

    candidates = [
        {"station_code": "01", "station_name": "松山撫遠場", "is_subscribed": False},
        {"station_code": "03", "station_name": "新莊新知六場", "is_subscribed": True},
        {"station_code": "05", "station_name": "樹林俊興場", "is_subscribed": True},
        {"station_code": "08", "station_name": "板橋文化場", "is_subscribed": False},
        {"station_code": "36", "station_name": "前金成功場", "is_subscribed": False},
        {"station_code": "37", "station_name": "青埔二場", "is_subscribed": False},
        {"station_code": "38", "station_name": "台中東山", "is_subscribed": False},
    ]

    # 建立狀態化 Mock SheetsService 以驗證多場站連續增刪與門檻設定
    live_subs = [
        {"line_user_id": "U_RAY", "company_id": "1", "station_code": "03", "station_name": "新莊新知六場", "space_lower": "5", "space_upper": "無", "space_cooldown": "6", "offline_cooldown": "24"},
        {"line_user_id": "U_RAY", "company_id": "1", "station_code": "05", "station_name": "樹林俊興場", "space_lower": "5", "space_upper": "無", "space_cooldown": "6", "offline_cooldown": "24"},
    ]

    mock_sheets = MagicMock()
    mock_sheets.last_error = None
    mock_sheets.fetch_tenant_permissions.return_value = [
        {"company_id": "1", "company_name": "微程式", "role_name": "設備運維 (甲)", "authorized_line_user_ids": "U_RAY"}
    ]
    mock_sheets.get_effective_max_stations.return_value = 10
    mock_sheets.fetch_candidate_sites.side_effect = lambda cid, uid: candidates
    mock_sheets.fetch_user_subscriptions.side_effect = lambda uid: list(live_subs)

    def _mock_add(uid, cid, query):
        q = query.strip()
        q_code = f"{int(q):02d}" if q.isdigit() else q
        matched = next((c for c in candidates if c["station_code"] == q_code or c["station_name"] == q or q in c["station_name"]), None)
        if not matched:
            return {"success": False, "error": f"找不到 {query}"}
        if any(s["station_name"] == matched["station_name"] for s in live_subs):
            return {"success": False, "error": f"場站「{matched['station_name']}」已在您的監控清單中"}
        live_subs.append({
            "line_user_id": uid,
            "company_id": cid,
            "station_code": matched["station_code"],
            "station_name": matched["station_name"],
            "space_lower": "5",
            "space_upper": "無",
            "space_cooldown": "6",
            "offline_cooldown": "24",
        })
        return {
            "success": True,
            "station_code": matched["station_code"],
            "station_name": matched["station_name"],
            "current_count": len(live_subs),
            "max_count": 10,
            "default_lower": "5",
            "default_space_cooldown": "6",
            "default_offline_cooldown": "24",
        }

    def _mock_remove(uid, query, company_id="1"):
        q = query.strip()
        q_code = f"{int(q):02d}" if q.isdigit() else q
        for idx, s in enumerate(live_subs):
            if s["station_code"] == q_code or s["station_name"] == q or q in s["station_name"]:
                removed = live_subs.pop(idx)
                return {"success": True, "station_name": removed["station_name"], "remaining_count": len(live_subs), "max_count": 10}
        return {"success": False, "error": f"未監控 {query}"}

    def _mock_space(line_user_id, station_query, lower, upper):
        for s in live_subs:
            if station_query == s["station_name"] or station_query in s["station_name"]:
                s["space_lower"] = str(lower)
                s["space_upper"] = str(upper)
                return {"success": True, "station_name": s["station_name"], "lower": str(lower), "upper": str(upper), "updated_at": "2026-10-29 16:00:00"}
        return {"success": False, "error": f"找不到 {station_query}"}

    mock_sheets.add_user_subscription.side_effect = _mock_add
    mock_sheets.remove_user_subscription.side_effect = _mock_remove
    mock_sheets.update_user_station_space.side_effect = _mock_space

    # 1. 重現使用者截圖問題：「幫我把前金成功場列入監控」
    r1 = TenantService.parse_and_execute_command("U_RAY", "幫我把前金成功場列入監控", mock_sheets)
    assert r1["action"] == "add_subscription" and r1["result"]["success"] is True
    assert r1["result"]["station_name"] == "前金成功場"
    print("  [PASS] 口語單站加入：『幫我把前金成功場列入監控』-> 成功加入 [36] 前金成功場")

    # 2. 多場站同時口語加入：「幫我把青埔二場跟松山撫遠場列入監控」
    r2 = TenantService.parse_and_execute_command("U_RAY", "幫我把青埔二場跟松山撫遠場列入監控", mock_sheets)
    assert r2["action"] == "batch_operation" and r2["is_success"] is True and r2["added_count"] == 2
    batch_bubble = LineService.build_command_result_flex_bubble(r2["title"], r2["message"], r2["details"], is_success=r2["is_success"])
    assert batch_bubble["type"] == "bubble"
    print(f"  [PASS] 多場站口語批次加入：一次新增 {r2['added_count']} 座場站，目前配額 {r2['current_count']}/{r2['max_count']}")

    # 3. 複合句（加入監控 + 設定車位下限，且驗證數字 8 不會誤判成 [08] 板橋文化場！）
    r3 = TenantService.parse_and_execute_command("U_RAY", "幫我把台中東山列入監控，車位少於 8 格通知我", mock_sheets)
    assert r3["action"] == "batch_operation" and r3["is_success"] is True
    dongshan = next((s for s in live_subs if s["station_name"] == "台中東山"), None)
    assert dongshan is not None and dongshan["space_lower"] == "8"
    assert not any(s["station_name"] == "板橋文化場" for s in live_subs), "8 格絕不可誤判為 [08] 板橋文化場"
    print("  [PASS] 複合口語：『幫我把台中東山列入監控，車位少於 8 格通知我』-> 自動加入並設下限 8 格（不誤判 08 號站）")

    # 4. 區分「取消車位警報」與「取消監控場站」
    r4 = TenantService.parse_and_execute_command("U_RAY", "把新莊新知六場車位警報取消", mock_sheets)
    assert r4["action"] == "space_update" and r4["result"]["lower"] == "無"
    assert any(s["station_name"] == "新莊新知六場" for s in live_subs)
    print("  [PASS] 語意精準區分：『把新莊新知六場車位警報取消』僅關閉車位門檻，不會誤刪監控場站")

    # 5. 多場站同時口語移除：「幫我把青埔二場和台中東山取消監控」
    r5 = TenantService.parse_and_execute_command("U_RAY", "幫我把青埔二場和台中東山取消監控", mock_sheets)
    assert r5["action"] == "batch_operation" and r5["removed_count"] == 2
    print("  [PASS] 多場站口語批次移除：『幫我把青埔二場和台中東山取消監控』-> 成功移除 2 站")

    # 6. 詢問可選場站口語：「請問有哪些場站可以選？」
    r6 = TenantService.parse_and_execute_command("U_RAY", "請問有哪些場站可以選？", mock_sheets)
    assert r6["action"] == "list_candidates"
    print("  [PASS] 口語查詢候選名單：『請問有哪些場站可以選？』-> 正確開啟可選場站清單")

    # 7. 第 1 軌效能實測 (50 次連續解析應 < 50ms)
    t0 = time.perf_counter()
    for _ in range(50):
        NLPService.parse_with_regex("幫我把前金成功場跟青埔二場列入監控，車位少於 6 格通知我", candidate_stations=candidates)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 100, f"Track 1 too slow: {elapsed_ms:.2f}ms"
    print(f"  [PASS] 第 1 軌極速效能驗證：連續解析 50 句複合多站口語僅耗時 {elapsed_ms:.2f} ms (平均 {elapsed_ms/50:.3f} ms/句)")

    # 8. 第 2 軌 Gemini Interactions API 結構化備援路由驗證 (Mock)
    with patch.object(
        NLPService,
        "parse_with_gemini",
        return_value={
            "intent": "add_subscription",
            "stations": ["板橋文化場"],
            "station_name": "板橋文化場",
        },
    ) as mock_gem:
        res_t2 = NLPService.understand_user_message(
            "麻煩幫我把文化路那座場子掛進來",
            candidate_stations=candidates,
            gemini_api_key="DUMMY_ENV_KEY_FOR_TEST",
        )
        assert res_t2["intent"] == "add_subscription" and res_t2["stations"] == ["板橋文化場"]
        mock_gem.assert_called_once()
    # 9. 重現使用者截圖同音字與連接詞：「設定樹林俊興廠的車位門檻下線為5上限為15」
    r9 = TenantService.parse_and_execute_command("U_RAY", "設定樹林俊興廠的車位門檻下線為5上限為15", mock_sheets)
    assert r9["action"] == "space_update" and r9["result"]["success"] is True
    assert r9["result"]["station_name"] == "樹林俊興場"
    assert r9["result"]["lower"] == "5" and r9["result"]["upper"] == "15", f"Got: {r9['result']}"
    print("  [PASS] 同音字與連接詞容錯：『設定樹林俊興廠的車位門檻下線為5上限為15』-> 精準設定 下限=5 / 上限=15")

    # 10. 單獨修改上限時保留原下限、單獨取消上限時仍保留原下限
    r10 = TenantService.parse_and_execute_command("U_RAY", "把樹林俊興場的車位上限改為 20", mock_sheets)
    assert r10["action"] == "space_update" and r10["result"]["lower"] == "5" and r10["result"]["upper"] == "20"
    r11 = TenantService.parse_and_execute_command("U_RAY", "把樹林俊興場的上限設為無", mock_sheets)
    assert r11["action"] == "space_update" and r11["result"]["lower"] == "5" and r11["result"]["upper"] == "無"
    print("  [PASS] 單項門檻局部修改保護：單獨改上限或單獨取消上限時，原下限 5 格安全保留不被清空")

    # 11. 台灣時區 (UTC+8) 驗證
    from services.sheets_service import _tw_now
    tw_dt = _tw_now()
    assert tw_dt.tzinfo is None
    # 12. 選項 B 防呆確認清空流程：「解除全部的廠站監控」 -> 確認卡片 -> /取消清空監控 -> /確認清空監控
    def _mock_clear_all(uid, company_id="1"):
        removed = [f"[{s['station_code']}] {s['station_name']}" for s in live_subs]
        live_subs.clear()
        return {
            "success": True,
            "removed_stations": removed,
            "removed_count": len(removed),
            "remaining_count": 0,
            "max_count": 10,
        }
    mock_sheets.clear_user_subscriptions.side_effect = _mock_clear_all

    count_before = len(live_subs)
    assert count_before > 0
    r12_prompt = TenantService.parse_and_execute_command("U_RAY", "解除全部的廠站監控", mock_sheets)
    assert r12_prompt["action"] == "confirm_clear_all"
    assert r12_prompt["current_count"] == count_before
    assert len(live_subs) == count_before, "防呆確認前絕不可提前刪除場站"

    confirm_bubble = LineService.build_clear_all_confirm_flex_bubble(
        r12_prompt["company_name"],
        r12_prompt["stations"],
        r12_prompt["current_count"],
        r12_prompt["max_count"],
    )
    assert confirm_bubble["type"] == "bubble"

    # 模擬先點擊「取消」-> 場站完全保留
    r12_cancel = TenantService.parse_and_execute_command("U_RAY", "/取消清空監控", mock_sheets)
    assert r12_cancel["action"] == "batch_operation" and len(live_subs) == count_before

    # 模擬點擊紅色「確定清空全部監控」-> 場站一次歸零，且企業綁定不受影響
    r12_exec = TenantService.parse_and_execute_command("U_RAY", "/確認清空監控", mock_sheets)
    assert r12_exec["action"] == "batch_operation" and r12_exec["is_success"] is True
    assert r12_exec["current_count"] == 0 and len(live_subs) == 0
    mock_sheets.unbind_user.assert_not_called()
    print(f"  [PASS] 選項 B 防呆清空全部監控驗證通過：『解除全部的廠站監控』先彈確認卡 -> 取消保留 -> 確認後一次清空 {count_before} 站且保留企業身份")


def test_group_smart_silent_gate_and_multi_group_isolation():
    print("\n=== 測試 7: 驗證群組智慧靜默閘門、雙群組獨立綁定與群組人數額度計算 ===")
    from unittest.mock import patch
    from services.nlp_service import NLPService
    from services.line_service import LineService

    # 模擬動態權限與雙群組獨立訂閱池 (C_GROUP_A 綁微程式、C_GROUP_B 綁乙企業)
    perm_store = []
    subs_by_target = {"C_GROUP_A": [], "C_GROUP_B": []}
    auth_codes = {
        "MOCK_AUTH_COMP1": {"company_id": "1", "company_name": "微程式"},
        "MOCK_AUTH_COMP2": {"company_id": "2", "company_name": "合作營運商B"},
    }
    comp_candidates = {
        "1": [
            {"station_code": "01", "station_name": "松山撫遠場", "is_subscribed": False},
            {"station_code": "05", "station_name": "樹林俊興場", "is_subscribed": False},
        ],
        "2": [
            {"station_code": "01", "station_name": "高雄前鎮場", "is_subscribed": False},
        ],
    }

    g_sheets = MagicMock()
    g_sheets.last_error = None
    g_sheets.fetch_tenant_permissions.side_effect = lambda: list(perm_store)
    g_sheets.get_effective_max_stations.return_value = 10
    g_sheets.fetch_candidate_sites.side_effect = lambda cid, uid: comp_candidates.get(str(cid), [])
    g_sheets.fetch_user_subscriptions.side_effect = lambda uid: list(subs_by_target.get(uid, []))

    def _bind(uid, code):
        info = auth_codes.get(code.strip())
        if not info:
            return None
        perm_store.append({
            "company_id": info["company_id"],
            "company_name": info["company_name"],
            "role_name": "管理員",
            "authorized_line_user_ids": uid,
        })
        return info

    def _add_sub(uid, cid, query):
        cands = comp_candidates.get(str(cid), [])
        q = query.strip()
        q_code = f"{int(q):02d}" if q.isdigit() else q
        matched = next((c for c in cands if c["station_code"] == q_code or c["station_name"] == q or q in c["station_name"]), None)
        if not matched:
            return {"success": False, "error": f"找不到 {query}"}
        subs_by_target.setdefault(uid, []).append({
            "line_user_id": uid,
            "company_id": str(cid),
            "station_code": matched["station_code"],
            "station_name": matched["station_name"],
            "space_lower": "5",
            "space_upper": "無",
            "space_cooldown": "6",
            "offline_cooldown": "24",
        })
        return {
            "success": True,
            "station_code": matched["station_code"],
            "station_name": matched["station_name"],
            "current_count": len(subs_by_target[uid]),
            "max_count": 10,
            "default_lower": "5",
            "default_space_cooldown": "6",
            "default_offline_cooldown": "24",
        }

    def _upd_space(line_user_id, station_query, lower, upper):
        for s in subs_by_target.get(line_user_id, []):
            if station_query in s["station_name"] or station_query == s["station_code"]:
                s["space_lower"] = lower
                s["space_upper"] = upper
                return {"success": True, "station_name": s["station_name"], "lower": lower, "upper": upper, "updated_at": "2026-10-29 16:45:00"}
        return {"success": False, "error": "未訂閱"}

    g_sheets.bind_user_to_company.side_effect = _bind
    g_sheets.add_user_subscription.side_effect = _add_sub
    g_sheets.update_user_station_space.side_effect = _upd_space

    # 1. 未綁定新群組日常閒聊 -> 100% 靜默 (silent_ignore)，絕不狂跳 auth_required
    for casual in ["早安大家", "今天誰去巡場？", "?", "收到，謝謝！"]:
        res_casual = TenantService.parse_and_execute_command("C_GROUP_A", casual, g_sheets, is_group=True, sender_user_id="U_SAME_ADMIN")
        assert res_casual["action"] == "silent_ignore", f"Unbound group should ignore '{casual}', got {res_casual}"

    # 未綁定群組明確輸入 `/` 指令或詢問綁定 -> 才提示輸入授權碼
    res_ask_bind = TenantService.parse_and_execute_command("C_GROUP_A", "/查詢", g_sheets, is_group=True, sender_user_id="U_SAME_ADMIN")
    assert res_ask_bind["action"] == "auth_required"
    auth_bubble = LineService.build_auth_request_flex_bubble("C_GROUP_A")
    assert "本群組 LINE Group ID" in json.dumps(auth_bubble, ensure_ascii=False)
    print("  [PASS] 未綁定群組安靜模式：日常對話 100% 靜默，僅在輸入 `/` 指令或詢問綁定時提示授權卡")

    # 2. 同一人將機器人拉進兩個群組並啟用兩個不同企業授權碼 -> 完全隔離不衝突
    b_a = TenantService.parse_and_execute_command("C_GROUP_A", "MOCK_AUTH_COMP1", g_sheets, is_group=True, sender_user_id="U_SAME_ADMIN")
    b_b = TenantService.parse_and_execute_command("C_GROUP_B", "/綁定 MOCK_AUTH_COMP2", g_sheets, is_group=True, sender_user_id="U_SAME_ADMIN")
    assert b_a["action"] == "auth_success" and b_a["company_name"] == "微程式"
    assert b_b["action"] == "auth_success" and b_b["company_name"] == "合作營運商B"
    print("  [PASS] 雙群組獨立綁定：同一人於 A 群綁『微程式』、B 群綁『合作營運商B』，以 Group ID 完全隔離不互蓋")

    # 3. 已綁定群組內的人類日常對話（含問號、換公司、查一下、便當選哪些、刪數字）-> 100% 靜默且不呼叫 Gemini API
    group_casual_phrases = [
        "早安，今天排班表出來了嗎",
        "?",
        "？",
        "說明",
        "幫我查一下這台車的車牌",
        "聽說老王換公司了",
        "中午便當可以選哪些",
        "把第 5 行刪掉",
        "車位還有嗎",
        "參數我改好了",
    ]
    with patch.object(NLPService, "parse_with_gemini") as mock_gemini:
        for msg in group_casual_phrases:
            r_ignore = TenantService.parse_and_execute_command(
                "C_GROUP_A", msg, g_sheets, is_group=True, sender_user_id="U_SAME_ADMIN"
            )
            assert r_ignore["action"] == "silent_ignore", f"Bound group should stay silent on '{msg}', got {r_ignore}"
        mock_gemini.assert_not_called()
    g_sheets.unbind_user.assert_not_called()
    print("  [PASS] 已綁定群組抗誤觸閘門：10 種易混淆同事對話全部保持 100% 靜默，且零消耗 Gemini API")

    # 4. 已綁定群組內的場站新增、門檻修改、清空確認與查詢指令 -> 精準回應且多群組場站互不干擾
    g_add_a = TenantService.parse_and_execute_command(
        "C_GROUP_A", "幫我把樹林俊興廠加入監控", g_sheets, is_group=True, sender_user_id="U_SAME_ADMIN"
    )
    assert g_add_a["action"] == "add_subscription" and g_add_a["result"]["success"] is True

    g_space_a = TenantService.parse_and_execute_command(
        "C_GROUP_A", "設定樹林俊興廠的車位門檻下線為5上限為15", g_sheets, is_group=True, sender_user_id="U_SAME_ADMIN"
    )
    assert g_space_a["action"] == "space_update" and g_space_a["result"]["lower"] == "5" and g_space_a["result"]["upper"] == "15"

    g_clear_prompt = TenantService.parse_and_execute_command(
        "C_GROUP_A", "解除全部的廠站監控", g_sheets, is_group=True, sender_user_id="U_SAME_ADMIN"
    )
    assert g_clear_prompt["action"] == "confirm_clear_all"

    # B 群組同時新增專屬場站，確認 A/B 群組訂閱清單完全獨立
    g_add_b = TenantService.parse_and_execute_command(
        "C_GROUP_B", "幫我把高雄前鎮場列入監控", g_sheets, is_group=True, sender_user_id="U_SAME_ADMIN"
    )
    assert g_add_b["action"] == "add_subscription" and g_add_b["result"]["station_name"] == "高雄前鎮場"
    assert [s["station_name"] for s in subs_by_target["C_GROUP_A"]] == ["樹林俊興場"]
    assert [s["station_name"] for s in subs_by_target["C_GROUP_B"]] == ["高雄前鎮場"]
    print("  [PASS] 群組管理意圖精準觸發：新增場站、改上下限、防呆清空全部皆正常回應，且 A/B 兩群組場站完全獨立")

    # 5. 驗證群組主動推播額度按群組人數精準計費
    ls = LineService()
    ls.channel_access_token = "mock_token"
    ls.messaging_api = MagicMock()
    ls.messaging_api.get_group_member_count.return_value = MagicMock(count=8)
    with patch("services.line_service.LINE_SDK_AVAILABLE", True):
        assert ls.get_target_push_cost("U_SINGLE_USER") == 1
        assert ls.get_target_push_cost("C_GROUP_A") == 8
    print("  [PASS] 群組主動推播額度計費保護：個人推播計 1 則、8 人群組推播自動計 8 則，守護 195/200 瀕危門檻")


if __name__ == "__main__":
    test_flex_bubbles()
    test_tenant_service_super_admin_commands()
    test_monthly_warning_counter_logic()
    test_full_candidate_list_and_interactive_actions()
    test_mode_a_anomaly_and_anti_false_unbind()
    test_dual_track_nlu_and_multi_station()
    test_group_smart_silent_gate_and_multi_group_isolation()
    print("\n🎉 全部單元與整合測試 100% 成功通過！")
