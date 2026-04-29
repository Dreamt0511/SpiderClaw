"""右栏：统计概要"""

from __future__ import annotations

from datetime import datetime

from rich.panel import Panel
from rich.table import Table
from rich.console import RenderableType

from ..base import MonitorModule
from ..state import DashboardState
from ..colors import PRIMARY, SUCCESS, ERROR, ACCENT, WARNING, DIM, AGENT_STATUS_CN, STATUS_COLORS


class StatsModule(MonitorModule):
    name = "运行统计"

    def render(self, state: DashboardState) -> RenderableType:
        elapsed = datetime.now() - state.start_time
        hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)

        status_cn = AGENT_STATUS_CN.get(state.agent_status, state.agent_status)
        status_color = STATUS_COLORS.get(state.agent_status, DIM)

        table = Table(expand=True, box=None, padding=(0, 2))
        table.add_column(style="bold white", width=10)
        table.add_column(overflow="fold")

        table.add_row("运行时长", f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        table.add_row("LLM 调用", f"[bold {ACCENT}]{state.total_llm_calls} 次[/]")
        table.add_row("工具调用", f"[bold {WARNING}]{state.total_tool_calls} 次[/]")
        table.add_row("token消耗", f"[bold {SUCCESS}]{state.total_tokens:,}[/]")
        table.add_row("异常次数", f"[bold {ERROR}]{state.total_errors} 次[/]")
        table.add_row("当前状态", f"[bold {status_color}]{status_cn}[/]")

        return Panel(
            table,
            title=f"[bold {PRIMARY}]运行统计[/]",
            border_style=PRIMARY, padding=(0, 1),
        )
