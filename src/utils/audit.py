"""结构化 JSONL 审计日志器 — 为 Dashboard 提供数据源"""

from __future__ import annotations

import json
import os
import re
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue, Empty
from typing import Any
from langchain_core.callbacks import BaseCallbackHandler


class AuditLogger:
    """线程安全、异步写入的 JSONL 审计日志器。

    所有 Agent/Orchestrator 的关键事件都通过此日志器记录，
    Dashboard 的 reader 通过 tail 此文件获取实时数据。

    使用单线程消费者 + 队列，避免每个事件都创建新线程。
    """

    _instance: AuditLogger | None = None
    _lock = threading.Lock()

    def __new__(cls, log_dir: str = "logs") -> AuditLogger:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init(log_dir)
            return cls._instance

    def _init(self, log_dir: str):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._log_path = self.log_dir / "audit.jsonl"
        self.buffer: deque[dict] = deque(maxlen=1000)

        # 队列 + 后台消费者线程
        self._queue: Queue[dict | None] = Queue(maxsize=1000)
        self._consumer = threading.Thread(target=self._consumer_loop, daemon=True)
        self._consumer.start()

    def _consumer_loop(self):
        """后台线程：从队列取记录并写入文件。收到 None 时退出。"""
        while True:
            record = self._queue.get()
            if record is None:
                break
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    f.flush()
            except Exception:
                pass  # 审计日志不能影响主流程

    def log_event(self, event: str, **kwargs: Any) -> None:
        """写入一条结构化审计事件。

        Args:
            event: 事件类型，如 "node_enter", "node_exit", "tool_call" 等
            **kwargs: 事件相关的结构化数据
        """
        now = datetime.now(timezone.utc)
        record = {
            "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "unix_ts": now.timestamp(),
            "event": event,
            **kwargs,
        }
        self.buffer.append(record)

        # 非阻塞入队，队列满时丢弃（防止阻塞主流程）
        try:
            self._queue.put_nowait(record)
        except Exception:
            pass

    def get_recent(self, n: int = 200) -> list[dict]:
        """获取最近的 N 条事件（供 dashboard 初始加载用）"""
        return list(self.buffer)[-n:]

    @property
    def log_path(self) -> Path:
        return self._log_path


# 全局单例
audit_logger = AuditLogger()


_KNOWN_ERRORS = frozenset({
    "NameError", "SyntaxError", "ModuleNotFoundError", "ImportError",
    "AttributeError", "TypeError", "ValueError", "KeyError", "IndexError",
    "FileNotFoundError", "IndentationError", "TabError", "RuntimeError",
    "OSError", "StopIteration",
})


def _extract_errors(text: str) -> list[str]:
    """从文本中提取已知的错误类型名"""
    return [e for e in _KNOWN_ERRORS if e in text]


class AuditCallbackHandler(BaseCallbackHandler):
    """LangChain 回调处理器 — 将 LLM 调用事件写入审计日志。

    拦截 ChatOpenAI 的 invoke/ainvoke 调用：
    - on_llm_start → 发射 llm_call 事件
    - on_llm_end   → 发射 llm_response 事件（提取 token 消耗 + 响应摘要）

    用法：
        ChatOpenAI(..., callbacks=[AuditCallbackHandler("修复Agent")])
    """

    def __init__(self, agent_name: str = ""):
        self.agent_name = agent_name

    def on_llm_start(self, serialized: dict, prompts: list[str], **kwargs) -> None:
        """LLM 调用开始时发射 llm_call 事件。"""
        try:
            kwargs_info = serialized.get("kwargs", {})
            model_name = kwargs_info.get("model_name") or kwargs_info.get("model", "") or ""
            prefix = f"{self.agent_name} " if self.agent_name else ""

            # 从 prompts 中提取关键上下文
            detail = ""
            for p in prompts:
                errors = _extract_errors(p)
                if errors:
                    detail = f"分析 {errors[0]}"
                    break
                # 提取文件路径提示
                files = re.findall(r'[\w/]+\.py', p)
                if files and len(files) <= 3:
                    detail = f"涉及 {', '.join(files[:2])}"

            if detail:
                summary = f"{prefix}{detail}"
            else:
                summary = f"{prefix}发送 LLM 请求"

            audit_logger.log_event("llm_call", agent=self.agent_name, model_name=model_name, summary=summary)
        except Exception:
            pass

    def on_llm_end(self, response, **kwargs) -> None:
        """LLM 调用结束时提取 token 消耗和响应摘要，发射 llm_response 事件。"""
        try:
            llm_output = getattr(response, "llm_output", None) or {}
            token_usage = llm_output.get("token_usage", {}) or {}

            total = token_usage.get("total_tokens") or 0
            model_name = llm_output.get("model_name", "")

            prefix = f"{self.agent_name} " if self.agent_name else ""

            # 提取响应摘要
            gens = getattr(response, "generations", None)
            detail = ""
            if gens and gens[0]:
                msg = getattr(gens[0][0], "message", None)
                if msg:
                    meta = getattr(msg, "response_metadata", {}) or {}
                    finish = meta.get("finish_reason", "")

                    if finish == "tool_calls" or hasattr(msg, "tool_calls") and msg.tool_calls:
                        tool_names = [tc.get("name", "?") for tc in (msg.tool_calls or [])]
                        if tool_names:
                            detail = f"调用 {', '.join(tool_names[:3])}"
                        else:
                            detail = "调用工具"
                    else:
                        content = getattr(msg, "content", "") or ""
                        if content:
                            # 取第一行或截取前 120 字
                            first_line = content.strip().split("\n")[0][:120]
                            detail = f"回复: {first_line}"
                        else:
                            detail = "无内容返回"
            if detail:
                summary = f"{prefix}{detail}"
            else:
                summary = f"{prefix}LLM 返回 ({total:,} tokens)" if total else f"{prefix}LLM 返回"

            audit_logger.log_event(
                "llm_response",
                agent=self.agent_name,
                token_count=total,
                model_name=model_name,
                summary=summary,
            )
        except Exception:
            pass

    @property
    def always_verbose(self) -> bool:
        return True
