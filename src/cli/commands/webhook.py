"""Webhook服务相关命令"""
import asyncio
import os
import typer
from rich.console import Console
from rich.table import Table

from src.config.settings import get_settings
from src.utils.logging import setup_logging, get_logger
from src.bus import get_event_bus, GitHubEvent
from src.monitor import GitHubWebhookMonitor
from src.agent.orchestrator import RepairOrchestrator

webhook_app = typer.Typer(help="GitHub Webhook服务管理")
console = Console()
logger = get_logger(__name__)


@webhook_app.command("start")
def start(
    host: str = typer.Option(None, "--host", help="监听主机地址"),
    port: int = typer.Option(None, "--port", "-p", help="监听端口"),
    secret: str = typer.Option(None, "--secret", "-s", help="GitHub Webhook密钥"),
    reload: bool = typer.Option(None, "--reload", help="启用热重载（开发环境）"),
    log_level: str = typer.Option(None, "--log-level", help="日志级别"),
    config: str = typer.Option(None, "--config", "-c", help="配置文件路径"),
):
    """启动GitHub Webhook服务"""
    # 构建覆盖配置
    overrides = {}

    # Webhook配置覆盖
    webhook_overrides = {}
    if host is not None:
        webhook_overrides["host"] = host
    if port is not None:
        webhook_overrides["port"] = port
    if secret is not None:
        webhook_overrides["secret"] = secret
    if reload is not None:
        webhook_overrides["reload"] = reload

    if webhook_overrides:
        overrides["webhook"] = webhook_overrides

    # 日志配置覆盖
    if log_level is not None:
        overrides["logging"] = {"level": log_level}

    # 加载配置
    settings = get_settings(config_path=config, overrides=overrides)

    # 验证必填配置
    if not settings.webhook.secret:
        console.print("[red]错误：必须提供GitHub Webhook密钥，可以通过--secret参数、环境变量或配置文件设置[/red]")
        raise typer.Exit(code=1)

    # 设置SSL验证环境变量
    os.environ["SSL_VERIFY"] = str(settings.webhook.ssl_verify).lower()

    # 设置日志
    setup_logging(
        log_level=settings.logging.level,
        log_dir=settings.logging.dir,
        json_format=settings.logging.json_format,
        retention_days=settings.logging.retention_days,
        service_name="github-webhook"
    )

    # 初始化事件总线
    event_bus = get_event_bus(
        maxsize=settings.webhook.event_queue_maxsize,
        max_processed_ids=settings.webhook.max_processed_ids
    )

    # 初始化Webhook服务
    monitor = GitHubWebhookMonitor(
        event_bus=event_bus,
        secret=settings.webhook.secret,
        host=settings.webhook.host,
        port=settings.webhook.port,
        reload=settings.webhook.reload,
        allowed_events=set(settings.webhook.allowed_events)
    )

    # 打印启动信息
    table = Table(title="GitHub Webhook服务配置")
    table.add_column("配置项", style="cyan")
    table.add_column("值", style="green")
    table.add_row("监听地址", f"{settings.webhook.host}:{settings.webhook.port}")
    table.add_row("允许的事件类型", ", ".join(settings.webhook.allowed_events))
    table.add_row("事件队列容量", str(settings.webhook.event_queue_maxsize))
    table.add_row("日志级别", settings.logging.level)
    table.add_row("运行环境", settings.environment)
    console.print(table)

    console.print(f"[green]启动GitHub Webhook服务，访问 http://{settings.webhook.host}:{settings.webhook.port}/health 检查健康状态[/green]")
    console.print(f"[blue]Webhook端点地址: http://{settings.webhook.host}:{settings.webhook.port}/webhook/github[/blue]")

    # 初始化修复编排器
    orchestrator = None
    if settings.agent.enabled:
        console.print("[green]自动修复功能已启用[/green]")
        try:
            orchestrator = RepairOrchestrator(
                github_token=settings.github.token,
                openai_api_key=settings.openai.api_key,
                openai_base_url=settings.openai.base_url,
                llm_model=settings.openai.model_name,
                max_retries=settings.agent.max_retries,
                max_change_lines=settings.agent.max_change_lines
            )
        except Exception as e:
            console.print(f"[yellow]警告: 初始化修复编排器失败: {e}，自动修复功能已禁用[/yellow]")

    async def event_consumer():
        """事件消费循环"""
        if not orchestrator:
            return

        logger.info("事件消费循环已启动，等待CI失败事件...")
        while True:
            try:
                event = await event_bus.subscribe()

                # 仅处理失败的CI事件
                if isinstance(event, GitHubEvent) and event.conclusion == "failure":
                    logger.info(f"收到CI失败事件: {event.event_id}, 仓库: {event.repository}, 分支: {event.branch}")

                    async def process_and_mark_done():
                        try:
                            await orchestrator.run(event)
                        finally:
                            event_bus.mark_done()

                    asyncio.create_task(
                        process_and_mark_done(),
                        name=f"repair_{event.event_id}"
                    )
                else:
                    event_bus.mark_done()

            except asyncio.CancelledError:
                logger.info("事件消费循环被取消")
                break
            except Exception as e:
                logger.error(f"事件消费出错: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def run_services():
        """同时运行Webhook服务和事件消费"""
        tasks = []

        # 启动Webhook服务
        webhook_task = asyncio.create_task(monitor.start())
        tasks.append(webhook_task)

        # 启动事件消费（如果启用了自动修复）
        if orchestrator:
            consumer_task = asyncio.create_task(event_consumer())
            tasks.append(consumer_task)

        # 等待所有任务完成
        await asyncio.gather(*tasks, return_exceptions=True)

    # 运行服务
    try:
        asyncio.run(run_services())
    except KeyboardInterrupt:
        console.print("\n[yellow]服务已停止[/yellow]")


@webhook_app.command("config")
def show_config(
    config: str = typer.Option(None, "--config", "-c", help="配置文件路径"),
    host: str = typer.Option(None, "--host", help="监听主机地址"),
    port: int = typer.Option(None, "--port", "-p", help="监听端口"),
    secret: str = typer.Option(None, "--secret", "-s", help="GitHub Webhook密钥"),
    log_level: str = typer.Option(None, "--log-level", help="日志级别"),
):
    """显示当前配置（用于调试）"""
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
