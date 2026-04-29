"""右栏：系统状态"""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from rich.console import RenderableType

from ..base import MonitorModule
from ..state import DashboardState
from ..colors import PRIMARY


class StatusModule(MonitorModule):
    name = "系统状态"

    def render(self, state: DashboardState) -> RenderableType:
        table = Table(expand=True, box=None, padding=(0, 2))
        table.add_column(style="bold white", width=8)
        table.add_column(overflow="fold")
        table.add_row("模型", state.model_name or "—")
        table.add_row("队列", f"{state.queue_backlog} 待处理")

        return Panel(
            table,
            title=f"[bold {PRIMARY}]系统状态[/]",
            border_style=PRIMARY, padding=(0, 1),
        )
