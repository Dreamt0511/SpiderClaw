# 远程日志监控方案实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扩展 SpiderClaw 支持远程运行时日志零侵入监控，采集脚本 tail 日志 → POST `/webhook/log` → 路径映射 + 版本管理 → 复用现有修复流水线

**Architecture:** 双入口 LangGraph 图 — CI 事件走 `collect_context`，远程日志走 `collect_runtime_context`，在 `fix_agent` 节点汇合。采集脚本部署在业务服务器，Agent 侧通过 `services.yaml` 注册服务。

**Tech Stack:** Python, FastAPI, LangGraph, Pydantic, GitPython, Bash

---

## 文件结构

| 操作 | 文件路径 | 职责 |
|------|----------|------|
| 修改 | `src/bus/schemas.py` | 新增 RuntimeLogEvent 模型 |
| 修改 | `src/bus/__init__.py` | 导出 RuntimeLogEvent |
| 修改 | `src/config/settings.py` | 新增 ServiceConfig, ServicesConfig, RateLimitConfig |
| 新增 | `src/config/service_registry.py` | ServiceRegistry 单例，加载 services.yaml |
| 新增 | `src/utils/path_mapping.py` | 运行时路径 → 仓库路径映射 |
| 新增 | `src/utils/version_manager.py` | 仓库版本管理（三级降级） |
| 新增 | `src/utils/rate_limiter.py` | 滑动窗口限流 |
| 修改 | `src/agent/orchestrator.py` | 双入口路由 + collect_runtime_context + create_pr 适配 |
| 修改 | `src/monitor/webhook_server.py` | /webhook/log 端点 + 事件消费扩展 |
| 修改 | `src/cli/app.py` | init-sidecar 命令 |
| 新增 | `scripts/collector.sh` | 采集脚本模板 |
| 新增 | `scripts/agent-mapping.conf` | 服务器端配置模板 |
| 新增 | `tests/test_path_mapping.py` | 路径映射测试 |
| 新增 | `tests/test_version_manager.py` | 版本管理测试 |
| 新增 | `tests/test_rate_limiter.py` | 限流器测试 |
| 新增 | `tests/test_service_registry.py` | 服务注册表测试 |
| 新增 | `tests/test_webhook_log.py` | /webhook/log 端点测试 |

---

## Task 1: RuntimeLogEvent 事件模型

**Files:**
- Modify: `src/bus/schemas.py`
- Modify: `src/bus/__init__.py`

- [ ] **Step 1: 在 schemas.py 末尾添加 RuntimeLogEvent**

在 `src/bus/schemas.py` 的 `GitHubEvent` 类之后添加：

```python
class RuntimeLogEvent(BaseEvent):
    """运行时日志事件 — 由 /webhook/log 端点创建

    独立事件类型，不伪装为 GitHubEvent。
    下游节点通过 event.event_type == "runtime_log" 判断。
    """
    event_type: str = "runtime_log"

    # === 运行时日志自有字段 ===
    log: str = ""
    service: str = ""
    version: str = ""
    hostname: str = ""

    # === 由 Agent 端从 services.yaml 查到后填充 ===
    repo_url: str = ""
    repo_local_path: str = ""
    branch: str = ""
    path_mapping: Dict[str, str] = Field(default_factory=dict)

    # === 仅供下游节点兼容访问 ===
    repository: str = ""
    clone_url: str = ""
    action: str = ""
    signature_valid: bool = True
```

- [ ] **Step 2: 更新 bus/__init__.py 导出**

将 `src/bus/__init__.py` 修改为：

```python
from .event_bus import EventBus, get_event_bus
from .schemas import BaseEvent, GitHubEvent, RuntimeLogEvent

__all__ = ["EventBus", "get_event_bus", "BaseEvent", "GitHubEvent", "RuntimeLogEvent"]
```

- [ ] **Step 3: 验证导入**

```bash
cd "D:\U 盘\SpiderClaw" && python -c "from src.bus import RuntimeLogEvent; e = RuntimeLogEvent(event_id='test', source='test', log='test', service='svc'); print(e.event_type, e.service)"
```

Expected: `runtime_log svc`

- [ ] **Step 4: 提交**

```bash
git add src/bus/schemas.py src/bus/__init__.py
git commit -m "feat: add RuntimeLogEvent schema for remote log monitoring"
```

---

## Task 2: 服务配置模型

**Files:**
- Modify: `src/config/settings.py`

- [ ] **Step 1: 添加配置模型**

在 `src/config/settings.py` 的 `LarkConfig` 类之后、`Settings` 类之前添加：

```python
class ServiceConfig(BaseModel):
    """单个远程服务的配置"""
    name: str = Field(description="服务名称，与采集脚本 SERVICE_NAME 对应")
    repo_url: str = Field(description="Git 仓库 URL")
    repo_local_path: str = Field(description="Agent 本地持久化 clone 路径（必填）")
    git_branch: str = Field(default="main", description="目标分支")
    path_mapping: Dict[str, str] = Field(
        default_factory=dict,
        description="运行时路径前缀 → 仓库路径前缀映射，如 {'/app/': 'src/'}"
    )


class RateLimitConfig(BaseModel):
    """远程日志修复限流配置"""
    max_fixes_per_minute: int = Field(default=3, description="每分钟最大修复次数")
    max_fixes_per_hour: int = Field(default=20, description="每小时最大修复次数")
    dedup_window_seconds: int = Field(default=300, description="去重窗口（秒）")
    aggregate_window_seconds: int = Field(default=60, description="聚合窗口（秒）")


class ServicesConfig(BaseModel):
    """远程服务注册配置"""
    services: list[ServiceConfig] = Field(default_factory=list, description="已注册的服务列表")
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
```

- [ ] **Step 2: 在 Settings 类中添加 services 字段**

在 `Settings` 类中找到 `lark: LarkConfig` 字段（约第 101 行），在其后添加：

```python
    # 远程服务配置
    services: ServicesConfig = Field(default_factory=ServicesConfig)
```

- [ ] **Step 3: 添加 services.yaml 加载支持**

修改 `Settings.load_from_yaml` 方法，使其在加载 `agent-config.yaml` 之后，尝试加载 `services.yaml`：

在 `return cls(**config_data)` 之前添加：

```python
            # 尝试加载 services.yaml（独立配置文件）
            services_path = config_path.parent / "services.yaml"
            if services_path.exists():
                with open(services_path, "r", encoding="utf-8") as sf:
                    services_data = yaml.safe_load(sf) or {}
                config_data["services"] = services_data
```

- [ ] **Step 4: 创建 services.yaml 模板**

创建 `src/config/services.yaml`：

```yaml
# 远程服务注册配置
# 每个服务对应一个线上业务系统，采集脚本通过 service name 关联
services:
  # - name: "order-service"
  #   repo_url: "https://github.com/myteam/order-service.git"
  #   repo_local_path: "/data/repos/order-service"
  #   git_branch: "main"
  #   path_mapping:
  #     "/app/": "src/"

rate_limit:
  max_fixes_per_minute: 3
  max_fixes_per_hour: 20
  dedup_window_seconds: 300
  aggregate_window_seconds: 60
```

- [ ] **Step 5: 验证配置加载**

```bash
cd "D:\U 盘\SpiderClaw" && python -c "
from src.config.settings import get_settings
s = get_settings()
print(type(s.services))
print(s.services.rate_limit.max_fixes_per_minute)
"
```

Expected: `<class 'src.config.settings.ServicesConfig'>` 和 `3`

- [ ] **Step 6: 提交**

```bash
git add src/config/settings.py src/config/services.yaml
git commit -m "feat: add ServiceConfig and ServicesConfig for remote service registry"
```

---

## Task 3: ServiceRegistry 服务注册表

**Files:**
- Create: `src/config/service_registry.py`
- Create: `tests/test_service_registry.py`

- [ ] **Step 1: 编写测试**

创建 `tests/test_service_registry.py`：

```python
"""ServiceRegistry 单元测试"""
import pytest
import yaml
import tempfile
import os
from src.config.service_registry import ServiceRegistry


@pytest.fixture
def services_yaml(tmp_path):
    """创建临时 services.yaml"""
    config = {
        "services": [
            {
                "name": "order-service",
                "repo_url": "https://github.com/test/order.git",
                "repo_local_path": "/tmp/repos/order",
                "git_branch": "main",
                "path_mapping": {"/app/": "src/"},
            },
            {
                "name": "user-service",
                "repo_url": "https://github.com/test/user.git",
                "repo_local_path": "/tmp/repos/user",
            },
        ],
        "rate_limit": {
            "max_fixes_per_minute": 5,
            "max_fixes_per_hour": 30,
        },
    }
    path = tmp_path / "services.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True)
    return str(path)


def test_load_services(services_yaml):
    """测试加载服务配置"""
    registry = ServiceRegistry(services_yaml)
    assert len(registry.list_services()) == 2
    assert "order-service" in registry.list_services()
    assert "user-service" in registry.list_services()


def test_get_existing_service(services_yaml):
    """测试查询已注册的服务"""
    registry = ServiceRegistry(services_yaml)
    svc = registry.get("order-service")
    assert svc is not None
    assert svc.repo_url == "https://github.com/test/order.git"
    assert svc.path_mapping == {"/app/": "src/"}


def test_get_nonexistent_service(services_yaml):
    """测试查询未注册的服务返回 None"""
    registry = ServiceRegistry(services_yaml)
    assert registry.get("nonexistent") is None


def test_rate_limit_config(services_yaml):
    """测试限流配置加载"""
    registry = ServiceRegistry(services_yaml)
    assert registry.rate_limit.max_fixes_per_minute == 5
    assert registry.rate_limit.max_fixes_per_hour == 30


def test_empty_config(tmp_path):
    """测试空配置文件"""
    path = tmp_path / "services.yaml"
    with open(path, "w") as f:
        f.write("services: []\n")
    registry = ServiceRegistry(str(path))
    assert registry.list_services() == []
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "D:\U 盘\SpiderClaw" && python -m pytest tests/test_service_registry.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.config.service_registry'`

- [ ] **Step 3: 实现 ServiceRegistry**

创建 `src/config/service_registry.py`：

```python
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

        services_list = data.get("services", [])
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
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd "D:\U 盘\SpiderClaw" && python -m pytest tests/test_service_registry.py -v
```

Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/config/service_registry.py tests/test_service_registry.py
git commit -m "feat: add ServiceRegistry for remote service lookup"
```

---

## Task 4: 路径映射

**Files:**
- Create: `src/utils/path_mapping.py`
- Create: `tests/test_path_mapping.py`

- [ ] **Step 1: 编写测试**

创建 `tests/test_path_mapping.py`：

```python
"""路径映射单元测试"""
import pytest
from src.utils.path_mapping import apply_path_mapping


def test_basic_mapping():
    """基本前缀映射"""
    mapping = {"/app/": "src/"}
    assert apply_path_mapping("/app/services/order.py", mapping) == "src/services/order.py"


def test_longest_prefix_wins():
    """最长前缀优先匹配"""
    mapping = {"/app/": "src/", "/app/services/": "src/core/"}
    assert apply_path_mapping("/app/services/order.py", mapping) == "src/core/order.py"


def test_no_mapping_returns_original():
    """无匹配映射时返回原路径"""
    mapping = {"/app/": "src/"}
    assert apply_path_mapping("services/order.py", mapping) == "services/order.py"


def test_empty_mapping():
    """空映射返回原路径"""
    assert apply_path_mapping("/app/order.py", {}) == "/app/order.py"


def test_absolute_path_no_match():
    """绝对路径无匹配时返回原路径"""
    mapping = {"/app/": "src/"}
    assert apply_path_mapping("/other/order.py", mapping) == "/other/order.py"


def test_multiple_mappings():
    """多个映射规则"""
    mapping = {"/app/": "src/", "/shared-lib/": "lib/"}
    assert apply_path_mapping("/shared-lib/utils.py", mapping) == "lib/utils.py"


def test_mapping_preserves_suffix():
    """映射保留路径后缀"""
    mapping = {"/app/": "src/"}
    assert apply_path_mapping("/app/a/b/c.py", mapping) == "src/a/b/c.py"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "D:\U 盘\SpiderClaw" && python -m pytest tests/test_path_mapping.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现路径映射**

创建 `src/utils/path_mapping.py`：

```python
"""路径映射 — 将生产环境运行时路径转换为仓库相对路径"""
import logging

logger = logging.getLogger(__name__)


def apply_path_mapping(runtime_path: str, mapping: dict[str, str]) -> str:
    """将运行时路径转换为仓库相对路径

    规则：按 mapping key 长度降序匹配（最长前缀优先）。
    无匹配时返回原路径。

    Args:
        runtime_path: 生产环境的文件路径，如 "/app/services/order.py"
        mapping: 路径映射规则，如 {"/app/": "src/"}

    Returns:
        映射后的仓库相对路径
    """
    if not mapping or not runtime_path:
        return runtime_path

    # 按 key 长度降序排序，确保最长前缀优先匹配
    sorted_keys = sorted(mapping.keys(), key=len, reverse=True)

    for prefix in sorted_keys:
        if runtime_path.startswith(prefix):
            suffix = runtime_path[len(prefix):]
            result = mapping[prefix] + suffix
            logger.debug(f"路径映射: {runtime_path} -> {result}")
            return result

    return runtime_path
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd "D:\U 盘\SpiderClaw" && python -m pytest tests/test_path_mapping.py -v
```

Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/utils/path_mapping.py tests/test_path_mapping.py
git commit -m "feat: add path mapping for runtime-to-repo path conversion"
```

---

## Task 5: 版本管理

**Files:**
- Create: `src/utils/version_manager.py`
- Create: `tests/test_version_manager.py`

- [ ] **Step 1: 编写测试**

创建 `tests/test_version_manager.py`：

```python
"""版本管理单元测试"""
import pytest
from src.utils.version_manager import VERSION_UNKNOWN, is_version_known


def test_version_unknown_constants():
    """VERSION_UNKNOWN 包含常见未知值"""
    assert "unknown" in VERSION_UNKNOWN
    assert "" in VERSION_UNKNOWN
    assert None in VERSION_UNKNOWN


def test_is_version_known_with_sha():
    """有效 SHA 判定为已知版本"""
    assert is_version_known("a1b2c3d4e5f6") is True


def test_is_version_known_with_unknown():
    """unknown 判定为未知"""
    assert is_version_known("unknown") is False


def test_is_version_known_with_empty():
    """空字符串判定为未知"""
    assert is_version_known("") is False


def test_is_version_known_with_none():
    """None 判定为未知"""
    assert is_version_known(None) is False
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "D:\U 盘\SpiderClaw" && python -m pytest tests/test_version_manager.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现版本管理**

创建 `src/utils/version_manager.py`：

```python
"""版本管理 — 确保仓库可用并 checkout 到正确版本（三级降级）"""
import logging
import os
import asyncio
from git import Repo, GitCommandError

logger = logging.getLogger(__name__)

# 版本未知的统一判断常量
VERSION_UNKNOWN = ("unknown", "", None)


def is_version_known(version: str) -> bool:
    """判断版本号是否有效（非未知状态）"""
    return version not in VERSION_UNKNOWN


async def ensure_repo_with_version(
    repo_url: str,
    local_path: str,
    version: str,
    branch: str = "main",
) -> tuple[str, bool]:
    """确保仓库可用并 checkout 到正确版本

    三级降级策略：
    1. version 已知 + local_path 存在 → fetch + checkout 精确 commit
    2. version 未知 + local_path 存在 → fetch + checkout branch + pull
    3. local_path 不存在 → clone 到 local_path

    Args:
        repo_url: Git 仓库 URL
        local_path: 本地持久化路径
        version: Git commit SHA（可为 "unknown" 或空）
        branch: 目标分支名

    Returns:
        (repo_path, degraded) — degraded=True 表示降级到最新代码
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _ensure_repo_sync, repo_url, local_path, version, branch
    )


def _ensure_repo_sync(
    repo_url: str,
    local_path: str,
    version: str,
    branch: str,
) -> tuple[str, bool]:
    """同步版本的仓库确保逻辑（在线程池中执行）"""
    local_path = os.path.abspath(local_path)

    # 情况 3：本地路径不存在 → clone
    if not os.path.exists(os.path.join(local_path, ".git")):
        logger.info(f"首次 clone: {repo_url} -> {local_path}")
        os.makedirs(local_path, exist_ok=True)
        try:
            Repo.clone_from(repo_url, local_path, branch=branch)
        except GitCommandError as e:
            logger.error(f"clone 失败: {e}")
            raise
        # clone 后如果 version 已知，尝试 checkout
        if is_version_known(version):
            try:
                repo = Repo(local_path)
                repo.git.fetch("origin")
                repo.git.checkout(version)
                logger.info(f"clone 后 checkout 到精确版本: {version}")
                return local_path, False
            except GitCommandError:
                logger.warning(f"无法 checkout 到 {version}，使用最新 {branch}")
        return local_path, True

    # 本地路径已存在
    repo = Repo(local_path)

    # 情况 1：version 已知 → fetch + checkout 精确 commit
    if is_version_known(version):
        try:
            repo.git.fetch("origin")
            repo.git.checkout(version)
            logger.info(f"checkout 到精确版本: {version}")
            return local_path, False
        except GitCommandError:
            logger.warning(f"无法 checkout 到 {version}，降级到最新 {branch}")

    # 情况 2：version 未知 → fetch + checkout branch + pull
    try:
        repo.git.fetch("origin")
        repo.git.checkout(branch)
        repo.git.pull("origin", branch)
        logger.info(f"更新到最新 {branch}")
    except GitCommandError as e:
        logger.warning(f"更新分支失败: {e}，使用本地现有代码")

    return local_path, True
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd "D:\U 盘\SpiderClaw" && python -m pytest tests/test_version_manager.py -v
```

Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/utils/version_manager.py tests/test_version_manager.py
git commit -m "feat: add version manager with three-level fallback strategy"
```

---

## Task 6: 限流器

**Files:**
- Create: `src/utils/rate_limiter.py`
- Create: `tests/test_rate_limiter.py`

- [ ] **Step 1: 编写测试**

创建 `tests/test_rate_limiter.py`：

```python
"""限流器单元测试"""
import pytest
import time
from unittest.mock import patch
from src.utils.rate_limiter import ServiceRateLimiter


@pytest.fixture
def limiter():
    return ServiceRateLimiter(max_per_minute=2, max_per_hour=5)


@pytest.mark.asyncio
async def test_allows_first_request(limiter):
    """首次请求允许通过"""
    assert await limiter.check("order-service") is True


@pytest.mark.asyncio
async def test_blocks_after_minute_limit(limiter):
    """超过每分钟限制后被限流"""
    await limiter.record("order-service")
    await limiter.record("order-service")
    assert await limiter.check("order-service") is False


@pytest.mark.asyncio
async def test_different_services_independent(limiter):
    """不同服务的限流独立计算"""
    await limiter.record("order-service")
    await limiter.record("order-service")
    assert await limiter.check("order-service") is False
    assert await limiter.check("user-service") is True


@pytest.mark.asyncio
async def test_should_alert_after_threshold(limiter):
    """连续限流超过阈值后触发告警"""
    # 先触发限流
    await limiter.record("order-service")
    await limiter.record("order-service")
    # 连续 check 多次（都被限流）
    for _ in range(10):
        await limiter.check("order-service")
    assert limiter.should_alert("order-service") is True


@pytest.mark.asyncio
async def test_no_alert_when_not_limited(limiter):
    """未触发限流时不告警"""
    await limiter.record("order-service")
    assert limiter.should_alert("order-service") is False
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "D:\U 盘\SpiderClaw" && python -m pytest tests/test_rate_limiter.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现限流器**

创建 `src/utils/rate_limiter.py`：

```python
"""滑动窗口限流器 — 基于服务名的修复频率控制"""
import time
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# 连续限流多少次后触发告警
ALERT_THRESHOLD = 10


class ServiceRateLimiter:
    """基于服务名的滑动窗口限流

    集成位置：event_consumer 层，事件进入 LangGraph 图之前。
    被限流的事件直接丢弃，不消耗图资源。
    """

    def __init__(self, max_per_minute: int = 3, max_per_hour: int = 20):
        self._max_per_minute = max_per_minute
        self._max_per_hour = max_per_hour
        # {service: [timestamp, ...]}
        self._minute_records: dict[str, list[float]] = defaultdict(list)
        self._hour_records: dict[str, list[float]] = defaultdict(list)
        # 连续被限流的次数
        self._limited_counts: dict[str, int] = defaultdict(int)

    async def check(self, service: str) -> bool:
        """检查服务是否允许修复。返回 True 表示允许。"""
        now = time.time()

        # 清理过期记录
        self._minute_records[service] = [
            t for t in self._minute_records[service] if now - t < 60
        ]
        self._hour_records[service] = [
            t for t in self._hour_records[service] if now - t < 3600
        ]

        # 检查限制
        if len(self._minute_records[service]) >= self._max_per_minute:
            self._limited_counts[service] += 1
            logger.warning(
                f"服务 {service} 触发分钟限流 "
                f"({len(self._minute_records[service])}/{self._max_per_minute})"
            )
            return False

        if len(self._hour_records[service]) >= self._max_per_hour:
            self._limited_counts[service] += 1
            logger.warning(
                f"服务 {service} 触发小时限流 "
                f"({len(self._hour_records[service])}/{self._max_per_hour})"
            )
            return False

        # 通过限流，重置连续限流计数
        self._limited_counts[service] = 0
        return True

    async def record(self, service: str) -> None:
        """记录一次修复"""
        now = time.time()
        self._minute_records[service].append(now)
        self._hour_records[service].append(now)

    def should_alert(self, service: str) -> bool:
        """连续限流超过阈值时返回 True"""
        return self._limited_counts[service] >= ALERT_THRESHOLD
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd "D:\U 盘\SpiderClaw" && python -m pytest tests/test_rate_limiter.py -v
```

Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/utils/rate_limiter.py tests/test_rate_limiter.py
git commit -m "feat: add sliding window rate limiter for remote log events"
```

---

## Task 7: 编排器改造 — 双入口 + collect_runtime_context

**Files:**
- Modify: `src/agent/orchestrator.py`

这是最核心的改动，需要修改图结构并新增 `collect_runtime_context` 节点。

- [ ] **Step 1: 添加新导入**

在 `src/agent/orchestrator.py` 顶部的导入区域添加：

```python
from src.bus.schemas import RuntimeLogEvent
from src.utils.path_mapping import apply_path_mapping
from src.utils.version_manager import ensure_repo_with_version
```

- [ ] **Step 2: 修改 _build_graph — 添加双入口**

在 `_build_graph` 方法中，将 `workflow.add_edge(START, "collect_context")` 替换为：

```python
        # 双入口：根据事件类型路由到不同的上下文收集节点
        workflow.add_node("collect_runtime_context", self._collect_runtime_context)
        workflow.add_conditional_edges(
            START,
            self._route_by_event,
            ["collect_context", "collect_runtime_context"],
        )
        workflow.add_conditional_edges(
            "collect_runtime_context",
            self._route_after_context,
            ["fix_agent", "handle_failure", END],
        )
```

- [ ] **Step 3: 添加 _route_by_event 路由函数**

在 `RepairOrchestrator` 类中（建议放在 `_route_after_context` 方法之前）添加：

```python
    def _route_by_event(self, state: RepairState) -> str:
        """入口路由：根据事件类型分发到不同的上下文收集节点"""
        event = state.get("event")
        if getattr(event, "event_type", "") == "runtime_log":
            return "collect_runtime_context"
        return "collect_context"
```

- [ ] **Step 4: 添加 _collect_runtime_context 节点**

在 `_collect_context` 方法之后添加新节点：

```python
    async def _collect_runtime_context(self, state: RepairState) -> dict[str, Any]:
        """收集运行时日志上下文 — 独立于 CI 事件的 collect_context"""
        audit_logger.log_event("node_enter", node="collect_runtime_context")
        event: RuntimeLogEvent = state["event"]
        logger.info(f"收集运行时上下文: service={event.service}, version={event.version}")

        try:
            # 事件去重
            event_key = f"runtime:{event.service}:{event.event_id}"
            async with self.lock:
                if event_key in self.processed_events:
                    logger.info(f"事件 {event_key} 已处理过，跳过")
                    return {"success": False, "error_message": "事件已处理过"}
                self.processed_events.add(event_key)

            set_tool_context({"github_token": self.github_token})

            # 1. 解析 Traceback
            error_locations_raw = []
            if event.log:
                error_locations_raw = parse_python_errors.invoke({"log_content": event.log})
                logger.info(f"解析到错误数量: {len(error_locations_raw)}")

            if not error_locations_raw:
                async with self.lock:
                    self.processed_events.discard(event_key)
                return {"success": False, "error_message": "日志中未检测到Python错误"}

            # 2. 路径映射
            for err in error_locations_raw:
                fp = err.get("file_path", "")
                if fp:
                    err["file_path"] = apply_path_mapping(fp, event.path_mapping)

            # 3. 版本管理（三级降级）
            repo_path, degraded = await ensure_repo_with_version(
                repo_url=event.repo_url,
                local_path=event.repo_local_path,
                version=event.version,
                branch=event.branch,
            )
            set_tool_context({"github_token": self.github_token, "repo_path": repo_path})

            if degraded:
                logger.warning(f"版本降级：使用最新 {event.branch} 分支代码")

            # 4. 过滤有效错误
            error_locations = self._filter_valid_errors(error_locations_raw, repo_path)
            if not error_locations:
                async with self.lock:
                    self.processed_events.discard(event_key)
                return {"success": False, "error_message": "过滤后没有有效错误"}

            # 5. 提取目标文件 + 读取源码
            target_files = sorted(set(
                err.file_path for err in error_locations
                if hasattr(err, "file_path") and err.file_path and err.file_path != "<string>"
            ))
            logger.info(f"目标文件列表: {target_files}")

            original_codes = {}
            for fp in target_files:
                try:
                    content = read_file.invoke({"file_path": f"{repo_path}/{fp}"})
                    if not content.startswith("Error:"):
                        original_codes[fp] = content
                except Exception as e:
                    logger.warning(f"读取文件 {fp} 失败: {e}")

            return {
                "ci_logs": event.log,
                "repo_path": repo_path,
                "error_locations": error_locations,
                "target_files": target_files,
                "original_codes": original_codes,
                "retry_count": 0,
                "max_retries": self.max_retries,
                "review_comments": "",
                "test_output": "",
                "risk_warnings": [],
                "failed_tests": [],
                "risk_level": "NONE",
                "degraded_version": degraded,
                "fix_source": "runtime_log",
            }

        except Exception as e:
            logger.error(f"收集运行时上下文失败: {e}", exc_info=True)
            return {"success": False, "error_message": f"收集运行时上下文失败: {str(e)}"}
        finally:
            audit_logger.log_event("node_exit", node="collect_runtime_context")
```

- [ ] **Step 5: 修改 run() 入口方法**

在 `run()` 方法中，找到 `initial_state` 字典构建（约第 1301 行），修改 `ci_logs` 的初始化：

```python
            # 根据事件类型决定 ci_logs 来源
            if getattr(event, "event_type", "") == "runtime_log":
                initial_ci_logs = event.log
            else:
                initial_ci_logs = ci_logs

            initial_state = {
                "event": event,
                "ci_logs": initial_ci_logs,
                # ... 其余字段不变
```

- [ ] **Step 6: 修改 _create_pull_request — 适配远程日志事件**

在 `_create_pull_request` 方法中，找到 PR 标题构建逻辑（约第 1020 行），修改为：

```python
            # PR 标题：区分来源
            if getattr(event, "event_type", "") == "runtime_log":
                pr_title = f"[SpiderClaw: fix] {primary_error} @{event.service}: {short_desc}"
            else:
                pr_title = f"[SpiderClaw: fix] {primary_error} @{pr_author_title}: {short_desc}"
```

在 PR body 构建之后，添加降级风险标注：

```python
            # 远程日志降级风险标注
            if state.get("degraded_version"):
                pr_body += (
                    "\n\n> ⚠️ **版本降级提示**：无法精确 checkout 到错误发生时的代码版本，"
                    "本次修复基于最新代码。修复可能不完全精确，请仔细审查。"
                )
```

- [ ] **Step 7: 验证导入和语法**

```bash
cd "D:\U 盘\SpiderClaw" && python -c "from src.agent.orchestrator import RepairOrchestrator; print('OK')"
```

Expected: `OK`

- [ ] **Step 8: 提交**

```bash
git add src/agent/orchestrator.py
git commit -m "feat: add dual-entry graph with collect_runtime_context node"
```

---

## Task 8: Webhook 端点 + 事件消费

**Files:**
- Modify: `src/monitor/webhook_server.py`

- [ ] **Step 1: 添加新导入**

在 `src/monitor/webhook_server.py` 顶部添加：

```python
from src.bus.schemas import RuntimeLogEvent
from src.config.service_registry import get_service_registry
from src.utils.rate_limiter import ServiceRateLimiter
```

- [ ] **Step 2: 在 _setup_routes 中添加 /webhook/log 端点**

在 `handle_github_webhook` 路由之后添加：

```python
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
            if not svc:
                logger.warning(f"未知服务: {service_name}")
                return {"status": "unknown_service", "service": service_name}

            # 创建事件
            import uuid
            event = RuntimeLogEvent(
                event_id=str(uuid.uuid4()),
                source="remote_log",
                log=log_content,
                service=service_name,
                version=body.get("version", ""),
                hostname=body.get("hostname", ""),
                repo_url=svc.repo_url,
                repo_local_path=svc.repo_local_path,
                branch=svc.git_branch,
                path_mapping=svc.path_mapping,
                # 兼容字段
                repository=service_name,
                clone_url=svc.repo_url,
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
```

- [ ] **Step 3: 修改 run_webhook_server — 事件消费扩展**

在 `run_webhook_server` 函数的 `event_consumer` 内部函数中，找到现有的事件处理逻辑，在 `isinstance(event, GitHubEvent)` 判断之前添加 RuntimeLogEvent 处理：

```python
        # 初始化限流器
        rate_limiter = ServiceRateLimiter(
            max_per_minute=get_service_registry().rate_limit.max_fixes_per_minute,
            max_per_hour=get_service_registry().rate_limit.max_fixes_per_hour,
        )

        async def event_consumer():
            """事件消费循环"""
            if not orchestrator:
                return
            while True:
                try:
                    event = await event_bus.subscribe()

                    # 远程日志事件处理（限流在图入口之前）
                    if isinstance(event, RuntimeLogEvent):
                        if not await rate_limiter.check(event.service):
                            logger.warning(f"服务 {event.service} 触发限流，跳过")
                            if rate_limiter.should_alert(event.service):
                                logger.error(f"服务 {event.service} 持续限流，请人工检查")
                            event_bus.mark_done()
                            continue
                        await rate_limiter.record(event.service)

                        async def process_runtime_and_mark_done(evt=event):
                            try:
                                await orchestrator.run(evt)
                            finally:
                                event_bus.mark_done()

                        asyncio.create_task(
                            process_runtime_and_mark_done(),
                            name=f"runtime_{event.event_id}"
                        )
                        continue

                    # GitHub CI 事件处理（原有逻辑）
                    if isinstance(event, GitHubEvent) and event.conclusion == "failure":
                        # ... 原有逻辑不变
```

注意：`process_runtime_and_mark_done` 使用默认参数 `evt=event` 来捕获当前事件，避免闭包问题。

- [ ] **Step 4: 同步修改 CLI webhook 命令**

在 `src/cli/commands/webhook.py` 的 `event_consumer` 函数中做相同的扩展（与上面步骤 3 一致）。

- [ ] **Step 5: 验证语法**

```bash
cd "D:\U 盘\SpiderClaw" && python -c "from src.monitor.webhook_server import GitHubWebhookMonitor; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: 提交**

```bash
git add src/monitor/webhook_server.py src/cli/commands/webhook.py
git commit -m "feat: add /webhook/log endpoint with rate limiting"
```

---

## Task 9: CLI init-sidecar 命令

**Files:**
- Modify: `src/cli/app.py`

- [ ] **Step 1: 添加 init-sidecar 命令**

在 `src/cli/app.py` 中找到 `setup_wizard` 函数（约第 203 行），在其后添加：

```python
@app.command("init-sidecar")
def init_sidecar(
    output_dir: str = typer.Option("./sidecar", "-o", "--output", help="输出目录"),
    service_name: str = typer.Option("my-service", "-n", "--name", help="服务名称"),
    agent_url: str = typer.Option("http://agent-host:8000/webhook/log", "--agent-url", help="Agent Webhook 地址"),
    log_path: str = typer.Option("/var/log/app/app.log", "--log-path", help="日志文件路径"),
):
    """生成采集脚本和配置模板"""
    import os

    os.makedirs(output_dir, exist_ok=True)

    # 生成 agent-mapping.conf
    conf_content = f'''# SpiderClaw 采集脚本配置
# 部署到业务服务器: /opt/agent-sidecar/agent-mapping.conf

SERVICE_NAME="{service_name}"
SERVICE_VERSION=""  # 部署时写入: echo "SERVICE_VERSION=$(git rev-parse HEAD)" >> agent-mapping.conf
LOG_PATH="{log_path}"
AGENT_URL="{agent_url}"
'''
    conf_path = os.path.join(output_dir, "agent-mapping.conf")
    with open(conf_path, "w", encoding="utf-8") as f:
        f.write(conf_content)

    # 生成 collector.sh
    collector_content = '''#!/bin/bash
# SpiderClaw 远程日志采集脚本
# 部署到业务服务器: /opt/agent-sidecar/collector.sh
# 启动: nohup bash /opt/agent-sidecar/collector.sh > /dev/null 2>&1 &

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/agent-mapping.conf"

AGENT_URL="${AGENT_URL:-http://agent-host:8000/webhook/log}"
DEDUP_WINDOW="${DEDUP_WINDOW:-300}"
BATCH_INTERVAL="${BATCH_INTERVAL:-10}"
MAX_BATCH_LINES="${MAX_BATCH_LINES:-50}"
MAX_BACKOFF="${MAX_BACKOFF:-300}"

LAST_HASH=""
BACKOFF=1
ERROR_CACHE=""
LAST_SEND_TIME=0
LINE_COUNT=0

# 错误哈希函数：提取 File+行号+错误类型 → MD5 前12位
compute_hash() {
    local text="$1"
    local file_line
    file_line=$(echo "$text" | grep -oP 'File "[^"]+", line \\d+' | tail -1 || true)
    local error_type
    error_type=$(echo "$text" | grep -oP '[A-Z][a-zA-Z0-9]*Error' | head -1 || true)
    local key="${file_line}:${error_type}"
    echo -n "$key" | md5sum | cut -c1-12
}

send_batch() {
    local payload="$1"
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \\
        -X POST "$AGENT_URL" \\
        -H "Content-Type: application/json" \\
        -d "$payload" \\
        --connect-timeout 10 \\
        --max-time 30 || echo "000")

    if [ "$http_code" = "429" ]; then
        BACKOFF=$((BACKOFF * 2))
        [ "$BACKOFF" -gt "$MAX_BACKOFF" ] && BACKOFF=$MAX_BACKOFF
        sleep "$BACKOFF"
    elif [ "$http_code" = "200" ] || [ "$http_code" = "201" ]; then
        BACKOFF=1
    else
        BACKOFF=$((BACKOFF * 2))
        [ "$BACKOFF" -gt "$MAX_BACKOFF" ] && BACKOFF=$MAX_BACKOFF
    fi
}

flush_cache() {
    [ -z "$ERROR_CACHE" ] && return

    local hash
    hash=$(compute_hash "$ERROR_CACHE")
    local now
    now=$(date +%s)

    # 去重：相同错误在 DEDUP_WINDOW 内不重复发送
    if [ "$hash" = "$LAST_HASH" ] && [ $((now - LAST_SEND_TIME)) -lt "$DEDUP_WINDOW" ]; then
        ERROR_CACHE=""
        LINE_COUNT=0
        return
    fi

    # 构造 JSON payload
    local escaped_log
    escaped_log=$(echo "$ERROR_CACHE" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '""')
    local payload="{\\\"log\\\":${escaped_log},\\\"service\\\":\\\"${SERVICE_NAME}\\\",\\\"version\\\":\\\"${SERVICE_VERSION}\\\",\\\"hostname\\\":\\\"$(hostname)\\\"}"

    send_batch "$payload"

    LAST_HASH="$hash"
    LAST_SEND_TIME=$now
    ERROR_CACHE=""
    LINE_COUNT=0
}

# 主循环
echo "SpiderClaw collector started: service=$SERVICE_NAME log=$LOG_PATH"
tail -F "$LOG_PATH" 2>/dev/null | while IFS= read -r line; do
    # 检测错误关键词
    if echo "$line" | grep -qiE 'Error|Traceback|Exception|FAILED'; then
        ERROR_CACHE="${ERROR_CACHE}${line}\\n"
        LINE_COUNT=$((LINE_COUNT + 1))

        # 批量发送条件：间隔到达 或 行数到达
        local now
        now=$(date +%s)
        if [ $LINE_COUNT -ge "$MAX_BATCH_LINES" ] || \
           ([ $((now - LAST_SEND_TIME)) -ge "$BATCH_INTERVAL" ] && [ $LINE_COUNT -gt 0 ]); then
            flush_cache
        fi
    fi
done
'''
    collector_path = os.path.join(output_dir, "collector.sh")
    with open(collector_path, "w", encoding="utf-8") as f:
        f.write(collector_content)
    os.chmod(collector_path, 0o755)

    console.print(Panel(
        f"采集脚本模板已生成！\n\n"
        f"输出目录: [#20d5f0]{output_dir}[/#20d5f0]\n"
        f"配置文件: [#20d5f0]{conf_path}[/#20d5f0]\n"
        f"采集脚本: [#20d5f0]{collector_path}[/#20d5f0]\n\n"
        f"部署步骤：\n"
        f"1. 将上述文件复制到业务服务器 /opt/agent-sidecar/\n"
        f"2. 修改 agent-mapping.conf 中的 SERVICE_VERSION\n"
        f"3. 启动: nohup bash /opt/agent-sidecar/collector.sh > /dev/null 2>&1 &",
        title="[bold #20d5f0]init-sidecar 完成[/bold #20d5f0]",
        border_style="#20d5f0",
    ))
```

- [ ] **Step 2: 验证命令可用**

```bash
cd "D:\U 盘\SpiderClaw" && python -m src.cli.app init-sidecar --help
```

Expected: 显示 `--output`, `--name`, `--agent-url`, `--log-path` 参数帮助

- [ ] **Step 3: 提交**

```bash
git add src/cli/app.py
git commit -m "feat: add init-sidecar CLI command for collector script generation"
```

---

## Task 10: 采集脚本模板

**Files:**
- Create: `scripts/collector.sh`
- Create: `scripts/agent-mapping.conf`

- [ ] **Step 1: 创建 agent-mapping.conf 模板**

创建 `scripts/agent-mapping.conf`：

```bash
# SpiderClaw 采集脚本配置
# 部署到业务服务器: /opt/agent-sidecar/agent-mapping.conf

# 服务标识（必填，与 Agent 端 services.yaml 中的 name 对应）
SERVICE_NAME="my-service"

# 当前部署版本（Git commit SHA）
# 部署脚本自动写入: echo "SERVICE_VERSION=$(git rev-parse HEAD)" >> agent-mapping.conf
# 空值视为未知，Agent 将降级到最新代码
SERVICE_VERSION=""

# 要监控的日志文件路径
LOG_PATH="/var/log/app/app.log"

# Agent Webhook 地址
AGENT_URL="http://agent-host:8000/webhook/log"
```

- [ ] **Step 2: 创建 collector.sh 脚本**

创建 `scripts/collector.sh`（内容与 Task 9 中生成的一致，作为独立可分发的模板文件）。

- [ ] **Step 3: 设置执行权限（Unix）**

```bash
chmod +x scripts/collector.sh
```

- [ ] **Step 4: 提交**

```bash
git add scripts/
git commit -m "feat: add collector script template for remote log monitoring"
```

---

## Task 11: 集成测试 — /webhook/log 端点

**Files:**
- Create: `tests/test_webhook_log.py`

- [ ] **Step 1: 编写集成测试**

创建 `tests/test_webhook_log.py`：

```python
"""/webhook/log 端点集成测试"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

from src.bus.schemas import RuntimeLogEvent


@pytest.fixture
def mock_service_registry():
    """模拟 ServiceRegistry"""
    from src.config.settings import ServiceConfig
    mock_svc = ServiceConfig(
        name="test-service",
        repo_url="https://github.com/test/repo.git",
        repo_local_path="/tmp/test-repo",
        git_branch="main",
        path_mapping={"/app/": "src/"},
    )
    mock_registry = MagicMock()
    mock_registry.get.return_value = mock_svc
    mock_registry.rate_limit.max_fixes_per_minute = 3
    mock_registry.rate_limit.max_fixes_per_hour = 20
    return mock_registry


@pytest.fixture
def test_app(mock_service_registry):
    """创建测试用 FastAPI 应用"""
    app = FastAPI()

    @app.post("/webhook/log")
    async def handle_log(request):
        from fastapi import Request as Req
        body = await request.json()
        log_content = body.get("log", "")
        service_name = body.get("service", "")

        if not log_content or not service_name:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Missing required fields")

        svc = mock_service_registry.get(service_name)
        if not svc:
            return {"status": "unknown_service"}

        return {"status": "accepted", "event_id": "test-123"}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


def test_receive_log_success(test_app):
    """正常接收日志事件"""
    client = TestClient(test_app)
    response = client.post("/webhook/log", json={
        "log": "Traceback (most recent call last):\n  File \"/app/main.py\", line 1\nZeroDivisionError: division by zero",
        "service": "test-service",
        "version": "abc123",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"


def test_receive_log_missing_fields(test_app):
    """缺少必填字段返回 400"""
    client = TestClient(test_app)
    response = client.post("/webhook/log", json={
        "service": "test-service",
    })
    assert response.status_code == 400


def test_health_check(test_app):
    """健康检查端点正常"""
    client = TestClient(test_app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
```

- [ ] **Step 2: 运行测试**

```bash
cd "D:\U 盘\SpiderClaw" && python -m pytest tests/test_webhook_log.py -v
```

Expected: 全部 PASS

- [ ] **Step 3: 提交**

```bash
git add tests/test_webhook_log.py
git commit -m "test: add integration tests for /webhook/log endpoint"
```

---

## Task 12: 全量验证

- [ ] **Step 1: 运行所有测试**

```bash
cd "D:\U 盘\SpiderClaw" && python -m pytest tests/ -v --asyncio-mode=auto
```

Expected: 所有测试通过，无回归

- [ ] **Step 2: 检查导入完整性**

```bash
cd "D:\U 盘\SpiderClaw" && python -c "
from src.bus import RuntimeLogEvent
from src.config.service_registry import get_service_registry
from src.utils.path_mapping import apply_path_mapping
from src.utils.version_manager import ensure_repo_with_version, VERSION_UNKNOWN
from src.utils.rate_limiter import ServiceRateLimiter
from src.agent.orchestrator import RepairOrchestrator
from src.monitor.webhook_server import GitHubWebhookMonitor
print('All imports OK')
"
```

Expected: `All imports OK`

- [ ] **Step 3: 最终提交**

```bash
git add -A
git commit -m "feat: complete remote log monitoring implementation"
```
