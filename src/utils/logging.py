"""结构化日志系统"""
import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
import structlog
from structlog.types import Processor
import json


def setup_logging(
    log_level: str = "INFO",
    log_dir: str = "logs",
    service_name: str = "spiderclaw",
    json_format: bool = True,
    retention_days: int = 30
) -> None:
    """
    配置结构化日志系统

    Args:
        log_level: 日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）
        log_dir: 日志文件存储目录
        service_name: 服务名称，用于日志文件名
        json_format: 是否使用JSON格式输出
        retention_days: 日志保留天数
    """
    # 创建日志目录
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    # 配置基础日志级别
    level = getattr(logging, log_level.upper())

    # 修复Windows控制台编码问题
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    # 清除已有 handlers，防止重复
    root_logger = logging.getLogger()
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)
        h.close()

    root_logger.setLevel(level)

    # 公共处理器（不含渲染器 — 渲染交给 ProcessorFormatter）
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # 添加服务名称
    def add_service_name(logger, method_name, event_dict):
        event_dict["service"] = service_name
        return event_dict

    processors.insert(1, add_service_name)

    # 桥接处理器：将 event_dict 转发到标准 logging handler 的 Formatter
    processors.append(structlog.stdlib.ProcessorFormatter.wrap_for_formatter)

    # 配置structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 创建渲染器：控制台和文件使用不同格式
    console_renderer: Processor = (
        structlog.processors.JSONRenderer(
            serializer=lambda obj, **kwargs: json.dumps(obj, ensure_ascii=False, **kwargs)
        )
        if json_format
        else structlog.dev.ConsoleRenderer()
    )
    file_renderer: Processor = structlog.processors.JSONRenderer(
        serializer=lambda obj, **kwargs: json.dumps(obj, ensure_ascii=False, **kwargs)
    )

    # 控制台 handler（格式由 json_format 控制）
    console_formatter = structlog.stdlib.ProcessorFormatter(processor=console_renderer)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # 文件 handler（始终 JSON 格式，不含 ANSI 转义码）
    file_formatter = structlog.stdlib.ProcessorFormatter(processor=file_renderer)
    log_file = Path(log_dir) / f"{service_name}.log"
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=retention_days,
        encoding="utf-8"
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # 兜底文件 handler：直接写入纯文本格式，防止 structlog 桥接失败时日志丢失
    plain_log_file = Path(log_dir) / f"{service_name}_plain.log"
    plain_handler = logging.FileHandler(plain_log_file, encoding="utf-8", mode="a")
    plain_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    plain_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(plain_handler)

    # 降低第三方库的日志级别
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("fastapi").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.BoundLogger:
    """
    获取结构化日志记录器

    Args:
        name: 日志记录器名称

    Returns:
        structlog.BoundLogger: 日志记录器实例
    """
    return structlog.get_logger(name)


def bind_context(**kwargs) -> None:
    """
    绑定上下文变量到所有日志

    Args:
        **kwargs: 要绑定的上下文变量
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def unbind_context(*keys) -> None:
    """
    解绑上下文变量

    Args:
        *keys: 要解绑的变量名
    """
    structlog.contextvars.unbind_contextvars(*keys)
