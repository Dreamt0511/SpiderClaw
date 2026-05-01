"""右栏：工具调用状态（自动滚动，已完成灰色，执行中绿色）"""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from rich.console import Console, RenderableType

from ..base import MonitorModule
from ..state import DashboardState
from ..colors import PRIMARY, DIM, SUCCESS, ERROR, WARNING

console = Console()

# 固定内容行数（不含边框），根据终端高度动态计算
_CONTENT_ROWS = max(6, min(20, (console.height or 30) - 20))


class ToolModule(MonitorModule):
    name = "工具调用"

    def render(self, state: DashboardState) -> RenderableType:
        calls = list(state.tool_calls)
        data_rows = _CONTENT_ROWS - 1  # 减去表头行

        table = Table(box=None, padding=(0, 1), expand=True)
        table.add_column("工具", overflow="fold")
        table.add_column("状态", width=8)

        # 取最新 data_rows 条
        if calls:
            end = len(calls)
            start = max(0, end - data_rows)
            shown = calls[start:end]
            for tc in reversed(shown):
                tool = tc.get("tool", "?")
                status = tc.get("status", "")
                if status == "success":
                    status_str = "✅ 成功"
                    tool_color = DIM
                elif status == "failed":
                    status_str = "❌ 失败"
                    tool_color = DIM
                else:
                    status_str = f"[bold {SUCCESS}]⏳ 执行中[/]"
                    tool_color = SUCCESS
                table.add_row(f"[{tool_color}]{tool}[/]", status_str)

        # 空行补齐到固定行数
        filled = min(len(calls), data_rows) if calls else 0
        for _ in range(data_rows - filled):
            table.add_row("", "")

        return Panel(
            table,
            title=f"[bold {PRIMARY}]工具调用[/]",
            border_style=PRIMARY, padding=(0, 1),
        )
