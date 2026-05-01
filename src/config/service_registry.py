"""服务注册表 — 启动时加载 services.yaml，支持按 service name 查询"""
import logging
from pathlib import Path
from typing import Optional
import yaml
from src.config.settings import ServiceConfig, ServicesConfig, RateLimitConfig

logger = logging.getLogger(__name__)


class ServiceRegistry:
    """服务注册表单例"""

    def __init__(self, config_path: str = "src/config/services.yaml"):
        self._services: dict[str, ServiceConfig] = {}
        self._rate_limit = RateLimitConfig()
        self._load(config_path)

    def _load(self, config_path: str) -> None:
        path = Path(config_path)
        if not path.exists():
            logger.warning(f"服务配置文件不存在: {config_path}")
            return

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        services_list = data.get("services") or []
        for svc_data in services_list:
            svc = ServiceConfig(**svc_data)
            self._services[svc.name] = svc
            logger.info(f"注册服务: {svc.name} -> {svc.repo_url}")

        rate_data = data.get("rate_limit", {})
        if rate_data:
            self._rate_limit = RateLimitConfig(**rate_data)

        logger.info(f"已注册 {len(self._services)} 个服务")

    def get(self, service_name: str) -> Optional[ServiceConfig]:
        """按服务名查询配置"""
        return self._services.get(service_name)

    def list_services(self) -> list[str]:
        """列出所有已注册的服务名"""
        return list(self._services.keys())

    @property
    def rate_limit(self) -> RateLimitConfig:
        return self._rate_limit


# 全局单例
_service_registry: Optional[ServiceRegistry] = None


def get_service_registry(config_path: str = "src/config/services.yaml") -> ServiceRegistry:
    """获取全局服务注册表实例"""
    global _service_registry
    if _service_registry is None:
        _service_registry = ServiceRegistry(config_path)
    return _service_registry


def reset_service_registry() -> None:
    """重置全局实例（仅用于测试）"""
    global _service_registry
    _service_registry = None
