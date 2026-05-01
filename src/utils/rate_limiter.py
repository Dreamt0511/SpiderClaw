"""滑动窗口限流器 — 基于服务名的修复频率控制"""
import time
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# 连续限流多少次后触发告警
ALERT_THRESHOLD = 10


class ServiceRateLimiter:
    """基于服务名的滑动窗口限流

    集成位置：event_consumer 层，事件进入 LangGraph 图之前。
    被限流的事件直接丢弃，不消耗图资源。
    """

    def __init__(self, max_per_minute: int = 3, max_per_hour: int = 20):
        self._max_per_minute = max_per_minute
        self._max_per_hour = max_per_hour
        self._minute_records: dict[str, list[float]] = defaultdict(list)
        self._hour_records: dict[str, list[float]] = defaultdict(list)
        self._limited_counts: dict[str, int] = defaultdict(int)

    async def check(self, service: str) -> bool:
        """检查服务是否允许修复。返回 True 表示允许。"""
        now = time.time()

        self._minute_records[service] = [
            t for t in self._minute_records[service] if now - t < 60
        ]
        self._hour_records[service] = [
            t for t in self._hour_records[service] if now - t < 3600
        ]

        if len(self._minute_records[service]) >= self._max_per_minute:
            self._limited_counts[service] += 1
            logger.warning(
                f"服务 {service} 触发分钟限流 "
                f"({len(self._minute_records[service])}/{self._max_per_minute})"
            )
            return False

        if len(self._hour_records[service]) >= self._max_per_hour:
            self._limited_counts[service] += 1
            logger.warning(
                f"服务 {service} 触发小时限流 "
                f"({len(self._hour_records[service])}/{self._max_per_hour})"
            )
            return False

        self._limited_counts[service] = 0
        return True

    async def record(self, service: str) -> None:
        """记录一次修复"""
        now = time.time()
        self._minute_records[service].append(now)
        self._hour_records[service].append(now)

    def should_alert(self, service: str) -> bool:
        """连续限流超过阈值时返回 True"""
        return self._limited_counts[service] >= ALERT_THRESHOLD
