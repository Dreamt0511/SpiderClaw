"""线程安全的共享状态"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DashboardState:
    """所有监控模块的数据来源 — reader 线程写入，render 线程读取。"""

    # ── 环形缓冲区 ──
    log_entries:  deque[dict] = field(default_factory=lambda: deque(maxlen=500))
    tool_calls:   deque[dict] = field(default_factory=lambda: deque(maxlen=20))
    node_jumps:   deque[dict] = field(default_factory=lambda: deque(maxlen=30))
    errors:       deque[dict] = field(default_factory=lambda: deque(maxlen=15))
    events:       deque[dict] = field(default_factory=lambda: deque(maxlen=200))

    # ── 累计统计 ──
    total_llm_calls:  int = 0
    total_tool_calls: int = 0
    total_errors:     int = 0
    total_tokens:     int = 0
    start_time: datetime = field(default_factory=datetime.now)

    # ── 当前状态 ──
    agent_status:  str = "idle"
    current_node:  str = ""
    model_name:    str = ""
    thread_id:     str = ""
    heartbeat_ok:  bool = True
    queue_backlog: int = 0

    # ── 日志滚动 ──
    log_scroll_offset: int = 0  # 0=最底部（自动滚动），正数=向上翻了几行

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _refresh_event: threading.Event = field(default_factory=threading.Event)

    # ── 事件触发：reader 写入后通知 render ──

    def signal_refresh(self):
        """reader 线程调用：有新数据，通知 render 线程重绘。"""
        self._refresh_event.set()

    def wait_refresh(self, timeout: float = 1.0) -> bool:
        """render 线程调用：等待新数据到达。返回 True 表示有更新。"""
        return self._refresh_event.wait(timeout=timeout)

    def clear_refresh(self):
        """render 线程调用：清除信号，准备下一轮等待。"""
        self._refresh_event.clear()

    # ── 线程安全操作 ──

    def atomic(self):
        return self._lock

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k):
                    setattr(self, k, v)
        self.signal_refresh()

    def scroll_log(self, delta: int):
        """调整日志滚动偏移（+1=向上翻一行，-1=向下）。"""
        with self._lock:
            self.log_scroll_offset = max(0, self.log_scroll_offset + delta)
        self.signal_refresh()

    def snapshot(self) -> DashboardState:
        with self._lock:
            return DashboardState(
                log_entries=deque(self.log_entries, maxlen=500),
                tool_calls=deque(self.tool_calls, maxlen=20),
                node_jumps=deque(self.node_jumps, maxlen=30),
                errors=deque(self.errors, maxlen=15),
                events=deque(self.events, maxlen=200),
                total_llm_calls=self.total_llm_calls,
                total_tool_calls=self.total_tool_calls,
                total_errors=self.total_errors,
                total_tokens=self.total_tokens,
                start_time=self.start_time,
                agent_status=self.agent_status,
                current_node=self.current_node,
                model_name=self.model_name,
                thread_id=self.thread_id,
                heartbeat_ok=self.heartbeat_ok,
                queue_backlog=self.queue_backlog,
                log_scroll_offset=self.log_scroll_offset,
            )

    # ── 专用追加方法（自动触发 signal_refresh） ──

    def append_log(self, entry: dict):
        with self._lock:
            self.log_entries.append(entry)
        self.signal_refresh()

    def append_tool_call(self, entry: dict):
        with self._lock:
            self.tool_calls.append(entry)
        self.signal_refresh()

    def append_node_jump(self, entry: dict):
        with self._lock:
            self.node_jumps.append(entry)
        self.signal_refresh()

    def append_error(self, entry: dict):
        with self._lock:
            self.errors.append(entry)
        self.signal_refresh()

    def append_event(self, entry: dict):
        with self._lock:
            self.events.append(entry)
        self.signal_refresh()
