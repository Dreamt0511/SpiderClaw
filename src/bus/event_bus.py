"""事件总线实现"""
import asyncio
import logging
from typing import Any, Optional, Set
from datetime import datetime
from .schemas import BaseEvent

logger = logging.getLogger(__name__)


class EventBus:
    """异步事件总线，支持反压和幂等去重"""

    def __init__(
        self,
        maxsize: int = 1000,
        max_processed_ids: int = 10000,
        processed_ids_ttl: int = 3600  # 已处理ID过期时间（秒）
    ):
        """
        初始化事件总线

        Args:
            maxsize: 队列最大容量，0表示无限制
            max_processed_ids: 最多保存的已处理事件ID数量
            processed_ids_ttl: 已处理ID的过期时间，过期后自动清理
        """
        self.queue = asyncio.Queue(maxsize=maxsize)
        self._max_processed_ids = max_processed_ids
        self._processed_ids_ttl = processed_ids_ttl

        # 已处理事件ID集合和对应的处理时间
        self._processed_ids: Set[str] = set()
        self._processed_times: dict[str, datetime] = {}
        self._processed_lock = asyncio.Lock()

        # 统计信息
        self._published_count = 0
        self._dropped_count = 0
        self._duplicate_count = 0
        self._start_time = datetime.now()

    async def publish(self, event: BaseEvent) -> bool:
        """
        发布事件到总线（非阻塞）

        Args:
            event: 要发布的事件

        Returns:
            bool: 发布成功返回True，队列满返回False
        """
        # 检查是否是重复事件
        if await self.is_duplicate(event.event_id):
            logger.info("Duplicate event skipped: %s", event.event_id)
            self._duplicate_count += 1
            return True  # 重复事件返回成功，避免GitHub重试

        try:
            self.queue.put_nowait(event)
            self._published_count += 1
            logger.debug("Event published: %s, type: %s", event.event_id, event.event_type)
            return True
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropped event: %s", event.event_id)
            self._dropped_count += 1
            return False

    async def subscribe(self) -> BaseEvent:
        """
        订阅事件（阻塞）。消费者处理完事件后应调用 mark_done()。

        Returns:
            BaseEvent: 从队列中获取的事件
        """
        event = await self.queue.get()
        return event

    def mark_done(self) -> None:
        """标记当前事件已处理完成，配合 subscribe() 使用。"""
        self.queue.task_done()

    async def task_done(self) -> None:
        """标记当前事件已处理完成（别名，兼容旧调用）。"""
        self.queue.task_done()

    async def is_duplicate(self, event_id: str) -> bool:
        """
        检查事件是否已处理

        Args:
            event_id: 事件ID

        Returns:
            bool: 已处理返回True，否则返回False
        """
        async with self._processed_lock:
            # 先清理过期的ID
            await self._cleanup_expired_ids()

            if event_id in self._processed_ids:
                return True

            # 超过容量时，删除最早的ID，保持容量不超过最大值
            if len(self._processed_ids) >= self._max_processed_ids:
                # 按时间排序，删除最早的1个
                sorted_ids = sorted(self._processed_times.items(), key=lambda x: x[1])
                oldest_id = sorted_ids[0][0]
                self._processed_ids.remove(oldest_id)
                del self._processed_times[oldest_id]

            self._processed_ids.add(event_id)
            self._processed_times[event_id] = datetime.now()
            return False

    async def _cleanup_expired_ids(self) -> None:
        """清理过期的已处理ID"""
        now = datetime.now()
        expired_ids = [
            event_id for event_id, process_time in self._processed_times.items()
            if (now - process_time).total_seconds() > self._processed_ids_ttl
        ]

        for event_id in expired_ids:
            self._processed_ids.remove(event_id)
            del self._processed_times[event_id]

        if expired_ids:
            logger.debug("Cleaned up %d expired event IDs", len(expired_ids))

    async def drain(self) -> None:
        """等待队列中的所有事件处理完成"""
        await self.queue.join()

    def qsize(self) -> int:
        """获取当前队列大小"""
        return self.queue.qsize()

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        return {
            "queue_size": self.qsize(),
            "published_count": self._published_count,
            "dropped_count": self._dropped_count,
            "duplicate_count": self._duplicate_count,
            "processed_ids_count": len(self._processed_ids),
            "uptime_seconds": (datetime.now() - self._start_time).total_seconds()
        }


# 全局事件总线实例
_event_bus: Optional[EventBus] = None


def get_event_bus(**kwargs) -> EventBus:
    """获取全局事件总线实例"""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus(**kwargs)
    return _event_bus
