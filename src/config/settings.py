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
        default_factory=lambda: ["workflow_run", "pull_request"],
        description="允许的事件类型"
    )
    max_payload_size: str = Field(default="10MB", description="最大请求体大小")
    event_queue_maxsize: int = Field(default=1000, description="事件队列最大容量")
    max_processed_ids: int = Field(default=10000, description="最大保存的已处理事件ID数量")
    shutdown_timeout: int = Field(default=30, description="优雅关闭超时时间（秒）")
    ssl_verify: bool = Field(default=True, description="是否验证SSL证书")


class LoggingConfig(BaseModel):
    """日志配置"""
    level: str = Field(default="INFO", description="日志级别")
    dir: str = Field(default="src/logs", description="日志目录")
    retention_days: int = Field(default=30, description="日志保留天数")
    json_format: bool = Field(default=True, description="是否使用JSON格式输出")


class AgentConfig(BaseModel):
    """Agent配置"""
    enabled: bool = Field(default=False, description="是否启用自动修复功能")
    max_retries: int = Field(default=3, description="最大修复重试次数")
    max_change_lines: int = Field(default=50, description="最大允许变更行数")
    auto_create_pr: bool = Field(default=True, description="是否自动创建PR")
    require_human_approval: bool = Field(default=False, description="创建PR前是否需要人工审批")


class GitHubConfig(BaseModel):
    """GitHub配置"""
    token: str = Field(default="", description="GitHub访问令牌")
    api_url: str = Field(default="https://api.github.com", description="GitHub API地址")
    default_branch: str = Field(default="main", description="默认分支名称")


class OpenAIConfig(BaseModel):
    """OpenAI配置"""
    api_key: str = Field(default="", description="OpenAI API密钥")
    base_url: str = Field(default="https://api.openai.com/v1", description="API基础地址")
    model_name: str = Field(default="gpt-4o", description="LLM模型名称")
    timeout: int = Field(default=60, description="API请求超时时间（秒）")


class LarkConfig(BaseModel):
    """飞书通知配置"""
    enabled: bool = Field(default=False, description="是否启用飞书通知")
    app_id: str = Field(default="", description="飞书应用ID")
    app_secret: str = Field(default="", description="飞书应用密钥")
    notify_users: list[str] = Field(default_factory=list, description="需要通知的用户open_id列表")
    notify_groups: list[str] = Field(default_factory=list, description="需要通知的群组chat_id列表")
    # 多维表格配置
    base_enabled: bool = Field(default=False, description="是否启用飞书多维表格数据上报")
    base_token: str = Field(default="", description="飞书多维表格token")
    repair_table_id: str = Field(default="", description="修复记录表ID")
    auto_create_table: bool = Field(default=True, description="表不存在时是否自动创建修复记录表")
    auto_fix_fields: bool = Field(default=True, description="字段缺失时是否自动补全字段")
    as_bot: bool = Field(default=False, description="是否以机器人身份操作（需要将机器人添加到base协作成员）")
    # 告警配置
    alert_on_failure: bool = Field(default=True, description="上报失败时是否发送告警通知")
    alert_threshold: int = Field(default=3, description="连续失败多少次后发送告警")


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

    # Agent配置
    agent: AgentConfig = Field(default_factory=AgentConfig)

    # GitHub配置
    github: GitHubConfig = Field(default_factory=GitHubConfig)

    # OpenAI配置
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)

    # 飞书配置
    lark: LarkConfig = Field(default_factory=LarkConfig)

    # 环境
    environment: str = Field(default="development", description="运行环境")
    debug: bool = Field(default=False, description="是否开启调试模式")

    @classmethod
    def load_from_yaml(cls, config_path: Optional[str] = None) -> "Settings":
        """
        从YAML配置文件加载配置

        Args:
            config_path: 配置文件路径，默认查找src/config/agent-config.yaml

        Returns:
            Settings: 配置实例
        """
        if not config_path:
            config_path = "src/config/agent-config.yaml"

        config_path = Path(config_path)
        if not config_path.exists():
            return cls()

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}

            # 向后兼容：如果agent段有llm_model，自动迁移到openai段的model_name
            if "agent" in config_data and "llm_model" in config_data["agent"]:
                if "openai" not in config_data:
                    config_data["openai"] = {}
                # 只有当openai中没有model_name时才迁移，避免覆盖
                if "model_name" not in config_data["openai"]:
                    config_data["openai"]["model_name"] = config_data["agent"]["llm_model"]
                # 移除旧的配置项
                del config_data["agent"]["llm_model"]

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
