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

    # 2. 測試全域參數控制台卡片
    configs = {
        "max_stations_per_user": "12",
        "maintenance_mode": "OFF",
        "monthly_push_count": "150",
        "monthly_push_warning": "195",
        "current_count_month": "2026-10"
    }
    bubble_cfg = LineService.build_system_configs_flex_bubble(configs)
    assert bubble_cfg["type"] == "bubble"
    assert "系統全域參數控制台" in bubble_cfg["header"]["contents"][0]["text"]
    assert bubble_cfg["footer"]["contents"][0]["action"]["text"] == "/靜音"
    print("  [PASS] 全域參數控制台 Flex 卡片結構驗證通過")

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
    print("  [PASS] 超級管理者修改參數 (/參數 最大場站數 15) 成功寫入")

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
    print("\n🎉 全部單元與整合測試 100% 成功通過！")
