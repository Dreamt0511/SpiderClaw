"""Dashboard 主类 — 纯事件驱动 + 节流刷新（手动 Alt Screen，绕过 Rich Live）"""

from __future__ import annotations

import sys
import time
import threading
from pathlib import Path
from typing import List, Optional

from rich.layout import Layout
from rich.console import Console, RenderableType

from .base import MonitorModule
from .state import DashboardState
from .reader import AuditReader
from .colors import PRIMARY
from src.config.settings import get_settings


console = Console(force_terminal=True, color_system="auto")
# 独立 console 用于 capture 渲染，不经过 Live 的 render hooks
_render_console = Console(force_terminal=True, color_system="auto")
BANNER_HEIGHT = 18  # 根据 make_banner() 实际行数调整
THROTTLE = 0.033     # 动画时最小刷新间隔（秒）：33ms ≈ 30FPS
THROTTLE_IDLE = 0.05 # 无动画时最小刷新间隔（秒）：50ms = 20FPS，节省 CPU
HEARTBEAT_ANIM = 0.033  # 有动画时的心跳间隔（秒）：保持 30FPS
HEARTBEAT_IDLE = 1.0    # 无事件无动画时的心跳间隔（秒）

# 右侧模块固定 Panel 高度（内容行 + 2 边框），用于 Layout minimum_size 防溢出
_TOOL_HEIGHT = 15       # tool_module: 13 行内容 + 2 边框
_NODE_HEIGHT = 18       # node_module: 16 行内容 + 2 边框
_STATS_HEIGHT = 9       # stats_module: 7 行内容 + 2 边框
_STATUS_HEIGHT = 5      # status_module: 3 行内容 + 2 边框
_RIGHT_TOP_MIN = max(_TOOL_HEIGHT, _NODE_HEIGHT)        # 18 行
_RIGHT_BTM_MIN = max(_STATS_HEIGHT, _STATUS_HEIGHT, 4)  # 9 行

if sys.platform == "win32":
    import msvcrt
else:
    msvcrt = None


class Dashboard:
    """双栏监控面板 — 左栏日志 + 右栏多模块，事件驱动刷新。"""

    def __init__(self, log_path: str | Path, banner: Optional[RenderableType] = None):
        self.log_path = Path(log_path)
        # 优先使用全局状态实例，确保所有模块共享同一个状态
        from .global_state import get_global_dashboard_state, set_global_dashboard_state
        self.state = get_global_dashboard_state()
        # 从配置读取模型名，启动即显示
        try:
            settings = get_settings()
            model_name = settings.openai.model_name
            if model_name:
                self.state.model_name = model_name
        except Exception:
            pass
        self.reader = AuditReader(self.log_path, self.state)
        self._modules: List[MonitorModule] = []
        self._running = False
        self._banner = banner
        # 设置为全局状态
        set_global_dashboard_state(self.state)

    def register(self, module: MonitorModule) -> None:
        self._modules.append(module)

    def _start_keyboard_listener(self):
        """后台线程读取键盘方向键，控制日志滚动。"""
        if msvcrt is None:
            return  # 非 Windows 暂不支持

        def _listen():
            while self._running:
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key == b'\xe0':  # 方向键前缀
                        arrow = msvcrt.getch()
                        if arrow == b'H':  # ↑
                            self.state.scroll_log(1)
                        elif arrow == b'P':  # ↓
                            self.state.scroll_log(-1)
                time.sleep(0.05)

        t = threading.Thread(target=_listen, daemon=True)
        t.start()

    def run(self):
        # Windows 控制台默认 GBK 编码，无法渲染 Unicode box-drawing 字符
        if sys.platform == "win32":
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")

        self.reader.start()
        self._running = True
        self._start_keyboard_listener()

        # 外层：banner 固定高度 + 面板填充剩余空间
        root = Layout()
        root.split_column(
            Layout(name="header", size=BANNER_HEIGHT),
            Layout(name="body", ratio=1),
        )
        if self._banner:
            root["header"].update(self._banner)

        # 内层：body 左右分栏
        body = Layout()
        body.split_row(
            Layout(name="left", ratio=3),
            Layout(name="right", ratio=2),
        )
        body["right"].split_column(
            Layout(name="right_top", ratio=2, minimum_size=_RIGHT_TOP_MIN),
            Layout(name="right_bottom", ratio=1, minimum_size=_RIGHT_BTM_MIN),
        )
        body["right_top"].split_row(
            Layout(name="tool_module", ratio=1),
            Layout(name="node_module", ratio=1),
        )
        body["right_bottom"].split_row(
            Layout(name="stats_module", ratio=1),
            Layout(name="status_module", ratio=1),
        )
        root["body"].update(body)

        module_map = {mod.name: mod for mod in self._modules}

        # 直接进入 Alt Screen，完全绕过 rich.live.Live
        # Live 在 Windows Git Bash 下因 legacy_windows 检测无法正常使用 Alt Screen，
        # 回退到 position_cursor() 模式导致每帧闪烁。此处手动输出 ANSI 序列，
        # 配合 capture 渲染彻底避开 Live 的 render hooks。
        console.file.write('\x1b[?25l\x1b[?1049h')
        console.file.flush()

        def _render():
            """渲染当前布局到字符串（不含任何定位/清屏命令）。"""
            _render_all(body, module_map, self.state)
            with _render_console.capture() as capture:
                _render_console.print(root, end='')
            return capture.get()

        try:
            # 首帧
            frame = _render()
            sys.stdout.write('\x1b[H' + frame)
            sys.stdout.flush()
            # 清除启动过程中 reader 线程可能已累积的事件信号
            self.state.clear_refresh()
            last_refresh = time.monotonic()

            while self._running:
                # 动画感知：有闪光动画时用高刷新率，否则省 CPU
                anim = self.state.has_active_animation()
                throttle = THROTTLE if anim else THROTTLE_IDLE
                heartbeat = HEARTBEAT_ANIM if anim else HEARTBEAT_IDLE

                elapsed = time.monotonic() - last_refresh
                wait = max(0.001, min(throttle - elapsed, heartbeat)) if elapsed < throttle else heartbeat

                got = self.state.wait_refresh(timeout=wait)
                if got:
                    self.state.clear_refresh()

                now = time.monotonic()
                elapsed = now - last_refresh

                should_render = (got and elapsed >= throttle) or elapsed >= heartbeat
                if should_render:
                    last_refresh = now
                    frame = _render()
                    sys.stdout.write('\x1b[H' + frame)
                    sys.stdout.flush()

        except KeyboardInterrupt:
            self._running = False
            self.reader.stop()
        finally:
            console.file.write('\x1b[?25h\x1b[?1049l')
            console.file.flush()


def _render_all(layout: Layout, module_map: dict, state: DashboardState):
    """快照当前状态并更新所有模块。"""
    snap = state.snapshot()

    if "事件日志" in module_map:
        layout["left"].update(module_map["事件日志"].render(snap))
    if "节点轨迹" in module_map:
        layout["node_module"].update(module_map["节点轨迹"].render(snap))
    if "工具调用" in module_map:
        layout["tool_module"].update(module_map["工具调用"].render(snap))
    if "运行统计" in module_map:
        layout["stats_module"].update(module_map["运行统计"].render(snap))
    if "系统状态" in module_map:
        layout["status_module"].update(module_map["系统状态"].render(snap))
