"""Webhook服务相关命令"""
import typer
from rich.console import Console

webhook_app = typer.Typer(help="GitHub Webhook服务管理")
console = Console()


@webhook_app.command("config")
def show_config(
    config: str = typer.Option(None, "--config", "-c", help="配置文件路径"),
    host: str = typer.Option(None, "--host", help="监听主机地址"),
    port: str = typer.Option(None, "--port", "-p", help="监听端口"),
    secret: str = typer.Option(None, "--secret", "-s", help="GitHub Webhook密钥"),
    log_level: str = typer.Option(None, "--log-level", help="日志级别"),
):
    """显示当前配置（用于调试）"""
    from src.config.settings import get_settings
    from rich.table import Table

    overrides = {}

    # Webhook配置覆盖
    webhook_overrides = {}
    if host is not None:
        webhook_overrides["host"] = host
    if port is not None:
        webhook_overrides["port"] = port
    if secret is not None:
        webhook_overrides["secret"] = secret

    if webhook_overrides:
        overrides["webhook"] = webhook_overrides

    # 日志配置覆盖
    if log_level is not None:
        overrides["logging"] = {"level": log_level}

    settings = get_settings(config_path=config, overrides=overrides)

    table = Table(title="GitHub Webhook配置")
    table.add_column("配置项", style="cyan")
    table.add_column("值", style="green")

    # Webhook配置
    for key, value in settings.webhook.model_dump().items():
        if key == "secret":
            value = "*" * 8 if value else "未设置"
        table.add_row(f"webhook.{key}", str(value))

    # 日志配置
    for key, value in settings.logging.model_dump().items():
        table.add_row(f"logging.{key}", str(value))

    # 全局配置
    table.add_row("environment", settings.environment)
    table.add_row("debug", str(settings.debug))

    console.print(table)
