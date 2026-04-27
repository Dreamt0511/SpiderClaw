"""GitHub Webhook服务实现"""
import asyncio
import hashlib
import hmac
import logging
import os
from datetime import datetime
from typing import Optional, Callable, Awaitable
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.logging import RichHandler
from .base import BaseMonitor
from src.bus import GitHubEvent, EventBus

# 强制启用颜色输出，即使输出到非终端
console = Console(force_terminal=True, color_system="auto")
logger = logging.getLogger(__name__)

# 支持的GitHub事件类型
SUPPORTED_EVENTS = {"workflow_run", "pull_request", "check_run"}


class GitHubWebhookMonitor(BaseMonitor):
    """GitHub Webhook监控器"""

    def __init__(
        self,
        event_bus: EventBus,
        secret: str,
        host: str = "0.0.0.0",
        port: int = 8000,
        reload: bool = False,
        allowed_events: Optional[set[str]] = None,
    ):
        """
        初始化GitHub Webhook服务

        Args:
            event_bus: 事件总线实例
            secret: GitHub Webhook密钥
            host: 监听主机地址
            port: 监听端口
            reload: 是否启用热重载（开发环境）
            allowed_events: 允许的事件类型集合
        """
        super().__init__(event_bus)
        self.secret = secret.encode() if isinstance(secret, str) else secret
        self.host = host
        self.port = port
        self.reload = reload
        self.allowed_events = allowed_events or SUPPORTED_EVENTS
        self.start_time = datetime.now()

        # 创建FastAPI应用
        self.app = FastAPI(title="SpiderClaw GitHub Webhook", version="1.0.0")
        self._setup_routes()
        self._setup_middleware()

        # Uvicorn服务器实例
        self.server: Optional[uvicorn.Server] = None
        self.config: Optional[uvicorn.Config] = None

    def _setup_middleware(self) -> None:
        """设置中间件"""
        # CORS配置
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # 请求日志中间件
        @self.app.middleware("http")
        async def log_requests(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
            start_time = datetime.now()
            response = await call_next(request)
            process_time = (datetime.now() - start_time).total_seconds() * 1000

            # 只记录Webhook请求
            if request.url.path == "/webhook/github":
                event_id = request.headers.get("X-GitHub-Delivery", "unknown")
                event_type = request.headers.get("X-GitHub-Event", "unknown")
                logger.info(
                    f"Webhook request processed: {event_id}, type: {event_type}, "
                    f"status: {response.status_code}, duration: {process_time:.2f}ms"
                )

            return response

    def _setup_routes(self) -> None:
        """设置路由"""

        @self.app.get("/health")
        async def health_check():
            """健康检查端点"""
            stats = self.event_bus.get_stats()
            return {
                "status": "ok",
                "service": "github-webhook",
                "start_time": self.start_time.isoformat(),
                **stats
            }

        @self.app.post("/webhook/github")
        async def handle_github_webhook(request: Request):
            """处理GitHub Webhook事件"""
            # 获取请求头信息
            event_id = request.headers.get("X-GitHub-Delivery")
            event_type = request.headers.get("X-GitHub-Event")
            signature_header = request.headers.get("X-Hub-Signature-256")

            if not event_id or not event_type or not signature_header:
                logger.warning("Missing required GitHub headers")
                raise HTTPException(status_code=400, detail="Missing required GitHub headers")

            # 验证事件类型是否支持
            if event_type not in self.allowed_events:
                logger.info(f"Ignoring unsupported event type: {event_type}")
                return {"status": "ignored", "reason": "unsupported event type"}

            # 读取请求体
            body = await request.body()

            # 验证签名
            signature_valid = self._verify_signature(body, signature_header)
            if not signature_valid:
                logger.warning(f"Invalid signature for event: {event_id}")
                raise HTTPException(status_code=403, detail="Invalid signature")

            # 解析payload
            try:
                payload = await request.json()
            except Exception as e:
                logger.error(f"Failed to parse JSON payload: {e}")
                raise HTTPException(status_code=400, detail="Invalid JSON payload")

            # 转换为内部事件格式
            try:
                event = self._convert_to_internal_event(
                    event_id=event_id,
                    event_type=event_type,
                    payload=payload,
                    signature_valid=signature_valid
                )
            except Exception as e:
                logger.error(f"Failed to convert event: {e}", exc_info=True)
                raise HTTPException(status_code=400, detail="Failed to process event")

            # 事件过滤：只处理需要触发修复的事件
            if event_type == "pull_request":
                # 只处理 PR opened 和 synchronize 事件，用于获取PR信息
                # 其他动作（closed、reopened等）忽略
                allowed_pr_actions = ["opened", "synchronize"]
                if event.action not in allowed_pr_actions:
                    logger.info(f"Ignoring pull_request event: {event_id}, action: {event.action}")
                    return {"status": "ignored", "reason": f"pull_request action '{event.action}' does not trigger repair"}
                # PR事件不需要检查conclusion，保留用于获取PR编号和分支信息
                logger.info(f"Accepted pull_request event: {event_id}, action: {event.action}, pr_number: {event.pr_number}")

            elif event_type == "workflow_run":
                # workflow_run 事件只处理 failure
                if event.conclusion != "failure":
                    logger.info(f"Ignoring workflow_run event: {event_id}, conclusion: {event.conclusion}")
                    return {"status": "ignored", "reason": f"workflow_run conclusion '{event.conclusion}' is not failure"}
                logger.info(f"Accepted workflow_run event: {event_id}, conclusion: {event.conclusion}")

            elif event_type == "check_run":
                # check_run 事件只处理 failure
                if event.conclusion != "failure":
                    logger.info(f"Ignoring check_run event: {event_id}, conclusion: {event.conclusion}")
                    return {"status": "ignored", "reason": f"check_run conclusion '{event.conclusion}' is not failure"}
                logger.info(f"Accepted check_run event: {event_id}, conclusion: {event.conclusion}")

            # 发布事件到总线
            publish_success = await self.publish_event(event)
            if not publish_success:
                raise HTTPException(status_code=503, detail="Service busy, please retry later")

            return {"status": "accepted", "event_id": event_id}

    def _verify_signature(self, body: bytes, signature_header: str) -> bool:
        """
        验证GitHub Webhook签名

        Args:
            body: 请求体内容
            signature_header: X-Hub-Signature-256请求头内容

        Returns:
            bool: 签名有效返回True
        """
        if not signature_header.startswith("sha256="):
            return False

        try:
            expected_signature = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
            received_signature = signature_header.split("=", 1)[1]
            return hmac.compare_digest(expected_signature, received_signature)
        except Exception:
            return False

    def _convert_to_internal_event(
        self,
        event_id: str,
        event_type: str,
        payload: dict,
        signature_valid: bool
    ) -> GitHubEvent:
        """
        将GitHub事件转换为内部统一格式

        Args:
            event_id: GitHub事件ID
            event_type: GitHub事件类型
            payload: 事件payload
            signature_valid: 签名是否有效

        Returns:
            GitHubEvent: 内部事件对象
        """
        action = payload.get("action", "")
        repository = payload.get("repository", {}).get("full_name", "")

        event = GitHubEvent(
            event_id=event_id,
            event_type=event_type,
            action=action,
            source="github_webhook",
            repository=repository,
            signature_valid=signature_valid,
            payload=payload
        )

        # 填充衍生字段
        event.clone_url = payload.get("repository", {}).get("clone_url", "")

        if event_type == "workflow_run":
            workflow_run = payload.get("workflow_run", {})
            event.branch = workflow_run.get("head_branch", "")
            event.conclusion = workflow_run.get("conclusion", "")
            event.logs_url = workflow_run.get("logs_url", "")

        elif event_type == "pull_request":
            pr = payload.get("pull_request", {})
            event.branch = pr.get("head", {}).get("ref", "")
            event.pr_number = pr.get("number")
            event.conclusion = pr.get("state", payload.get("action", ""))

        elif event_type == "check_run":
            check_run = payload.get("check_run", {})
            event.branch = check_run.get("check_suite", {}).get("head_branch", "")
            event.conclusion = check_run.get("conclusion", "")
            # 构造job日志的API URL，而不是网页URL
            job_id = check_run.get("id", "")
            if job_id and repository:
                event.logs_url = f"https://api.github.com/repos/{repository}/actions/jobs/{job_id}/logs"
            else:
                event.logs_url = check_run.get("details_url", "")
            prs = check_run.get("pull_requests", [])
            if prs:
                event.pr_number = prs[0].get("number")

        return event

    async def start(self) -> None:
        """启动Webhook服务"""
        if self.running:
            logger.warning("Webhook server is already running")
            return

        self.config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            reload=self.reload,
            log_level="info"
        )
        self.server = uvicorn.Server(self.config)

        self.running = True
        logger.info(f"GitHub Webhook server starting on {self.host}:{self.port}")

        try:
            await self.server.serve()
        except asyncio.CancelledError:
            logger.info("Webhook server received shutdown signal")
        finally:
            self.running = False
            logger.info("GitHub Webhook server stopped")

    async def stop(self) -> None:
        """停止Webhook服务"""
        if not self.running or not self.server:
            return

        logger.info("Stopping GitHub Webhook server...")
        self.server.should_exit = True

        # 等待服务器关闭
        while self.running:
            await asyncio.sleep(0.1)

        # 等待事件队列排空
        await self.event_bus.drain()
        logger.info("GitHub Webhook server stopped gracefully")


def run_webhook_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    reload: bool = False,
    secret: Optional[str] = None,
) -> None:
    """同步方式启动Webhook服务（用于CLI直接调用）"""
    from src.config.settings import get_settings
    from src.utils.logging import get_logger
    from src.bus import get_event_bus, GitHubEvent
    from src.agent.orchestrator import RepairOrchestrator

    log = get_logger(__name__)

    # 配置基础日志
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    handler = RichHandler(
        show_time=True,
        show_level=True,
        show_path=False,
        markup=True
    )
    logging.basicConfig(
        level="INFO",
        format="%(message)s",
        datefmt="[%X]",
        handlers=[handler]
    )

    settings = get_settings()

    # 设置SSL验证环境变量（CI日志下载等HTTP客户端会读取）
    os.environ["SSL_VERIFY"] = str(settings.webhook.ssl_verify).lower()

    webhook_secret = secret or settings.webhook.secret
    if not webhook_secret:
        console.print("[bold #ff4444]错误：Webhook secret 未配置，请通过 --secret 参数或配置文件设置[/bold #ff4444]")
        return

    event_bus = get_event_bus(
        maxsize=settings.webhook.event_queue_maxsize,
        max_processed_ids=settings.webhook.max_processed_ids,
    )

    monitor = GitHubWebhookMonitor(
        event_bus=event_bus,
        secret=webhook_secret,
        host=host,
        port=port,
        reload=reload,
        allowed_events=set(settings.webhook.allowed_events),
    )

    # 初始化修复编排器
    orchestrator = None
    if settings.agent.enabled:
        try:
            orchestrator = RepairOrchestrator(
                github_token=settings.github.token,
                openai_api_key=settings.openai.api_key,
                openai_base_url=settings.openai.base_url,
                llm_model=settings.openai.model_name,
                max_retries=settings.agent.max_retries,
                max_change_lines=settings.agent.max_change_lines,
                lark_notify_enabled=settings.lark.enabled,
                lark_notify_users=settings.lark.notify_users,
            )
        except Exception as e:
            log.warning(f"初始化修复编排器失败: {e}，自动修复功能已禁用")

    # 显示启动面板
    repair_status = "[green]已启用[/green]" if orchestrator else "[dim]未启用[/dim]"
    console.print(Panel(
        f"[bold #ffffff]SpiderClaw 总监控服务已启动[/bold #ffffff]\n\n"
        f"监听地址: [bold #20d5f0]http://{host}:{port}[/bold #20d5f0]\n"
        f"Webhook端点: [bold #20d5f0]/webhook/github[/bold #20d5f0]\n"
        f"健康检查: [bold #20d5f0]/health[/bold #20d5f0]\n"
        f"允许事件: [bold #20d5f0]{', '.join(settings.webhook.allowed_events)}[/bold #20d5f0]\n"
        f"自动修复: {repair_status}\n\n"
        f"[dim]按 Ctrl+C 停止服务[/dim]",
        title="[bold #20d5f0]SpiderClaw 运行中[/bold #20d5f0]",
        border_style="#20d5f0",
        padding=(1, 2)
    ))
    console.print()

    async def event_consumer():
        """事件消费循环"""
        if not orchestrator:
            return
        while True:
            try:
                event = await event_bus.subscribe()

                if isinstance(event, GitHubEvent) and event.conclusion == "failure":
                    log.info(f"收到CI失败事件: {event.event_id}, 仓库: {event.repository}, 分支: {event.branch}")

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
                break
            except Exception as e:
                log.error(f"事件消费出错: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _run():
        tasks = [asyncio.create_task(monitor.start())]
        if orchestrator:
            tasks.append(asyncio.create_task(event_consumer()))
        await asyncio.gather(*tasks, return_exceptions=True)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("Webhook server stopped by user")
