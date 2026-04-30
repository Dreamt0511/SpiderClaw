"""右栏：节点跳转轨迹（自动滚动，历史灰色，当前绿色）"""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text
from rich.console import Console, RenderableType

from ..base import MonitorModule
from ..state import DashboardState
from ..colors import PRIMARY, DIM, SUCCESS

console = Console()

# 固定内容行数（不含边框），确保 Panel 高度恒定防抖动
_CONTENT_ROWS = max(4, min(8, (console.height or 30) - 28))


class NodeModule(MonitorModule):
    name = "节点轨迹"

    def render(self, state: DashboardState) -> RenderableType:
        jumps = list(state.node_jumps)
        text = Text()

        if jumps:
            n = len(jumps)
            data_rows = _CONTENT_ROWS
            start = max(0, n - data_rows)
            shown = jumps[start:]

            for i, jump in enumerate(shown):
                is_latest = (start + i) == n - 1
                to_node = jump.get("to", "")
                duration = jump.get("duration")

                if is_latest:
                    icon = " ● "
                    icon_style = f"bold {SUCCESS}"
                    name_style = f"bold {SUCCESS}"
                else:
                    icon = " ● "
                    icon_style = DIM
                    name_style = DIM

                text.append(icon, style=icon_style)
                text.append(to_node, style=name_style)
                if duration is not None:
                    if duration < 1000:
                        text.append(f" ({duration}ms)", style=DIM)
                    else:
                        text.append(f" ({duration/1000:.1f}s)", style=DIM)
                text.append("\n")

        # 空行补齐到固定行数
        filled = min(len(jumps), _CONTENT_ROWS) if jumps else 0
        for _ in range(_CONTENT_ROWS - filled):
            text.append("\n")

        return Panel(
            text,
            title=f"[bold {PRIMARY}]节点轨迹[/]",
            border_style=PRIMARY, padding=(0, 1),
        )
