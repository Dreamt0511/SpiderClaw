"""监控器基类"""
from abc import ABC, abstractmethod
from typing import Any
from src.bus import EventBus


class BaseMonitor(ABC):
    """监控器抽象基类"""

    def __init__(self, event_bus: EventBus):
        """
        初始化监控器

        Args:
            event_bus: 事件总线实例
        """
        self.event_bus = event_bus
        self.running = False

    @abstractmethod
    async def start(self) -> None:
        """启动监控器"""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """停止监控器"""
        pass

    async def publish_event(self, event: Any) -> bool:
        """
        发布事件到事件总线

        Args:
            event: 要发布的事件

        Returns:
            bool: 发布成功返回True
        """
        return await self.event_bus.publish(event)
