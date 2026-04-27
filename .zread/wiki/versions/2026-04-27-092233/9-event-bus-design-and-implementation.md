本文档详细介绍SpiderClaw系统核心异步事件总线的设计理念、数据模型、核心能力实现与API规范，目标受众为需要进行二次开发或问题排查的高级开发者。事件总线是系统各模块解耦的核心基础设施，负责承接所有上游事件并路由到下游消费模块。

## 架构总览
事件总线采用异步无锁设计，核心目标是实现生产者与消费者的完全解耦，同时保证高并发场景下的可靠性和性能。
```mermaid
flowchart LR
    P1[GitHub Webhook 生产者]
    P2[CLI 命令生产者]
    P3[本地测试 生产者]
    EB[事件总线\n[重复过滤器 + 异步队列]]
    C1[Agent 编排消费者]
    C2[通知服务消费者]
    C3[监控服务消费者]
    
    P1 --> EB
    P2 --> EB
    P3 --> EB
    EB --> C1
    EB --> C2
    EB --> C3
```
设计遵循三大原则：1. 全异步实现适配高并发事件吞吐需求；2. 内置可靠性机制避免事件重复消费与队列溢出；3. 低侵入式集成，生产者与消费者无直接依赖。
Sources: [event_bus.py](src/bus/event_bus.py#L11-L42)

## 核心事件数据模型
所有总线事件均继承自`BaseEvent`基础模型，采用Pydantic实现类型校验与序列化支持。
### BaseEvent 公共字段
| 字段名 | 类型 | 描述 |
|--------|------|------|
| event_id | str | 事件全局唯一ID，用于去重判断 |
| event_type | str | 事件类型标识，消费者据此进行分发处理 |
| timestamp | datetime | 事件生成时间，默认自动生成 |
| source | str | 事件来源标识，用于问题溯源 |
| payload | Dict[str, Any] | 事件原始负载，可存储任意扩展数据 |

### GitHubEvent 业务事件
系统核心业务事件，继承自`BaseEvent`，扩展了GitHub Webhook场景所需的专用字段：
| 字段名 | 类型 | 描述 |
|--------|------|------|
| action | str | GitHub事件动作（如completed、opened等） |
| repository | str | 关联仓库全名（owner/repo格式） |
| signature_valid | bool | Webhook签名是否验证通过 |
| clone_url | str | 仓库克隆地址 |
| branch | str | 关联分支名 |
| pr_number | Optional[int] | 关联PR编号，非PR事件为None |
| logs_url | str | CI日志下载链接 |
| conclusion | str | CI执行结果（success/failure等） |
Sources: [schemas.py](src/bus/schemas.py#L7-L47)

## 核心能力实现
### 反压保护机制
事件总线内置队列容量控制，避免消费速度慢于生产速度时内存溢出：
- 初始化时可指定`maxsize`参数设置队列最大容量，0表示无限制
- 发布事件采用非阻塞实现，队列满时直接返回失败并记录丢弃统计
- 丢弃事件会触发警告日志，便于运维监控队列健康状态
Sources: [event_bus.py](src/bus/event_bus.py#L43-L67)

### 幂等去重机制
为解决上游重试导致的重复事件问题，总线内置自动去重能力：
- 已处理事件ID存储在集合中，默认保留10000条，过期时间3600秒
- 每次检查重复前自动清理过期ID，超过容量时采用LRU策略淘汰最早的ID
- 重复事件返回发布成功，避免上游（如GitHub）持续重试
Sources: [event_bus.py](src/bus/event_bus.py#L87-L129)

### 可观测性支持
总线内置全链路统计能力，支持运行状态监控与问题排查：
- `get_stats()`方法返回队列大小、发布数量、丢弃数量、重复数量等核心指标
- `drain()`方法支持优雅关闭时等待所有队列中事件处理完成
- 所有关键操作均有日志输出，支持debug级别的全链路追踪
Sources: [event_bus.py](src/bus/event_bus.py#L131-L148)

### 全局单例模式
系统提供全局唯一事件总线实例，避免多实例导致的事件分散问题：
- 通过`get_event_bus()`函数获取全局实例，支持首次调用时传入配置参数
- 所有模块共享同一个实例，保证事件统一路由
Sources: [event_bus.py](src/bus/event_bus.py#L151-L160)

## 公共API参考
| 方法名 | 参数 | 返回值 | 描述 |
|--------|------|--------|------|
| publish | event: BaseEvent | bool | 异步非阻塞发布事件，成功返回True，队列满返回False |
| subscribe | 无 | BaseEvent | 异步阻塞订阅事件，获取队列中最早的事件 |
| mark_done | 无 | None | 标记事件处理完成，需在消费完成后调用 |
| drain | 无 | None | 异步等待队列中所有事件处理完成，用于优雅关闭 |
| qsize | 无 | int | 获取当前队列待处理事件数量 |
| get_stats | 无 | dict[str, Any] | 获取总线运行统计指标 |
| get_event_bus | **kwargs | EventBus | 获取全局事件总线实例，首次调用可传入配置参数 |

## 典型使用示例
### 事件发布示例
```python
from src.bus import get_event_bus, GitHubEvent

# 获取全局总线实例
bus = get_event_bus()

# 构建事件
event = GitHubEvent(
    event_id="unique-event-id-123",
    event_type="workflow_run",
    action="completed",
    source="github_webhook",
    repository="owner/repo",
    signature_valid=True,
    conclusion="failure"
)

# 发布事件
success = await bus.publish(event)
if not success:
    print("事件队列已满，发布失败")
```

### 事件消费示例
```python
from src.bus import get_event_bus

bus = get_event_bus()

async def event_consumer():
    while True:
        # 阻塞等待事件
        event = await bus.subscribe()
        try:
            # 处理事件逻辑
            print(f"收到事件：{event.event_id}, 类型：{event.event_type}")
        finally:
            # 标记事件处理完成
            bus.mark_done()
```
Sources: [test_event_bus.py](tests/bus/test_event_bus.py#L7-L157)

## 后续阅读
- 了解事件如何被消费处理：[Agent Orchestration Workflow](10-agent-orchestration-workflow)
- 了解事件如何从Webhook产生：[Monitor Subsystem Deep Dive](12-monitor-subsystem-deep-dive)