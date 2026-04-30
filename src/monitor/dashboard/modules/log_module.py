"""左栏：可滚动事件日志（↑↓ 翻页）"""

from __future__ import annotations

import re

from rich.panel import Panel
from rich.text import Text
from rich.console import Console, RenderableType

from ..base import MonitorModule
from ..state import DashboardState
from ..colors import EVENT_COLORS, EVENT_LABELS, DIM, PRIMARY, ERROR, WARNING, SUCCESS, ACCENT, ICE

console = Console()
# 启动时固定的内容行数（不含边框和脚注），确保 Panel 高度恒定防抖动
_HEIGHT = max(16, (console.height or 30) - 16)


class LogModule(MonitorModule):
    name = "事件日志"

    _HIDDEN_EVENTS = frozenset({"tool_call", "tool_result"})

    def render(self, state: DashboardState) -> RenderableType:
        entries = [e for e in state.log_entries if e.get("event") not in self._HIDDEN_EVENTS]
        n = len(entries)
        visible = _HEIGHT
        offset = state.log_scroll_offset

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

            if event == "milestone":
                text.append(f" {ts} ", style=DIM)
                text.append(summary, style=color)
            elif event == "app_log":
                text.append(f" {ts} ", style=DIM)
                # 根据日志级别着色（只匹配大写级别，避免模块名误伤如 uvicorn.error）
                if re.search(r'\b(ERROR|错误|失败|exception|traceback)\b', summary):
                    summary_color = ERROR
                elif re.search(r'\b(WARNING|警告)\b', summary):
                    summary_color = WARNING
                elif re.search(r'\b(SUCCESS|成功|完成|启动)\b', summary):
                    summary_color = SUCCESS
                elif re.search(r'\b(INFO|信息)\b', summary):
                    summary_color = PRIMARY
                elif re.search(r'\b(DEBUG|调试)\b', summary):
                    summary_color = DIM
                else:
                    summary_color = ICE
                text.append(summary, style=summary_color)
            else:
                text.append(f" {ts} ", style=DIM)
                text.append(f"{label:<12}", style=f"bold {color}")
                if summary:
                    text.append(f" │ ", style=DIM)
                    text.append(summary, style=color)
            text.append("\n")

        # 空行补齐：shown 可能少于 visible，用空行填到 _HEIGHT 行
        # 预留末尾 1 行给脚注
        pad = max(0, visible - len(shown))
        for _ in range(pad):
            text.append("\n")

        # 始终显示脚注（1 行）
        if n == 0:
            text.append("[dim]等待事件...[/]")
        elif offset > 0:
            remaining = n - end
            text.append(f"[dim]↑ {offset} 行 (↑↓ 滚动)[/]")
        elif n > visible:
            text.append(f"[dim]共 {n} 条 (↑ 键查看历史)[/]")
        else:
            text.append(f"[dim]─{''.join(['─' for _ in range(8)])}[/]")

        return Panel(
            text,
            title=f"[bold {PRIMARY}]事件日志[/]",
            border_style=PRIMARY, padding=(0, 1),
        )
