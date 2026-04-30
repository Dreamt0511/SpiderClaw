#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.config.settings import get_settings
# 加载配置
settings = get_settings(config_path="src/config/agent-config.yaml")
print("agent.enabled:", settings.agent.enabled)
print("lark.base_enabled:", settings.lark.base_enabled)
print("lark.base_token:", settings.lark.base_token)
print("lark.repair_table_id:", settings.lark.repair_table_id)
# 检查初始化条件
if settings.lark.base_enabled and settings.lark.base_token:
    print("\nInitialization conditions met")
    from src.notify.lark_base import init_lark_base
    try:
        init_lark_base(
            base_token=settings.lark.base_token,
            repair_table_id=settings.lark.repair_table_id,
            as_bot=True
        )
        print("Init success")

        # 检查全局变量
        from src.notify.lark_base import _lark_base_client
        print("Global client exists:", _lark_base_client is not None)

    except Exception as e:
        print("Init failed:", str(e))
        import traceback
        traceback.print_exc()
else:
    print("\nInitialization conditions NOT met")
    if not settings.lark.base_enabled:
        print("  - base_enabled is false")
    if not settings.lark.base_token:
        print("  - base_token is empty")
