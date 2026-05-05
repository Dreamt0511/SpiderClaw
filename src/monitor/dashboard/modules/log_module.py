"""左栏：可滚动事件日志（↑↓ 翻页）"""

from __future__ import annotations

import re

from rich.panel import Panel
from rich.text import Text
from rich.console import Console, RenderableType, Group

from ..base import MonitorModule
from ..state import DashboardState
from ..colors import EVENT_COLORS, EVENT_LABELS, DIM, PRIMARY, ERROR, WARNING, SUCCESS, ACCENT, ICE, INFO

# Agent 响应 Panel 占用行数（上边框 + 内容 + 下边框）
_PANEL_LINES = 3
_AGENT_NAME_RE = re.compile(r'^(修复Agent|审查Agent|测试Agent|主Agent)\s')

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

        renderables: list[RenderableType] = []
        used_lines = 0

        for entry in shown:
            event = entry.get("event", "")
            ts = entry.get("ts", "")
            summary = entry.get("summary", "")

            if event == "llm_response":
                # Agent 响应：用彩色 Panel 包裹
                agent_name = self._extract_agent_name(summary)
                color = self._response_color(summary)
                panel = Panel(
                    Text(f" {summary}", style=ICE),
                    title=f"[bold {color}]{agent_name} 的响应[/]",
                    border_style=color,
                    padding=(0, 1),
                )
                renderables.append(panel)
                used_lines += _PANEL_LINES
            else:
                text = self._render_entry(event, ts, summary)
                renderables.append(text)
                used_lines += 1

            if used_lines >= visible:
                break

        # 空行补齐
        pad = max(0, visible - used_lines)
        if pad > 0:
            renderables.append(Text("\n" * pad))

        # 脚注
        footer = Text()
        if n == 0:
            footer.append("等待事件...", style=DIM)
        elif offset > 0:
            remaining = n - end
            footer.append(f"↑ {offset} 行 (↑↓ 滚动)", style=DIM)
        elif n > visible:
            footer.append(f"共 {n} 条 (↑ 键查看历史)", style=DIM)
        else:
            footer.append(f"─{''.join(['─' for _ in range(8)])}", style=DIM)
        renderables.append(footer)

        return Panel(
            Group(*renderables),
            title=f"[bold {PRIMARY}]事件日志[/]",
            border_style=PRIMARY, padding=(0, 1),
        )

    @staticmethod
    def _extract_agent_name(summary: str) -> str:
        m = _AGENT_NAME_RE.match(summary)
        return m.group(1) if m else "Agent"

    @staticmethod
    def _response_color(summary: str) -> str:
        if re.search(r'\b(ERROR|错误|失败|exception|traceback)\b', summary, re.IGNORECASE):
            return ERROR
        return SUCCESS

    @staticmethod
    def _render_entry(event: str, ts: str, summary: str) -> Text:
        text = Text()
        color = EVENT_COLORS.get(event, "white")
        label = EVENT_LABELS.get(event, event)

        if event == "milestone":
            text.append(f" {ts} ", style=DIM)
            text.append(summary, style=color)
        elif event == "app_log":
            text.append(f" {ts} ", style=DIM)
            if re.search(r'\b(ERROR|错误|失败|exception|traceback)\b', summary):
                summary_color = ERROR
            elif re.search(r'\b(WARNING|警告)\b', summary):
                summary_color = WARNING
            elif re.search(r'\b(SUCCESS|成功|完成|启动)\b', summary):
                summary_color = SUCCESS
            elif re.search(r'\b(INFO|信息)\b', summary):
                summary_color = INFO
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
        return text
