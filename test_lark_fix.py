#!/usr/bin/env python3
"""测试修复后的飞书通知"""
import asyncio
from src.notify.lark_notify import send_repair_notification
from src.config.settings import get_settings

async def main():
    settings = get_settings()
    if not settings.lark.notify_users:
        print("请先配置飞书通知用户")
        return

    receive_id = settings.lark.notify_users[0]

    # 测试成功通知
    print("发送成功通知...")
    await send_repair_notification(
        repair_success=True,
        error_type="语法错误",
        source_branch="my_test_branch",
        pr_url="https://github.com/Dreamt0511/AutoFix_Test_rep/pull/28",
        fix_description="修复了3个语法错误：缺失的冒号、未闭合的括号、字符串格式化错误",
        receive_id=receive_id,
        pr_author="Dreamt0511",
        bug_count=3
    )

    # 等待1秒
    await asyncio.sleep(1)

    # 测试失败通知
    print("发送失败通知...")
    await send_repair_notification(
        repair_success=False,
        error_type="语法错误",
        source_branch="my_test_branch",
        pr_url="",
        fix_description="尝试修复语法错误时遇到问题",
        receive_id=receive_id,
        error_message="下载CI日志失败: 网络连接超时，无法获取错误信息",
        pr_author="Dreamt0511",
        bug_count=2
    )

    print("发送完成，请检查飞书是否收到完整内容")

if __name__ == "__main__":
    asyncio.run(main())
