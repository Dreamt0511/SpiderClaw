"""全局仪表盘状态单例
确保所有模块都能访问到同一个状态实例
"""
from typing import Optional
from .state import DashboardState

# 全局单例
_global_dashboard_state: Optional[DashboardState] = None


def get_global_dashboard_state() -> DashboardState:
    """获取全局仪表盘状态实例"""
    global _global_dashboard_state
    if _global_dashboard_state is None:
        _global_dashboard_state = DashboardState()
    return _global_dashboard_state


def set_global_dashboard_state(state: DashboardState) -> None:
    """设置全局仪表盘状态实例（仪表盘启动时调用）"""
    global _global_dashboard_state
    _global_dashboard_state = state
