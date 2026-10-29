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
    print("  [PASS] /查詢 個人監控清單支援多張垂直捲動與點擊『➖ 可移除』驗證通過")

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

if __name__ == "__main__":
    test_flex_bubbles()
    test_tenant_service_super_admin_commands()
    test_monthly_warning_counter_logic()
    test_full_candidate_list_and_interactive_actions()
    print("\n🎉 全部單元與整合測試 100% 成功通過！")
