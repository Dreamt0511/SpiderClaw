"""右栏：工具调用状态（自动滚动，已完成灰色，执行中绿色）"""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from rich.console import Console, RenderableType

from ..base import MonitorModule
from ..state import DashboardState
from ..colors import PRIMARY, DIM, SUCCESS, ERROR, WARNING

console = Console()


class ToolModule(MonitorModule):
    name = "工具调用"

    def render(self, state: DashboardState) -> RenderableType:
        calls = list(state.tool_calls)
        if not calls:
            return Panel(
                Table.grid(padding=(0, 1)),
                title=f"[bold {PRIMARY}]工具调用[/]",
                border_style=PRIMARY, padding=(0, 1),
            )

        # 根据终端高度估算可见行数
        height = console.height or 30
        visible = max(3, height - 28)

        # 取尾部 visible 条（自动滚动到最新）
        offset = 0
        n = len(calls)
        end = n - offset
        start = max(0, end - visible)
        shown = calls[start:end]

        table = Table(box=None, padding=(0, 1), expand=True)
        table.add_column("工具", overflow="fold")
        table.add_column("状态", width=8)

        for tc in reversed(shown):
            tool = tc.get("tool", "?")
            status = tc.get("status", "")
            is_pending = status not in ("success", "failed")

            if status == "success":
                status_str = f"✅ 成功"
                tool_color = DIM
            elif status == "failed":
                status_str = f"❌ 失败"
                tool_color = DIM
            else:
                status_str = f"[bold {SUCCESS}]⏳ 执行中[/]"
                tool_color = SUCCESS

            table.add_row(f"[{tool_color}]{tool}[/]", status_str)

        return Panel(
            table,
            title=f"[bold {PRIMARY}]工具调用[/]",
            border_style=PRIMARY, padding=(0, 1),
        )
