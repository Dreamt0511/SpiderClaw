# GitHub Webhook 服务设计文档

## 一、项目背景
本服务是事件驱动的自动诊断与修复系统（AutoFix Agent）的监控层组件，负责接收GitHub发送的Webhook事件，验证签名有效性，转换为内部统一事件格式，发送到事件总线供后续Agent处理。

## 二、整体架构
```
┌─────────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│   GitHub        │────▶│  Webhook Service    │────▶│   Event Bus     │
│   Webhook       │     │  (FastAPI :8000)    │     │  (asyncio.Queue)│
└─────────────────┘     └─────────────────────┘     └─────────────────┘
                               ▲
                               │
                               ▼
                        ┌─────────────┐   ┌─────────────┐
                        │  Config     │   │  Logging    │
                        │  (CLI/Env/  │   │  System     │
                        │   YAML)     │   │             │
                        └─────────────┘   └─────────────┘
```

## 三、核心功能
1. **事件接收**：支持GitHub Workflow runs、Pull requests、Check runs三种事件
2. **签名验证**：验证GitHub Webhook签名，防止伪造请求
3. **事件转换**：将GitHub事件格式转换为内部统一事件格式
4. **幂等去重**：基于事件ID去重，避免重复处理
5. **反压保护**：事件队列满时返回503，由GitHub重试
6. **健康检查**：提供运维监控端点
7. **优雅关闭**：确保进程退出时不丢失正在处理的事件
8. **结构化日志**：完整记录事件处理全流程

## 四、API接口设计

### 1. Webhook接收端点
- **路径**：`POST /webhook/github`
- **请求头**：
  - `X-GitHub-Event`: GitHub事件类型
  - `X-GitHub-Delivery`: 事件唯一ID
  - `X-Hub-Signature-256`: 签名头，格式为`sha256=HMAC-SHA256(secret, payload)`
  - `Content-Type`: `application/json`
- **响应**：
  - `200 OK`: 事件处理成功或重复事件
  - `403 Forbidden`: 签名验证失败
  - `400 Bad Request`: 无效的事件格式或不支持的事件类型
  - `503 Service Unavailable`: 事件队列已满，稍后重试

### 2. 健康检查端点
- **路径**：`GET /health`
- **响应**：
  ```json
  {
    "status": "ok",
    "queue_size": 12,
    "uptime_seconds": 3600,
    "processed_events": 156
  }
  ```

## 五、事件格式定义

### 内部统一事件格式
```python
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class GitHubEvent(BaseModel):
    """内部统一GitHub事件格式"""
    event_id: str                    # X-GitHub-Delivery
    event_type: str                  # X-GitHub-Event
    action: str                      # 事件动作（如completed、opened等）
    payload: dict                    # 原始事件payload
    timestamp: datetime              # 接收时间
    repository: str                  # 仓库全名（owner/repo）
    signature_valid: bool            # 签名是否验证通过
    
    # 衍生字段，Agent直接使用
    clone_url: str = ""              # 仓库克隆地址
    branch: str = ""                 # 分支名
    pr_number: Optional[int] = None  # PR编号
    logs_url: str = ""               # CI日志下载链接
    conclusion: str = ""             # 执行结果（success/failure等）
```

### 支持的GitHub事件类型
| 事件类型 | 触发场景 | 处理逻辑 |
|---------|---------|---------|
| `workflow_run` | CI流水线运行结束 | 过滤失败的运行，提取分支、日志URL等信息 |
| `pull_request` | PR创建/更新/关闭 | 提取PR编号、分支、仓库信息 |
| `check_run` | CI检查运行结束 | 过滤失败的检查，提取具体失败项、日志URL |

## 六、安全设计

### 1. 签名验证
- 算法：`HMAC-SHA256`，使用配置的密钥对请求体计算摘要
- 密钥优先级：CLI参数 > 环境变量 > 配置文件
  - CLI参数：`--webhook-secret <secret>`
  - 环境变量：`GITHUB_WEBHOOK_SECRET`
  - 配置文件：`webhook.github.secret`
- 验证失败直接返回403，不进行后续处理

### 2. 输入验证
- 严格验证事件格式，不符合格式的请求直接返回400
- 只允许配置的事件类型进入处理流程
- 对payload进行大小限制（默认10MB），防止恶意大请求

## 七、可靠性设计

### 1. 反压机制
```python
class EventBus:
    def __init__(self, maxsize: int = 1000):
        self.queue = asyncio.Queue(maxsize=maxsize)
        self.processed_count = 0
    
    async def publish(self, event) -> bool:
        """非阻塞投递事件，满则返回False"""
        try:
            self.queue.put_nowait(event)
            self.processed_count += 1
            return True
        except asyncio.QueueFull:
            logger.warning(f"Event queue full, dropped event: {event.event_id}")
            return False
```
- 队列默认容量：1000，可通过配置修改
- 队列满时返回503给GitHub，由GitHub负责重试（默认最多15次，指数退避）

### 2. 幂等去重
```python
class EventBus:
    def __init__(self, max_processed_ids: int = 10000):
        self.processed_ids = set()
        self.processed_lock = asyncio.Lock()
        self.max_processed_ids = max_processed_ids
    
    async def is_duplicate(self, event_id: str) -> bool:
        """检查事件是否已处理，非重复事件加入已处理集合"""
        async with self.processed_lock:
            if event_id in self.processed_ids:
                return True
            # 简单的LRU策略：超过容量时清空一半
            if len(self.processed_ids) >= self.max_processed_ids:
                remove_count = self.max_processed_ids // 2
                self.processed_ids = set(list(self.processed_ids)[remove_count:])
            self.processed_ids.add(event_id)
            return False
```
- 基于`X-GitHub-Delivery`去重
- 已处理ID集合默认最大容量：10000
- 超过容量时采用简单LRU策略，保留最近一半的ID
- 重复事件直接返回200，不进入处理流程

### 3. 优雅关闭
```python
async def shutdown(signal: signal.Signals, server: fastapi.Server, event_bus: EventBus):
    """优雅关闭流程"""
    logger.info(f"Received exit signal {signal.name}...")
    
    # 1. 停止接收新请求
    server.should_exit = True
    
    # 2. 等待队列排空（最多等待30秒）
    drain_timeout = 30
    start_time = time.time()
    while not event_bus.queue.empty() and (time.time() - start_time) < drain_timeout:
        await asyncio.sleep(0.1)
    
    if not event_bus.queue.empty():
        logger.warning(f"Force shutdown with {event_bus.queue.qsize()} events unprocessed")
    
    # 3. 关闭其他资源
    logger.info("Shutdown complete")
```
- 捕获SIGTERM/SIGINT信号
- 先停止接收新请求，再处理队列中已有事件
- 最多等待30秒，超时强制退出

## 八、可观测性设计

### 1. 日志系统
- **日志级别**：DEBUG/INFO/WARNING/ERROR/CRITICAL，可配置
- **输出格式**：结构化JSON，便于日志收集分析
- **日志分类**：
  - 访问日志：记录所有HTTP请求信息
  - 操作日志：记录事件处理全流程
  - 错误日志：记录异常情况
- **链路追踪**：同一个事件的所有日志都带有`event_id`字段
- **存储**：按天滚动存储到`logs/webhook/`目录，默认保留30天

### 2. 指标收集
- 处理的事件总数（按事件类型、结果分类）
- 队列当前大小
- 平均处理延迟
- 签名失败次数
- 队列满丢弃次数
- 重复事件次数

## 九、配置说明

### YAML配置示例（src/config/agent-config.yaml）
```yaml
webhook:
  github:
    # 基础配置
    secret: "your-webhook-secret-here"
    host: "0.0.0.0"
    port: 8000
    reload: false  # 开发环境热重载
    
    # 事件配置
    allowed_events: ["workflow_run", "pull_request", "check_run"]
    max_payload_size: "10MB"
    
    # 可靠性配置
    event_queue_maxsize: 1000
    max_processed_ids: 10000
    shutdown_timeout: 30
    
    # 日志配置
    log_level: "INFO"
    log_dir: "logs/webhook"
    log_retention_days: 30
```

### 环境变量
```bash
GITHUB_WEBHOOK_SECRET="your-secret"
GITHUB_WEBHOOK_PORT=8000
GITHUB_WEBHOOK_HOST="0.0.0.0"
```

## 十、CLI命令设计
```bash
# 启动Webhook服务
spiderclaw webhook start [OPTIONS]

Options:
  --host TEXT              监听主机地址 (默认: 0.0.0.0)
  --port INTEGER           监听端口 (默认: 8000)
  --secret TEXT            GitHub Webhook密钥
  --config PATH            配置文件路径 (默认: src/config/agent-config.yaml)
  --log-level TEXT         日志级别 (DEBUG/INFO/WARNING/ERROR)
  --reload                 启用热重载（开发环境）
  --help                   显示帮助信息
```

## 十一、部署说明
1. 生成Webhook密钥：`openssl rand -hex 32`
2. 在GitHub仓库设置中配置Webhook：
   - Payload URL: `https://your-domain.com/webhook/github`
   - Content type: `application/json`
   - Secret: 生成的密钥
   - 选择事件：Workflow runs、Pull requests、Check runs
3. 配置环境变量或配置文件中的密钥
4. 启动服务：`spiderclaw webhook start`
5. 配置反向代理（如Nginx）对外提供服务

## 十二、后续优化方向
1. 支持Webhook事件持久化，避免服务重启丢失未处理事件
2. 接入Prometheus指标暴露
3. 增加限流机制，防止恶意请求洪水
4. 支持多个GitHub App/密钥，同时监听多个组织的事件
5. 增加事件过滤规则配置，灵活控制哪些事件需要处理
