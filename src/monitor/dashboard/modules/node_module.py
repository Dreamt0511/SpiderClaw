"""右栏：节点跳转轨迹（自动滚动，历史灰色，当前绿色）"""

from __future__ import annotations

import time

from rich.panel import Panel
from rich.text import Text
from rich.console import Console, RenderableType

from ..base import MonitorModule
from ..state import DashboardState
from ..colors import PRIMARY, DIM, SUCCESS

console = Console()

# 固定内容行数（不含边框），根据终端高度动态计算
_CONTENT_ROWS = max(6, min(20, (console.height or 30) - 20))

# 需要闪光动画的节点名
_SHIMMER_NODES = {"修复Agent", "审查Agent"}
# 高亮窗口宽度（字符数）
_SHIMMER_WIDTH = 4
# 动画速度（字符/秒）— 更快更流畅
_SHIMMER_SPEED = 25
# 高亮色（绿色）
_SHIMMER_COLOR = "#00ff88"
# 未被高亮窗口覆盖的字符颜色（暗绿）
_SHIMMER_DIM = "#2a5a3a"
# 渐变过渡色（平滑边缘）
_SHIMMER_FADE = "#00cc66"


def _shimmer_text(text: str) -> Text:
    """生成从左到右循环移动高亮窗口的闪光文本（带渐变边缘）。"""
    n = len(text)
    if n == 0:
        return Text(text)

    # 当前高亮起始位置（循环）
    t = time.time()
    pos = (t * _SHIMMER_SPEED) % (n + _SHIMMER_WIDTH * 2) - _SHIMMER_WIDTH

    result = Text()
    for i, ch in enumerate(text):
        dist = abs(i - pos - _SHIMMER_WIDTH / 2)
        if dist < _SHIMMER_WIDTH / 2:
            # 核心高亮
            result.append(ch, style=f"bold {_SHIMMER_COLOR}")
        elif dist < _SHIMMER_WIDTH:
            # 渐变边缘
            result.append(ch, style=f"bold {_SHIMMER_FADE}")
        else:
            result.append(ch, style=_SHIMMER_DIM)
    return result


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
                is_active_agent = (
                    is_latest and duration is None and to_node in _SHIMMER_NODES
                )

                if is_active_agent:
                    text.append(" ● ", style=f"bold {_SHIMMER_COLOR}")
                    text.append_text(_shimmer_text(to_node))
                elif is_latest:
                    text.append(" ● ", style=f"bold {SUCCESS}")
                    text.append(to_node, style=f"bold {SUCCESS}")
                else:
                    text.append(" ● ", style=DIM)
                    text.append(to_node, style=DIM)

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
