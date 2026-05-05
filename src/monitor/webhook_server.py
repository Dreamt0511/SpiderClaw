"""GitHub Webhook服务实现"""
import asyncio
import hashlib
import hmac
import json
import logging
import logging.handlers
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
from src.bus.schemas import RuntimeLogEvent
from src.config.service_registry import get_service_registry
from src.store.repair_store import get_pending_event_store, get_pending_push_store
from src.utils.audit import audit_logger
from src.utils.rate_limiter import ServiceRateLimiter
from src.utils.version_manager import pre_sync_repos

class SafeRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """文件被删除后自动重建的日志 Handler。"""

    def emit(self, record):
        try:
            if self.stream and self.stream.closed:
                self.stream = self._open()
            elif self.stream and not os.path.exists(self.baseFilename):
                self.stream.close()
                self.stream = self._open()
        except Exception:
            self.stream = self._open()
        super().emit(record)


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

        # 确保文件日志始终写入（绕过 structlog，直接写入日志文件）
        log_dir = "src/logs"
        os.makedirs(log_dir, exist_ok=True)
        if not any(
            isinstance(h, (SafeRotatingFileHandler, logging.handlers.TimedRotatingFileHandler))
            and h.baseFilename.endswith("spiderclaw.log")
            for h in logging.root.handlers
        ):
            file_handler = SafeRotatingFileHandler(
                os.path.join(log_dir, "spiderclaw.log"),
                when="midnight",
                interval=1,
                backupCount=30,
                encoding="utf-8",
            )
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            file_handler.setLevel(logging.DEBUG)
            logging.root.addHandler(file_handler)

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
                audit_logger.log_event("milestone", node="webhook_event", event_type="pull_request", event_id=event_id, pr_number=event.pr_number)

            elif event_type == "workflow_run":
                # workflow_run 事件只处理 failure
                if event.conclusion != "failure":
                    logger.info(f"Ignoring workflow_run event: {event_id}, conclusion: {event.conclusion}")
                    return {"status": "ignored", "reason": f"workflow_run conclusion '{event.conclusion}' is not failure"}
                logger.info(f"Accepted workflow_run event: {event_id}, conclusion: {event.conclusion}")
                audit_logger.log_event("milestone", node="webhook_event", event_type="workflow_run", event_id=event_id)

            elif event_type == "check_run":
                # check_run 事件只处理 failure
                if event.conclusion != "failure":
                    logger.info(f"Ignoring check_run event: {event_id}, conclusion: {event.conclusion}")
                    return {"status": "ignored", "reason": f"check_run conclusion '{event.conclusion}' is not failure"}
                logger.info(f"Accepted check_run event: {event_id}, conclusion: {event.conclusion}")

            # 过滤 SpiderClaw 自动修复 PR 的 CI 事件，避免循环修复
            if event.branch and event.branch.startswith("autofix/"):
                logger.info(f"Ignoring SpiderClaw's own PR event: {event_id}, branch: {event.branch}")
                return {"status": "ignored", "reason": "SpiderClaw autofix branch, skip to avoid loop"}

            # 落盘：持久化事件到 SQLite，防止服务中断丢失
            pending_store = get_pending_event_store()
            event_payload = event.model_dump_json()
            pending_store.insert(
                event_id=event_id,
                event_type="github",
                payload=event_payload,
                source=event.repository,
            )

            # 发布事件到总线
            audit_logger.log_event(
                "system_action",
                action=f"收到 {event_type} 事件: {event.repository}#{event.pr_number}",
                event_id=event_id,
            )
            publish_success = await self.publish_event(event)
            if not publish_success:
                raise HTTPException(status_code=503, detail="Service busy, please retry later")

            return {"status": "accepted", "event_id": event_id}

        @self.app.post("/webhook/log")
        async def handle_log_webhook(request: Request):
            """接收远程运行时日志事件"""
            try:
                body = await request.json()
            except Exception as e:
                logger.error(f"解析日志请求体失败: {e}")
                raise HTTPException(status_code=400, detail="Invalid JSON payload")

            # 校验必填字段
            log_content = body.get("log", "")
            service_name = body.get("service", "")
            if not log_content or not service_name:
                raise HTTPException(status_code=400, detail="Missing required fields: 'log' and 'service'")

            # 查找服务配置
            registry = get_service_registry()
            svc = registry.get(service_name)

            # 创建事件（未注册的服务也发布，由编排器决定发通知）
            import uuid
            import re

            # 从 repo_url 提取仓库全名（如 "Dreamt0511/AutoFix_Test_rep"）
            def _extract_repo_full_name(url: str) -> str:
                if not url:
                    return ""
                # https://github.com/owner/repo.git → owner/repo
                m = re.search(r'github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$', url)
                return m.group(1) if m else ""

            repo_full_name = _extract_repo_full_name(svc.repo_url) if svc else ""

            event = RuntimeLogEvent(
                event_id=str(uuid.uuid4()),
                source="remote_log",
                log=log_content,
                service=service_name,
                version=body.get("version", ""),
                hostname=body.get("hostname", ""),
                repo_url=svc.repo_url if svc else "",
                repo_local_path=svc.repo_local_path if svc else "",
                branch=svc.git_branch if svc else "main",
                path_mapping=svc.path_mapping if svc else {},
                # 兼容字段
                repository=repo_full_name,
                clone_url=svc.repo_url if svc else "",
            )

            if not svc:
                logger.warning(f"未知服务: {service_name}")

            # 落盘：持久化事件到 SQLite，防止服务中断丢失
            pending_store = get_pending_event_store()
            event_payload = event.model_dump_json()
            pending_store.insert(
                event_id=event.event_id,
                event_type="runtime_log",
                payload=event_payload,
                source=service_name,
            )

            # 发布到事件总线
            publish_success = await self.publish_event(event)
            if not publish_success:
                raise HTTPException(status_code=503, detail="Service busy, please retry later")

            logger.info(f"接收运行时日志: service={service_name}, version={event.version}")
            audit_logger.log_event(
                "system_action",
                action=f"收到远程日志: {service_name}",
                event_id=event.event_id,
            )

            return {"status": "accepted", "event_id": event.event_id}


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
            # workflow_run 的 PR 编号在 pull_requests 数组中
            prs = workflow_run.get("pull_requests", [])
            if prs:
                event.pr_number = prs[0].get("number")

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
            log_level="info",
            log_config=None,  # 防止 uvicorn 覆盖已有的日志配置（TimedRotatingFileHandler 等）
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


def _start_lark_ws_thread(app_id, app_secret, event_queue, connected_event=None):
    """线程方式启动 lark SDK WebSocket 客户端（Windows 下子进程回调不触发，必须用线程）"""
    from lark_oapi import ws as lark_ws, LogLevel as LarkLogLevel
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

    def on_approval_event(data):
        try:
            event = data.event or {}
            instance_code = event.get("instance_code", "")
            status = event.get("status", "")
            print(f"[approval-ws] 收到: instance_code={instance_code}, status={status}", flush=True)
            if instance_code:
                event_queue.put((instance_code, status))
        except Exception as e:
            print(f"[approval-ws] 回调异常: {e}", flush=True)

    event_handler = EventDispatcherHandler.builder("", "") \
        .register_p1_customized_event("approval_instance", on_approval_event) \
        .build()

    client = lark_ws.Client(
        app_id=app_id, app_secret=app_secret,
        event_handler=event_handler,
        log_level=LarkLogLevel.DEBUG,
        auto_reconnect=True,
    )

    # monkey-patch _connect：连接建立后通知主线程
    if connected_event is not None:
        _original_connect = client._connect

        async def _patched_connect():
            await _original_connect()
            connected_event.set()

        client._connect = _patched_connect

    print("[approval-ws] 启动 WebSocket 客户端", flush=True)
    client.start()


def run_webhook_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    reload: bool = False,
    secret: Optional[str] = None,
    console_output: bool = True,
    config_path: Optional[str] = None,
    log_level: Optional[str] = None,
) -> None:
    """同步方式启动Webhook服务（用于CLI直接调用）

    Args:
        host: 监听主机地址
        port: 监听端口
        reload: 是否启用热重载
        secret: GitHub Webhook密钥
        console_output: 是否输出到控制台（dashboard模式下为False）
        config_path: 配置文件路径（None则使用默认路径）
        log_level: 日志级别覆盖（None则使用配置文件中的值）
    """
    from src.config.settings import get_settings
    from src.utils.logging import get_logger
    from src.bus import get_event_bus, GitHubEvent
    from src.bus.schemas import RuntimeLogEvent
    from src.agent.orchestrator import RepairOrchestrator

    # 构建配置覆盖
    overrides = {}
    if log_level is not None:
        overrides["logging"] = {"level": log_level}

    settings = get_settings(config_path=config_path, overrides=overrides)

    log = get_logger(__name__)

    # 配置基础日志
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    log_dir = settings.logging.dir
    os.makedirs(log_dir, exist_ok=True)

    # 文件日志（主日志，自动轮转）
    file_handler = SafeRotatingFileHandler(
        os.path.join(log_dir, "spiderclaw.log"),
        when="midnight",
        interval=1,
        backupCount=settings.logging.retention_days,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    file_handler.setLevel(logging.INFO)

    # 详细日志（保存到 detail.log，不与 dashboard 共享）
    detail_handler = SafeRotatingFileHandler(
        os.path.join(log_dir, "detail.log"),
        when="midnight",
        interval=1,
        backupCount=settings.logging.retention_days,
        encoding="utf-8",
    )
    detail_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d\n%(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    detail_handler.setLevel(logging.DEBUG)

    # 控制台日志（dashboard 模式下不输出到终端，避免抢显）
    if console_output:
        console_handler = RichHandler(
            show_time=True, show_level=True, show_path=False, markup=True
        )
        console_handler.setLevel(logging.INFO)
        logging.root.addHandler(console_handler)
    else:
        # 抑制 uvicorn 自身的控制台输出
        for uvi_logger in ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi"):
            uvi = logging.getLogger(uvi_logger)
            uvi.handlers.clear()
            uvi.setLevel(logging.WARNING)

    # 直接添加 handler（比 logging.basicConfig 更可靠，不受线程竞争影响）
    logging.root.addHandler(file_handler)
    logging.root.addHandler(detail_handler)
    log_level_int = getattr(logging, settings.logging.level.upper(), logging.DEBUG)
    logging.root.setLevel(log_level_int)

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
                lark_base_enabled=settings.lark.base_enabled,
                lark_base_token=settings.lark.base_token,
                lark_base_repair_table_id=settings.lark.repair_table_id,
                lark_as_bot=settings.lark.as_bot,
                lark_auto_create_table=settings.lark.auto_create_table,
                lark_auto_fix_fields=settings.lark.auto_fix_fields,
                lark_alert_on_failure=settings.lark.alert_on_failure,
                lark_alert_threshold=settings.lark.alert_threshold,
                environment=settings.environment
            )
        except Exception as e:
            log.warning(f"初始化修复编排器失败: {e}，自动修复功能已禁用")

    # 显示启动面板（dashboard 模式不重复输出）
    if console_output:
        repair_status = "[green]已启用[/green]" if orchestrator else "[dim]未启用[/dim]"
        console.print(Panel(
            f"[bold #ffffff]SpiderClaw 总监控服务已启动[/bold #ffffff]\n\n"
            f"监听地址: [bold #20d5f0]http://{host}:{port}[/bold #20d5f0]\n"
            f"Webhook端点: [bold #20d5f0]/webhook/github[/bold #20d5f0]\n"
            f"健康检查: [bold #20d5f0]/health[/bold #20d5f0]\n"
            f"允许事件: [bold #20d5f0]{', '.join(settings.webhook.allowed_events)}[/bold #20d5f0]\n"
            f"自动修复: [bold #20d5f0]{repair_status}[/bold #20d5f0]\n\n"
            f"[#5a6b7c]备注：开发环境下可以使用 ngrok http 8000 来暴露服务，方便外部访问[#5a6b7c]\n",
            title="[bold #20d5f0]SpiderClaw 运行中[/bold #20d5f0]",
            border_style="#20d5f0",
            padding=(1, 2)
        ))
        console.print()

    # 初始化限流器
    rate_limiter = ServiceRateLimiter(
        max_per_minute=get_service_registry().rate_limit.max_fixes_per_minute,
        max_per_hour=get_service_registry().rate_limit.max_fixes_per_hour,
    )

    approval_ws_ready = asyncio.Event()
    _repair_lock = asyncio.Lock()  # 修复流程排队锁，确保串行执行

    async def recover_pending_events():
        """启动时恢复上次中断的未处理事件"""
        if not orchestrator:
            return
        pending_store = get_pending_event_store()

        # 1. 重置卡住的 processing 事件为 pending
        pending_store.reset_processing_to_pending()

        # 2. 统计待处理事件数量
        pending_events = pending_store.get_all_pending()
        count = len(pending_events)
        if count == 0:
            return

        # 3. 根据数量决定自动恢复或通知开发者
        threshold = settings.agent.pending_event_auto_threshold
        if count <= threshold:
            log.info(f"自动恢复 {count} 个待处理事件")
            for record in pending_events:
                try:
                    payload = json.loads(record["payload"])
                    if record["event_type"] == "runtime_log":
                        event_obj = RuntimeLogEvent(**payload)
                    else:
                        event_obj = GitHubEvent(**payload)
                    await event_bus.publish(event_obj)
                    log.info(f"已恢复事件: {record['event_id']} ({record['source']})")
                except Exception as e:
                    log.error(f"恢复事件 {record['event_id']} 失败: {e}")
                    pending_store.delete(record["event_id"])
        else:
            log.warning(f"待处理事件数量 ({count}) 超过阈值 ({threshold})，需人工确认")
            # 等待飞书 WebSocket 长连接就绪后再发送审批，避免审批回调丢失
            log.info("[approval] 等待飞书长连接就绪...")
            await approval_ws_ready.wait()
            log.info("[approval] 长连接已就绪，开始创建审批")
            try:
                from src.notify.lark_notify import (
                    ensure_approval_definition,
                    subscribe_approval_events,
                    send_pending_events_notification,
                )
                from src.store.repair_store import get_pending_approval_store

                # 自动确保审批定义存在
                approver = settings.lark.notify_users[0] if settings.lark.notify_users else ""
                approval_code, widget_id = await ensure_approval_definition(
                    config_approval_code=settings.lark.approval_code,
                    approver_open_id=approver,
                )

                if approval_code and widget_id:
                    # 订阅审批事件（幂等，重复调用无副作用）
                    await subscribe_approval_events(approval_code)

                    event_summaries = [
                        {
                            "event_id": r["event_id"],
                            "event_type": r["event_type"],
                            "source": r["source"],
                            "created_at": r["created_at"],
                        }
                        for r in pending_events
                    ]
                    instance_code = await send_pending_events_notification(
                        event_summaries, count, settings.lark.notify_users,
                        approval_code=approval_code,
                        widget_id=widget_id,
                    )
                    if instance_code:
                        approval_store = get_pending_approval_store()
                        approval_store.insert(instance_code, count)
                        log.info(f"审批实例已创建: {instance_code}")
                    else:
                        log.error("创建审批实例失败")
                else:
                    log.error("审批定义创建失败，跳过通知")
            except Exception as e:
                log.error(f"发送待处理事件通知失败: {e}")

    async def event_consumer():
        """事件消费循环"""
        if not orchestrator:
            return
        pending_store = get_pending_event_store()

        # === runtime_log 事件缓冲：按服务聚合，短时间内同一服务的多个事件合并为一次修复 ===
        _runtime_buffer: dict[str, list[RuntimeLogEvent]] = {}  # service → [events]
        _runtime_flush_timers: dict[str, asyncio.Task] = {}     # service → timer task
        _buffer_window = 5  # 秒：同一服务的事件在此窗口内合并

        async def _flush_service_buffer(service: str):
            """合并并处理同一服务的缓冲事件"""
            await asyncio.sleep(_buffer_window)
            events = _runtime_buffer.pop(service, [])
            _runtime_flush_timers.pop(service, None)
            if not events:
                return

            if len(events) == 1:
                # 单个事件，直接处理
                merged_event = events[0]
                log.info(f"服务 {service}: 1 个事件，直接处理")
            else:
                # 多个事件合并：日志拼接，元数据取第一个
                combined_log = "\n".join(evt.log for evt in events)
                base = events[0]
                merged_event = RuntimeLogEvent(
                    event_id=base.event_id,
                    source=base.source,
                    log=combined_log,
                    service=service,
                    version=base.version,
                    hostname=base.hostname,
                    repo_url=base.repo_url,
                    repo_local_path=base.repo_local_path,
                    branch=base.branch,
                    path_mapping=base.path_mapping,
                    repository=base.repository,
                    clone_url=base.clone_url,
                )
                log.info(
                    f"服务 {service}: 合并 {len(events)} 个事件为一次修复 "
                    f"（日志合计 {len(combined_log)} 字符）"
                )
                # 清理被合并事件的 pending 记录（保留 base 的 event_id）
                for evt in events[1:]:
                    pending_store.delete(evt.event_id)
                    event_bus.mark_done()

            try:
                async with _repair_lock:
                    await orchestrator.run(merged_event)
            except Exception as e:
                log.error(f"处理合并事件失败: {e}", exc_info=True)
            finally:
                pending_store.delete(merged_event.event_id)
                event_bus.mark_done()

        while True:
            try:
                event = await event_bus.subscribe()

                # 标记事件为处理中
                pending_store.mark_processing(event.event_id)

                # 远程日志事件处理：缓冲聚合，不立即处理
                if isinstance(event, RuntimeLogEvent):
                    if not await rate_limiter.check(event.service):
                        log.warning(f"服务 {event.service} 触发限流，跳过")
                        if rate_limiter.should_alert(event.service):
                            log.error(f"服务 {event.service} 持续限流，请人工检查")
                        pending_store.delete(event.event_id)
                        event_bus.mark_done()
                        continue
                    await rate_limiter.record(event.service)

                    # 加入缓冲
                    svc = event.service
                    if svc not in _runtime_buffer:
                        _runtime_buffer[svc] = []
                    _runtime_buffer[svc].append(event)

                    # 重置 flush timer：每次新事件到来都重新计时
                    if svc in _runtime_flush_timers:
                        _runtime_flush_timers[svc].cancel()
                    _runtime_flush_timers[svc] = asyncio.create_task(
                        _flush_service_buffer(svc)
                    )
                    continue

                # GitHub CI 事件处理（原有逻辑）
                if isinstance(event, GitHubEvent) and event.conclusion == "failure":
                    log.info(f"收到CI失败事件: {event.event_id}, 仓库: {event.repository}, 分支: {event.branch}")

                    async def process_and_mark_done(evt=event):
                        try:
                            async with _repair_lock:
                                await orchestrator.run(evt)
                        finally:
                            pending_store.delete(evt.event_id)
                            event_bus.mark_done()

                    asyncio.create_task(
                        process_and_mark_done(),
                        name=f"repair_{event.event_id}"
                    )
                else:
                    pending_store.delete(event.event_id)
                    event_bus.mark_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"事件消费出错: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def approval_event_listener():
        """通过飞书 SDK WebSocket 长连接监听审批状态变更事件"""
        log.info("[approval] approval_event_listener 任务已启动")

        if not settings.lark.app_id or not settings.lark.app_secret:
            log.error("[approval] 飞书 app_id 或 app_secret 未配置，无法监听审批事件")
            return

        pending_store = get_pending_event_store()
        from src.store.repair_store import get_pending_approval_store
        approval_store = get_pending_approval_store()

        # 1. 先启动 WebSocket 长连接
        import queue as thread_queue
        import threading

        event_queue = thread_queue.Queue()
        ws_connected = threading.Event()

        ws_thread = threading.Thread(
            target=_start_lark_ws_thread,
            args=(settings.lark.app_id, settings.lark.app_secret, event_queue, ws_connected),
            daemon=True, name="lark-ws",
        )
        ws_thread.start()
        log.info("[approval] 飞书 WebSocket 线程已启动，等待连接建立...")

        # 2. 等待 WebSocket 连接真正建立
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, ws_connected.wait)
        log.info("[approval] 飞书 WebSocket 已连接")

        # 3. 连接建立后再订阅审批事件
        from src.notify.lark_notify import _load_approval_code, subscribe_approval_events
        approval_code = settings.lark.approval_code or _load_approval_code()
        if approval_code:
            log.info(f"[approval] 订阅审批事件: {approval_code}")
            await subscribe_approval_events(approval_code)

        # 4. 通知就绪，recover_pending_events 可以创建审批了
        approval_ws_ready.set()
        log.info("[approval] 审批监听就绪")

        # 主循环：从线程 Queue 中读取事件并处理
        while True:
            try:
                instance_code, status = await asyncio.wait_for(
                    loop.run_in_executor(None, event_queue.get), timeout=30
                )
                log.info(f"[approval] 主循环收到事件: instance_code={instance_code}, status={status}")

                # PENDING 事件仅记录日志，不处理
                if status == "PENDING":
                    continue

                approval = approval_store.get_by_instance_code(instance_code)
                if not approval:
                    log.warning(f"[approval] 未找到审批记录: {instance_code}")
                    continue

                # 已处理过的审批不再重复处理
                if approval["status"] != "PENDING":
                    log.info(f"[approval] 审批已处理过: {instance_code}, 状态: {approval['status']}")
                    continue

                log.info(f"收到审批事件: {instance_code}, 状态: {status}")

                if status == "APPROVED":
                    pending_events = pending_store.get_all_pending()
                    count = 0
                    for record in pending_events:
                        try:
                            payload = json.loads(record["payload"])
                            if record["event_type"] == "runtime_log":
                                event_obj = RuntimeLogEvent(**payload)
                            else:
                                event_obj = GitHubEvent(**payload)
                            await event_bus.publish(event_obj)
                            count += 1
                        except Exception as e:
                            log.error(f"恢复事件 {record['event_id']} 失败: {e}")
                    # 不管修复流程成功还是失败，都清除待处理记录
                    deleted = pending_store.delete_all_pending()
                    log.info(f"审批通过：已恢复 {count} 个待处理事件，已清除 {deleted} 条记录")
                    approval_store.update_status(instance_code, "APPROVED")

                elif status in ("REJECTED", "CANCELED", "DELETED"):
                    count = pending_store.delete_all_pending()
                    log.info(f"审批拒绝/取消：已丢弃 {count} 个待处理事件")
                    approval_store.update_status(instance_code, status)

            except asyncio.TimeoutError:
                # 超时，检查线程是否还活着
                if not ws_thread.is_alive():
                    log.warning("[approval] 飞书 WebSocket 线程已退出，正在重启...")
                    event_queue = thread_queue.Queue()
                    ws_connected = threading.Event()
                    ws_thread = threading.Thread(
                        target=_start_lark_ws_thread,
                        args=(settings.lark.app_id, settings.lark.app_secret, event_queue, ws_connected),
                        daemon=True, name="lark-ws",
                    )
                    ws_thread.start()
                    await loop.run_in_executor(None, ws_connected.wait)
                    log.info("[approval] 飞书 WebSocket 重连成功")
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.error(f"[approval] 处理审批事件异常: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _retry_pending_push(record: dict) -> bool:
        """重试单条待推送记录，成功返回 True"""
        from src.agent.tools import push_branch, create_pull_request, set_tool_context
        from src.store.repair_store import get_repair_store, RepairLifecycleStatus
        from git import Repo

        branch_name = record["branch_name"]
        repo_path = record["repo_path"]

        # 检查仓库目录是否存在
        if not os.path.isdir(os.path.join(repo_path, ".git")):
            log.warning(f"仓库目录不存在: {repo_path}，跳过推送 {branch_name}")
            return False

        try:
            repo = Repo(repo_path)

            # 关键：checkout 到 autofix 分支，否则 push_branch 会推送 HEAD（可能是 main）
            try:
                repo.git.checkout(branch_name)
            except Exception as e:
                log.warning(f"checkout 到 {branch_name} 失败: {e}，跳过")
                return False

            set_tool_context({
                "repo": repo,
                "repo_path": repo_path,
                "github_token": settings.github.token,
            })

            push_result = push_branch.invoke({"branch_name": branch_name})
            if push_result != "Success":
                log.warning(f"重试推送仍失败: {branch_name} — {push_result}")
                return False

            pr_url = create_pull_request.invoke({
                "repo_full_name": record["repo_full_name"],
                "head_branch": branch_name,
                "base_branch": record["base_branch"],
                "title": record["pr_title"],
                "body": record["pr_body"],
            })

            if pr_url.startswith("Error:"):
                log.error(f"重试创建PR失败: {branch_name} — {pr_url}")
                return False

            # 更新 repair_records 状态
            fp = record.get("fingerprint", "")
            if fp:
                repair_store = get_repair_store()
                repair_store.upsert(fp, RepairLifecycleStatus.PENDING_DEPLOY.value,
                                    fix_pr_url=pr_url)

            log.info(f"重试推送成功: {branch_name} → {pr_url}")

            # 发飞书通知
            if settings.lark.enabled and settings.lark.notify_users:
                from src.notify.lark_notify import send_markdown_message
                md = (
                    f"**遗留推送重试成功**\n\n"
                    f"- 服务: {record.get('service', 'N/A')}\n"
                    f"- 分支: `{branch_name}`\n"
                    f"- PR: [查看PR]({pr_url})\n"
                    f"- 修复描述: {record.get('fix_description', '')[:100]}"
                )
                for user_id in settings.lark.notify_users:
                    await send_markdown_message(user_id, md, title="SpiderClaw 推送恢复")

            return True

        except Exception as e:
            log.error(f"重试推送异常: {branch_name} — {e}", exc_info=True)
            return False

    async def recover_pending_pushes():
        """启动时重试推送失败的修复分支"""
        push_store = get_pending_push_store()
        pending = push_store.get_all()
        if not pending:
            return

        log.info(f"发现 {len(pending)} 个待推送的修复分支，开始重试...")

        # 发飞书通知：有积压推送
        if settings.lark.enabled and settings.lark.notify_users:
            from src.notify.lark_notify import send_markdown_message
            branch_list = "\n".join(f"- `{r['branch_name']}` ({r.get('service', 'N/A')})" for r in pending)
            md = (
                f"**发现 {len(pending)} 个待推送的修复分支，正在重试...**\n\n"
                f"{branch_list}"
            )
            for user_id in settings.lark.notify_users:
                await send_markdown_message(user_id, md, title="SpiderClaw 推送恢复")

        for record in pending:
            success = await _retry_pending_push(record)
            if success:
                push_store.delete(record["id"])
            else:
                push_store.increment_retry(record["id"])

    async def _pending_push_timer():
        """每 10 分钟检查积压的待推送记录并自动重试"""
        TIMER_INTERVAL = 600  # 10 分钟
        while True:
            await asyncio.sleep(TIMER_INTERVAL)
            try:
                push_store = get_pending_push_store()
                pending = push_store.get_all()
                if not pending:
                    continue

                log.info(f"[定时重试] 发现 {len(pending)} 个待推送记录，开始重试...")
                for record in pending:
                    success = await _retry_pending_push(record)
                    if success:
                        push_store.delete(record["id"])
                    else:
                        push_store.increment_retry(record["id"])
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.error(f"[定时重试] 异常: {e}", exc_info=True)

    async def _run():
        def _task_done_callback(task: asyncio.Task):
            """任务结束回调：记录异常或意外退出"""
            if task.cancelled():
                return
            exc = task.exception()
            if exc:
                log.error(f"任务 [{task.get_name()}] 异常退出: {exc}", exc_info=exc)

        tasks = [
            asyncio.create_task(monitor.start(), name="monitor"),
        ]
        if orchestrator:
            tasks.append(asyncio.create_task(event_consumer(), name="event_consumer"))
            tasks.append(asyncio.create_task(_pending_push_timer(), name="pending_push_timer"))
        # 始终启动审批事件监听（WebSocket 客户端），即使没有待处理事件
        approval_task = asyncio.create_task(approval_event_listener(), name="approval_listener")
        approval_task.add_done_callback(_task_done_callback)
        tasks.append(approval_task)
        # 启动时预同步所有注册服务的仓库（确保本地有可用代码）
        if orchestrator:
            registry = get_service_registry()
            await pre_sync_repos(registry.all())

        # 启动时重试推送失败的修复分支（异步执行，不阻塞启动）
        if orchestrator:
            asyncio.create_task(recover_pending_pushes(), name="recover_pending_pushes")

        # 启动时恢复上次中断的未处理事件（在 WebSocket 启动之后）
        await recover_pending_events()
        await asyncio.gather(*tasks, return_exceptions=True)

    try:
        audit_logger.log_event("milestone", node="service_start")
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("Webhook server stopped by user")
