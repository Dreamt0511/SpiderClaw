"""Dashboard 主类 — 事件驱动刷新，带节流"""

from __future__ import annotations

import sys
import time
import threading
from pathlib import Path
from typing import List, Optional

from rich.layout import Layout
from rich.live import Live
from rich.console import Console, RenderableType

from .base import MonitorModule
from .state import DashboardState
from .reader import AuditReader
from .colors import PRIMARY
from src.config.settings import get_settings

console = Console(force_terminal=True, color_system="auto")
BANNER_HEIGHT = 18  # 根据 make_banner() 实际行数调整
MIN_REFRESH_INTERVAL = 0.25  # 最小刷新间隔 250ms，防抖动

if sys.platform == "win32":
    import msvcrt
else:
    msvcrt = None


class Dashboard:
    """双栏监控面板 — 左栏日志 + 右栏多模块，事件驱动刷新。"""

    def __init__(self, log_path: str | Path, banner: Optional[RenderableType] = None):
        self.log_path = Path(log_path)
        self.state = DashboardState()
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
            Layout(name="right_top", ratio=2),
            Layout(name="right_bottom", ratio=1),
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

        try:
            with Live(root, refresh_per_second=2, screen=True) as live:
                _render_all(body, module_map, self.state)
                last_refresh = time.monotonic()

                while self._running:
                    got = self.state.wait_refresh(timeout=1.0)
                    if got:
                        self.state.clear_refresh()

                    now = time.monotonic()
                    if got and (now - last_refresh >= MIN_REFRESH_INTERVAL):
                        last_refresh = now
                        _render_all(body, module_map, self.state)
                        live.refresh()

        except KeyboardInterrupt:
            self._running = False
            self.reader.stop()


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
