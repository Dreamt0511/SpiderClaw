"""所有监控模块的基类"""
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import RenderableType
    from .state import DashboardState


class MonitorModule(ABC):
    """监控模块基类 — 任何新监控源只需继承此类并实现 render()。"""

    name: str = "unnamed"

    @abstractmethod
    def render(self, state: "DashboardState") -> "RenderableType":
        ...
