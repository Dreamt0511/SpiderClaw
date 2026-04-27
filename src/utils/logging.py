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
    log_dir: str = "src/logs",
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

    # 配置基础日志
    level = getattr(logging, log_level.upper())

    # 修复Windows控制台编码问题
    import sys
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=sys.stdout
    )

    # 公共处理器
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

    # 根据格式选择渲染器
    if json_format:
        processors.append(structlog.processors.JSONRenderer(
            serializer=lambda obj, **kwargs: json.dumps(obj, ensure_ascii=False, **kwargs)
        ))
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    # 配置structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 配置日志文件处理器（按天滚动）
    log_file = Path(log_dir) / f"{service_name}.log"
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=retention_days,
        encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(file_handler)

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
