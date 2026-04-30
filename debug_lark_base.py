#!/usr/bin/env python3
"""调试飞书多维表格初始化问题"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.config.settings import get_settings
from src.notify.lark_base import init_lark_base, report_repair_record
import asyncio
def main():
    # 加载配置
    print("🔍 加载配置文件...")
    settings = get_settings(config_path="src/config/agent-config.yaml")

    print(f"\n📋 配置检查结果:")
    print(f"  agent.enabled: {settings.agent.enabled}")
    print(f"  lark.base_enabled: {settings.lark.base_enabled}")
    print(f"  lark.base_token: '{settings.lark.base_token}' (长度: {len(settings.lark.base_token)})")
    print(f"  lark.repair_table_id: '{settings.lark.repair_table_id}'")

    if not settings.lark.base_enabled:
        print("\n❌ 错误: lark.base_enabled 为 false，不会初始化多维表格客户端")
        return

    if not settings.lark.base_token:
        print("\n❌ 错误: lark.base_token 为空，不会初始化多维表格客户端")
        return

    print("\n🚀 开始初始化多维表格客户端...")
    try:
        init_lark_base(
            base_token=settings.lark.base_token,
            repair_table_id=settings.lark.repair_table_id,
            as_bot=True
        )
        print("✅ 客户端初始化成功！")

        # 测试上报
        print("\n📝 测试上报功能...")
        async def test_report():
            result = await report_repair_record(
                error_type="DebugTest",
                repo_name="test/repo",
                branch_name="test-branch",
                pr_author="debug-user",
                original_pr_url="https://github.com/test/repo/pull/1",
                fix_pr_url="https://github.com/test/repo/pull/2",
                repair_success=True,
                fix_description="调试测试：初始化验证",
                error_message="这是一条测试错误信息",
                file_count=1,
                change_lines=5,
                repair_duration=2.5,
                retry_count=0,
                token_usage=100,
                environment="测试"
            )
            return result

        result = asyncio.run(test_report())
        if result:
            print("✅ 上报测试成功！")
        else:
            print("❌ 上报测试失败")

    except Exception as e:
        print(f"\n❌ 初始化失败，错误信息: {str(e)}")
        import traceback
        traceback.print_exc()
if __name__ == "__main__":
    main()