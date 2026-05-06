# AutoFix Agent 远程日志监控方案设计


使用方式：

  # 1. 在 Agent 端注册服务
  vim src/config/services.yaml  # 添加服务配置

  # 2. 启动服务
  spiderclaw --no-dashboard --secret <secret> --port 8000

  # 3. 生成采集脚本并部署到业务服务器
  spiderclaw init-sidecar -o ./sidecar -n "order-service" --agent-url http://agent-host:8000/webhook/log

  # 4. 在业务服务器启动采集
  nohup bash /opt/agent-sidecar/collector.sh > /dev/null 2>&1 &

## Context

当前 SpiderClaw 仅支持 GitHub CI 事件触发修复（`/webhook/github`）。线上业务服务器的运行时错误无法自动捕获和修复。本方案扩展系统以支持远程日志零侵入监控：在业务服务器上部署轻量采集脚本，tail 日志文件，检测到 Traceback 后 POST 到 Agent 服务，复用现有修复流水线。

**核心原则**：业务服务零改动、零重启。采集脚本仅读取日志文件。

---

## 设计决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 集成策略 | 扩展现有编排器 | 最大复用现有 fix/review/test/pr 流程 |
| 图结构 | 双入口（方案 B） | 职责分离清晰，各自独立演进 |
| 版本管理 | 三级降级 | 兼顾精确性和鲁棒性 |
| 采集脚本 | 项目内维护 | 降低用户使用门槛 |
| 路径映射 | 仅 Agent 侧配置 | 简化采集脚本，集中管理 |

---

## 一、事件模型扩展

### 新增 `RuntimeLogEvent`（修改 `src/bus/schemas.py`）

```python
class RuntimeLogEvent(BaseEvent):
    """运行时日志事件 — 由 /webhook/log 端点创建"""
    log: str                    # 原始日志内容（含 Traceback）
    service: str                # 服务名称（如 "order-service"）
    version: str = ""           # Git commit SHA 或镜像 tag
    hostname: str = ""          # 来源主机名

    # 以下字段由 Agent 端从 services.yaml 查到后填充
    repo_url: str = ""
    repo_local_path: str = ""
    branch: str = ""
    path_mapping: dict[str, str] = {}

    # 兼容 GitHubEvent 接口（下游节点通过 event.repository 等访问）
    repository: str = ""        # 用 service 值填充
    clone_url: str = ""         # 用 repo_url 填充
    action: str = ""
    signature_valid: bool = True
```

**兼容性说明**：`collect_runtime_context` 返回的状态结构与 `collect_context` 完全一致（ci_logs, repo_path, error_locations, target_files, original_codes 等），下游 `fix_agent` → `review` → `test` → `create_pr` 节点无需任何修改。`create_pr` 节点通过 `state["event"]` 访问的字段（repository, branch 等）在 `RuntimeLogEvent` 中均有对应。

---

## 二、服务注册与配置

### 新增配置模型（修改 `src/config/settings.py`）

```python
class ServiceConfig(BaseModel):
    """单个服务的配置"""
    name: str                         # 与采集脚本 SERVICE_NAME 对应
    repo_url: str                     # Git 仓库 URL
    repo_local_path: str = ""         # Agent 本地 clone 路径（空则临时目录）
    git_branch: str = "main"
    path_mapping: dict[str, str] = {} # {"/app/": "src/"}

class RateLimitConfig(BaseModel):
    max_fixes_per_minute: int = 3
    max_fixes_per_hour: int = 20
    dedup_window_seconds: int = 300
    aggregate_window_seconds: int = 60

class ServicesConfig(BaseModel):
    services: list[ServiceConfig] = []
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
```

### 新增 `services.yaml` 配置文件

```yaml
services:
  - name: "order-service"
    repo_url: "https://github.com/myteam/order-service.git"
    repo_local_path: "/data/repos/order-service"
    git_branch: "main"
    path_mapping:
      "/app/": "src/"

rate_limit:
  max_fixes_per_minute: 3
  max_fixes_per_hour: 20
```

---

## 三、Webhook 端点

### 新增 `/webhook/log`（修改 `src/monitor/webhook_server.py`）

接收格式：
```json
{
    "log": "Traceback ...\nZeroDivisionError: division by zero",
    "service": "order-service",
    "version": "a1b2c3d4",
    "hostname": "prod-server-01"
}
```

处理流程：
1. 从 services.yaml 查找服务配置
2. 创建 `RuntimeLogEvent`（填充 repo_url、path_mapping 等）
3. 发布到事件总线
4. 返回 `{"status": "accepted"}`

### 事件消费扩展

在 `event_consumer` 中增加 `RuntimeLogEvent` 分支：
```python
if isinstance(event, RuntimeLogEvent):
    asyncio.create_task(orchestrator.run(event))
```

---

## 四、编排器改造

### 图结构变更（修改 `src/agent/orchestrator.py`）

```
                        ┌─ GitHubEvent ─→ collect_context ─────────┐
                        │                                          │
START → route_by_event ─┤                                          ├→ fix_agent → validation_gate → review → test → create_pr
                        │                                          │
                        └─ RuntimeLogEvent → collect_runtime_context ┘
```

### 新增 `_route_by_event` 入口路由

```python
def _route_by_event(self, state: RepairState) -> str:
    event = state["event"]
    if isinstance(event, RuntimeLogEvent):
        return "collect_runtime_context"
    return "collect_context"
```

图构建变更：
```python
# 原来：workflow.add_edge(START, "collect_context")
# 改为：
workflow.add_conditional_edges(START, self._route_by_event, ["collect_context", "collect_runtime_context"])

# collect_runtime_context 使用与 collect_context 相同的后路由逻辑
workflow.add_conditional_edges(
    "collect_runtime_context",
    self._route_after_context,  # 复用同一个路由函数
    ["fix_agent", "handle_failure", END],
)
```

### 新增 `collect_runtime_context` 节点

核心步骤：
1. **解析 Traceback**：复用 `parse_python_errors` 工具
2. **路径映射**：调用 `apply_path_mapping()` 转换路径
3. **版本管理**：调用 `ensure_repo_with_version()` 三级降级
4. **读取源码**：复用 `read_file` + `code_context.py`
5. **返回状态**：与 `collect_context` 相同结构，下游节点无需修改

### `create_pr` 节点适配

- PR 标题格式：`[SpiderClaw: fix] {error_type} @{service}: {description}`
- PR 描述中标注来源（远程日志 / CI）和版本信息
- 降级时标注风险

### `run()` 入口适配

- 支持 `RuntimeLogEvent` 类型参数
- 初始状态中 `ci_logs` 用 `event.log` 填充

---

## 五、新增工具模块

### 5.1 路径映射（新文件 `src/utils/path_mapping.py`）

```python
def apply_path_mapping(runtime_path: str, mapping: dict[str, str]) -> str:
    """将运行时路径转换为仓库相对路径
    
    规则：按 mapping key 长度降序匹配（最长前缀优先）
    示例："/app/services/order.py" + {"/app/": "src/"} → "src/services/order.py"
    """
```

### 5.2 版本管理（新文件 `src/utils/version_manager.py`）

```python
async def ensure_repo_with_version(
    repo_url: str,
    local_path: str,
    version: str,
    branch: str,
) -> tuple[str, bool]:
    """确保仓库可用并 checkout 到正确版本
    
    三级降级：
    1. version 有值 + local_path 存在 → fetch + checkout 精确 commit
    2. local_path 存在 → pull 最新 + checkout branch
    3. 都没有 → clone 最新 + 标记风险（返回 degraded=True）
    
    Returns:
        (repo_path, degraded) 元组
    """
```

### 5.3 限流器（新文件 `src/utils/rate_limiter.py`）

```python
class ServiceRateLimiter:
    """基于服务名的滑动窗口限流"""
    
    def __init__(self, max_per_minute=3, max_per_hour=20):
        ...
    
    async def check(self, service: str) -> bool:
        """返回 True 表示允许，False 表示限流"""
    
    async def record(self, service: str):
        """记录一次修复"""
```

集成位置：`collect_runtime_context` 入口处检查，超过阈值升级为"请人工检查"通知。

---

## 六、采集脚本

### 6.1 `scripts/collector.sh`

部署在业务服务器，核心逻辑：
- `tail -F $LOG_PATH` 实时监控日志
- 检测 Error/Traceback/Exception 关键词
- 错误哈希去重（File+行号+错误类型 → MD5 前12位）
- 批量发送（10秒间隔或 50 行）
- HTTP 429 指数退避（1s→2s→...→300s 上限）

### 6.2 `scripts/agent-mapping.conf`

```bash
SERVICE_NAME="my-service"
SERVICE_VERSION="unknown"
LOG_PATH="/var/log/app/app.log"
AGENT_URL="http://agent-host:8000/webhook/log"
```

---

## 七、CLI 命令

### 新增 `spiderclaw init-sidecar`（修改 `src/cli/app.py`）

```bash
spiderclaw init-sidecar -o ./sidecar
# 生成：
#   ./sidecar/collector.sh
#   ./sidecar/agent-mapping.conf
```

---

## 文件变更清单

| 操作 | 文件路径 | 变更内容 |
|------|----------|----------|
| 修改 | `src/bus/schemas.py` | 新增 `RuntimeLogEvent` |
| 修改 | `src/config/settings.py` | 新增 `ServiceConfig`, `ServicesConfig`, `RateLimitConfig` |
| 修改 | `src/monitor/webhook_server.py` | 新增 `/webhook/log` 端点 + 事件消费扩展 |
| 修改 | `src/agent/orchestrator.py` | 新增双入口路由 + `collect_runtime_context` 节点 + `create_pr` 适配 |
| 修改 | `src/cli/app.py` | 新增 `init-sidecar` 命令 |
| 新增 | `src/utils/path_mapping.py` | 路径映射逻辑 |
| 新增 | `src/utils/version_manager.py` | 仓库版本管理（三级降级） |
| 新增 | `src/utils/rate_limiter.py` | 事件级限流 |
| 新增 | `scripts/collector.sh` | 采集脚本模板 |
| 新增 | `scripts/agent-mapping.conf` | 服务器端配置模板 |

---

## 实施优先级

1. **事件模型 + 配置系统** → `schemas.py`, `settings.py`, `services.yaml`
2. **路径映射 + 版本管理** → `path_mapping.py`, `version_manager.py`
3. **编排器改造** → `orchestrator.py`（双入口 + `collect_runtime_context`）
4. **Webhook 端点** → `webhook_server.py`（`/webhook/log`）
5. **限流器** → `rate_limiter.py`
6. **采集脚本** → `scripts/collector.sh`, `scripts/agent-mapping.conf`
7. **CLI 命令** → `init-sidecar`
8. **测试** → 单元测试 + 集成测试

---

## 验证方案

1. **单元测试**：
   - `apply_path_mapping()` 各种映射规则
   - `ensure_repo_with_version()` 三级降级路径
   - `parse_python_errors()` 对运行时日志的解析
   - `ServiceRateLimiter` 滑动窗口行为

2. **集成测试**：
   - 模拟 POST → `/webhook/log` → `RuntimeLogEvent` → 事件总线
   - `collect_runtime_context` 节点完整流程

3. **端到端测试**：
   - 在测试仓库注入运行时错误
   - 采集脚本 → Agent → 修复 → PR 完整链路

4. **采集脚本测试**：
   - 本地模拟日志输出 → 验证去重、批量、退避
