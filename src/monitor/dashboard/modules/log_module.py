"""左栏：可滚动事件日志（↑↓ 翻页）"""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text
from rich.console import Console, RenderableType

from ..base import MonitorModule
from ..state import DashboardState
from ..colors import EVENT_COLORS, EVENT_LABELS, DIM, PRIMARY, ERROR, WARNING, SUCCESS, ACCENT

console = Console()


class LogModule(MonitorModule):
    name = "事件日志"

    _HIDDEN_EVENTS = frozenset({"tool_call", "tool_result"})
    _HEIGHT = max(16, (console.height or 30) - 16)  # 启动时固定，防抖动

    def render(self, state: DashboardState) -> RenderableType:
        # 过滤掉工具调用事件（右侧工具面板已展示，左侧不重复）
        entries = [e for e in state.log_entries if e.get("event") not in self._HIDDEN_EVENTS]
        if not entries:
            return Panel(
                Text("等待事件...", style=DIM),
                title=f"[bold {PRIMARY}]事件日志[/]",
                border_style=PRIMARY, padding=(0, 1),
            )

        visible = self._HEIGHT

        offset = state.log_scroll_offset
        n = len(entries)

        # 根据 offset 计算可见窗口
        end = n - offset
        if end <= 0:
            end = min(visible, n)
        start = max(0, end - visible)
        shown = entries[start:end]

        text = Text()
        for entry in shown:
            event = entry.get("event", "")
            ts = entry.get("ts", "")
            summary = entry.get("summary", "")
            color = EVENT_COLORS.get(event, "white")
            label = EVENT_LABELS.get(event, event)

            if event in ("app_log", "milestone"):
                # 普通日志 / 里程碑：不加 label 前缀，summary 本身可读
                text.append(f" {ts} ", style=DIM)
                summary_color = color if event == "milestone" else "white"
                text.append(summary, style=summary_color)
            else:
                text.append(f" {ts} ", style=DIM)
                text.append(f"{label:<12}", style=f"bold {color}")
                if summary:
                    text.append(f" │ ", style=DIM)
                    text.append(summary, style="white")
            text.append("\n")

        # 底部提示
        if offset > 0:
            remaining = n - end
            text.append(f"\n[dim]↑ {offset} 行 (↑↓ 滚动)[/]")
        elif n > visible:
            text.append(f"\n[dim]共 {n} 条 (↑ 键查看历史)[/]")

        return Panel(
            text,
            title=f"[bold {PRIMARY}]事件日志[/]",
            border_style=PRIMARY, padding=(0, 1),
        )
