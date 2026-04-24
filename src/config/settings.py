"""配置管理模块"""
from typing import Optional, Dict, Any
from pathlib import Path
import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebhookConfig(BaseModel):
    """Webhook服务配置"""
    secret: str = Field(default="", description="GitHub Webhook密钥")
    host: str = Field(default="0.0.0.0", description="监听主机地址")
    port: int = Field(default=8000, description="监听端口")
    reload: bool = Field(default=False, description="是否启用热重载")
    allowed_events: list[str] = Field(
        default_factory=lambda: ["workflow_run", "pull_request", "check_run"],
        description="允许的事件类型"
    )
    max_payload_size: str = Field(default="10MB", description="最大请求体大小")
    event_queue_maxsize: int = Field(default=1000, description="事件队列最大容量")
    max_processed_ids: int = Field(default=10000, description="最大保存的已处理事件ID数量")
    shutdown_timeout: int = Field(default=30, description="优雅关闭超时时间（秒）")


class LoggingConfig(BaseModel):
    """日志配置"""
    level: str = Field(default="INFO", description="日志级别")
    dir: str = Field(default="logs", description="日志目录")
    retention_days: int = Field(default=30, description="日志保留天数")
    json_format: bool = Field(default=True, description="是否使用JSON格式输出")


class Settings(BaseSettings):
    """全局配置"""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore"
    )

    # Webhook配置
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)

    # 日志配置
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # 环境
    environment: str = Field(default="development", description="运行环境")
    debug: bool = Field(default=False, description="是否开启调试模式")

    @classmethod
    def load_from_yaml(cls, config_path: Optional[str] = None) -> "Settings":
        """
        从YAML配置文件加载配置

        Args:
            config_path: 配置文件路径，默认查找config/agent-config.yaml

        Returns:
            Settings: 配置实例
        """
        if not config_path:
            config_path = "config/agent-config.yaml"

        config_path = Path(config_path)
        if not config_path.exists():
            return cls()

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}
            return cls(**config_data)
        except Exception as e:
            raise RuntimeError(f"Failed to load config file: {e}") from e


def get_settings(
    config_path: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None
) -> Settings:
    """
    获取配置实例，支持配置文件和参数覆盖

    Args:
        config_path: 配置文件路径
        overrides: 覆盖配置的字典，优先级最高

    Returns:
        Settings: 配置实例
    """
    # 从配置文件加载
    settings = Settings.load_from_yaml(config_path)

    # 应用覆盖配置
    if overrides:
        # 处理嵌套配置
        for section, section_overrides in overrides.items():
            if not hasattr(settings, section):
                continue

            # 如果是字典，说明是嵌套配置
            if isinstance(section_overrides, dict):
                section_obj = getattr(settings, section)
                for key, value in section_overrides.items():
                    if value is not None and hasattr(section_obj, key):
                        setattr(section_obj, key, value)
            else:
                # 顶层配置
                if section_overrides is not None:
                    setattr(settings, section, section_overrides)

    return settings
