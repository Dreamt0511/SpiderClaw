"""后台线程 tail 审计日志 + 应用日志，更新 DashboardState"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from .state import DashboardState
from .colors import NODE_ALIAS

_LOG_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})[,\.\d]*\s*(.*)")


class AuditReader:
    """后台 daemon 线程轮询 audit.jsonl 和 spiderclaw.log。"""

    def __init__(
        self,
        log_path: Path,
        state: DashboardState,
        poll_interval: float = 0.1,
        app_log_path: Path | None = None,
    ):
        self.log_path = log_path
        self.state = state
        self.poll_interval = poll_interval
        self._running = False
        self._threads: list[threading.Thread] = []
        self._node_enter_time: float | None = None
        self._current_node_entry: dict | None = None
        self._app_log_path = app_log_path or Path("src/logs/spiderclaw.log")

    def start(self):
        self._running = True
        t1 = threading.Thread(target=self._tail_audit, daemon=True)
        t2 = threading.Thread(target=self._tail_app_log, daemon=True)
        t1.start()
        t2.start()
        self._threads = [t1, t2]

    def stop(self):
        self._running = False

    # ── audit.jsonl 审计事件 ──

    def _tail_audit(self):
        while self._running and not self.log_path.exists():
            time.sleep(0.5)
        if not self._running:
            return

        with open(self.log_path, "r", encoding="utf-8") as f:
            f.seek(0, 2)
            while self._running:
                line = f.readline()
                if not line:
                    time.sleep(self.poll_interval)
                    continue
                try:
                    self._parse_audit_line(line.strip())
                except Exception:
                    continue  # 防止单行解析异常杀死线程

    def _parse_audit_line(self, line: str):
        data = json.loads(line)
        event = data.get("event", "")
        ts_raw = data.get("ts", "")

        ts = ts_raw
        try:
            if ts_raw.endswith("Z"):
                ts_raw = ts_raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(ts_raw)
            ts = dt.astimezone().strftime("%H:%M:%S")
        except Exception:
            pass

        # 通用日志条目
        entry = {"ts": ts, "event": event, "summary": ""}
        self.state.append_log(entry)
        self.state.append_event(entry)

        if event == "node_enter":
            node = data.get("node", "")
            now = time.time()
            self._node_enter_time = now

            friendly = NODE_ALIAS.get(node, node)
            node_entry = {"ts": ts, "to": friendly, "duration": None}
            self.state.append_node_jump(node_entry)
            self._current_node_entry = node_entry
            self.state.update(current_node=friendly, agent_status="thinking")
            entry["summary"] = f"进入节点: {friendly}"

        elif event == "node_exit":
            node = data.get("node", "")
            friendly = NODE_ALIAS.get(node, node)
            # 在离开节点时记录该节点的实际运行时长
            if self._current_node_entry is not None and self._node_enter_time is not None:
                elapsed = time.time() - self._node_enter_time
                self._current_node_entry["duration"] = round(elapsed * 1000)
                self._current_node_entry = None
            entry["summary"] = f"离开节点: {friendly}"

        elif event == "tool_call":
            self.state.update(agent_status="calling_tool")
            with self.state.atomic():
                self.state.total_tool_calls += 1
            tool_name = data.get("tool", "unknown")
            args = data.get("args", {})
            args_str = json.dumps(args, ensure_ascii=False)[:300]
            tc_entry = {
                "ts": ts,
                "tool": tool_name,
                "args": args,
                "status": "pending",
            }
            self.state.append_tool_call(tc_entry)
            entry["summary"] = f"{tool_name}({args_str})"

        elif event == "tool_result":
            self.state.update(agent_status="thinking")
            tool_name = data.get("tool", "unknown")
            result_summary = data.get("result_summary", "") or ""
            is_error = data.get("is_error", False)
            status = "failed" if is_error else "success"
            with self.state.atomic():
                for tc in reversed(self.state.tool_calls):
                    if tc["tool"] == tool_name and tc["status"] == "pending":
                        tc["status"] = status
                        break
                # 也更新 log_entries 中对应的条目
                for le in reversed(self.state.log_entries):
                    if le["event"] == "tool_call" and tool_name in le.get("summary", ""):
                        le["summary"] = result_summary[:500]
                        break
            entry["summary"] = result_summary[:500]

        elif event == "llm_call":
            with self.state.atomic():
                self.state.total_llm_calls += 1
            mn = data.get("model_name")
            if mn:
                self.state.update(model_name=mn)
            entry["summary"] = (data.get("summary", "") or "")[:500]

        elif event == "llm_response":
            tokens = data.get("token_count", 0)
            if tokens:
                with self.state.atomic():
                    self.state.total_tokens += tokens
            entry["summary"] = (data.get("summary", "") or "")[:500]

        elif event == "error":
            with self.state.atomic():
                self.state.total_errors += 1
            self.state.update(agent_status="error")
            self.state.append_error({
                "ts": ts,
                "detail": data.get("message", data.get("error", ""))[:500],
            })
            entry["summary"] = data.get("message", "发生错误")

        elif event == "system_action":
            entry["summary"] = data.get("action", "")
            mn = data.get("model_name")
            if mn:
                self.state.update(model_name=mn)

        elif event == "milestone":
            node = data.get("node", "")
            friendly = NODE_ALIAS.get(node, node)
            self.state.append_node_jump({
                "ts": ts,
                "to": friendly,
                "duration": None,
            })
            entry["summary"] = friendly
            # 修复流程完结时统计成功/失败次数
            if node == "repair_complete":
                if data.get("success"):
                    with self.state.atomic():
                        self.state.total_repair_success += 1
                else:
                    with self.state.atomic():
                        self.state.total_repair_failures += 1

    # ── spiderclaw.log 应用日志 ──

    def _tail_app_log(self):
        while self._running and not self._app_log_path.exists():
            time.sleep(1)
        if not self._running:
            return

        # 跳到文件末尾，只读取本次运行后的新日志（不持有文件句柄）
        try:
            with open(self._app_log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                position = f.tell()
        except OSError:
            position = 0

        # 轮询读取新行，每次读完释放文件句柄，避免阻塞日志轮转
        while self._running:
            try:
                with open(self._app_log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(position)
                    lines = f.readlines()
                    position = f.tell()
            except OSError:
                time.sleep(self.poll_interval)
                continue

            for line in lines:
                line = line.rstrip("\n\r")
                if line:
                    try:
                        self._parse_log_line(line)
                    except Exception:
                        continue
            time.sleep(self.poll_interval)

    def _parse_log_line(self, line: str):
        """解析普通日志行（非 JSON），提取时间戳和消息。"""
        m = _LOG_TIME_RE.match(line)
        if m:
            ts = m.group(1).strip()
            # 只保留 HH:MM:SS
            if len(ts) > 8:
                ts = ts[-8:]
            msg = m.group(2).strip()
        else:
            ts = datetime.now().strftime("%H:%M:%S")
            msg = line[:500]

        entry = {"ts": ts, "event": "app_log", "summary": msg[:500]}
        self.state.append_log(entry)
