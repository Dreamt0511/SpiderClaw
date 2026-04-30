#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.config.settings import get_settings
from src.agent.orchestrator import RepairOrchestrator
# 加载配置
settings = get_settings(config_path="src/config/agent-config.yaml")
print("Settings lark config:")
print(f"  base_enabled: {settings.lark.base_enabled}")
print(f"  base_token: {settings.lark.base_token}")
print(f"  repair_table_id: {settings.lark.repair_table_id}")
# 模拟webhook中的初始化
print("\nCreating RepairOrchestrator...")
try:
    orchestrator = RepairOrchestrator(
        github_token=settings.github.token,
        openai_api_key=settings.openai.api_key,
        openai_base_url=settings.openai.base_url,
        llm_model=settings.openai.model_name,
        max_retries=settings.agent.max_retries,
        max_change_lines=settings.agent.max_change_lines,
        lark_notify_enabled=settings.lark.enabled,
        lark_notify_users=settings.lark.notify_users,
        lark_base_enabled=settings.lark.base_enabled,
        lark_base_token=settings.lark.base_token,
        lark_base_repair_table_id=settings.lark.repair_table_id,
        environment=settings.environment
    )
    print("Orchestrator created successfully")

    # 检查全局客户端
    from src.notify.lark_base import _lark_base_client
    print(f"Global client after orchestrator init: {_lark_base_client is not None}")

    if _lark_base_client:
        print(f"  base_token: {_lark_base_client.base_token}")
        print(f"  repair_table_id: {_lark_base_client.repair_table_id}")
except Exception as e:
    print(f"Failed to create orchestrator: {e}")
    import traceback
    traceback.print_exc()
