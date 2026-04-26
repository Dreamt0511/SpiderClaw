"""事件总线单元测试"""
import asyncio
import pytest
from src.bus import EventBus, GitHubEvent


@pytest.mark.asyncio
async def test_event_bus_publish_subscribe():
    """测试事件发布和订阅"""
    bus = EventBus(maxsize=10)

    # 创建测试事件
    event = GitHubEvent(
        event_id="test-123",
        event_type="workflow_run",
        action="completed",
        source="test",
        repository="owner/repo",
        signature_valid=True
    )

    # 发布事件
    result = await bus.publish(event)
    assert result is True
    assert bus.qsize() == 1

    # 订阅事件
    received_event = await bus.subscribe()
    assert received_event.event_id == "test-123"
    assert bus.qsize() == 0


@pytest.mark.asyncio
async def test_event_bus_backpressure():
    """测试反压机制"""
    bus = EventBus(maxsize=2)

    # 发布3个事件，第三个应该失败
    event1 = GitHubEvent(event_id="1", event_type="test", action="test", source="test", repository="test/repo", signature_valid=True)
    event2 = GitHubEvent(event_id="2", event_type="test", action="test", source="test", repository="test/repo", signature_valid=True)
    event3 = GitHubEvent(event_id="3", event_type="test", action="test", source="test", repository="test/repo", signature_valid=True)

    assert await bus.publish(event1) is True
    assert await bus.publish(event2) is True
    assert await bus.publish(event3) is False  # 队列满，发布失败

    assert bus.qsize() == 2
    assert bus.get_stats()["dropped_count"] == 1


@pytest.mark.asyncio
async def test_event_bus_duplicate_detection():
    """测试重复事件检测"""
    bus = EventBus()

    event = GitHubEvent(
        event_id="duplicate-test",
        event_type="workflow_run",
        action="completed",
        source="test",
        repository="owner/repo",
        signature_valid=True
    )

    # 第一次发布
    assert await bus.publish(event) is True
    assert bus.qsize() == 1

    # 第二次发布同一个事件，应该被识别为重复
    assert await bus.publish(event) is True  # 重复事件返回成功
    assert bus.qsize() == 1  # 队列大小不变
    assert bus.get_stats()["duplicate_count"] == 1


@pytest.mark.asyncio
async def test_event_bus_processed_ids_lru():
    """测试已处理ID的LRU清理机制"""
    bus = EventBus(max_processed_ids=10)

    # 发布15个不同的事件
    for i in range(15):
        event = GitHubEvent(
            event_id=f"event-{i}",
            event_type="test",
            action="test",
            source="test",
            repository="test/repo",
            signature_valid=True
        )
        await bus.publish(event)

    # 已处理ID应该保留最近的10个
    stats = bus.get_stats()
    assert stats["processed_ids_count"] == 10

    # 验证最近的10个ID还在（先验证，避免检查旧ID时加入新ID导致LRU淘汰）
    for i in range(5, 15):
        assert await bus.is_duplicate(f"event-{i}") is True

    # 验证最早的5个ID被清理了
    for i in range(5):
        assert not await bus.is_duplicate(f"event-{i}")  # 应该返回False，说明不在已处理集合中


@pytest.mark.asyncio
async def test_event_bus_drain():
    """测试队列排空功能"""
    bus = EventBus()

    # 发布多个事件
    for i in range(5):
        event = GitHubEvent(
            event_id=f"drain-test-{i}",
            event_type="test",
            action="test",
            source="test",
            repository="test/repo",
            signature_valid=True
        )
        await bus.publish(event)

    assert bus.qsize() == 5

    # 启动一个协程来消费事件
    async def consumer():
        while bus.qsize() > 0:
            await bus.subscribe()
            bus.mark_done()
            await asyncio.sleep(0.01)

    consumer_task = asyncio.create_task(consumer())

    # 等待队列排空
    await bus.drain()
    await consumer_task

    assert bus.qsize() == 0


@pytest.mark.asyncio
async def test_event_bus_stats():
    """测试统计信息"""
    bus = EventBus()

    event1 = GitHubEvent(event_id="stat-1", event_type="test", action="test", source="test", repository="test/repo", signature_valid=True)
    event2 = GitHubEvent(event_id="stat-2", event_type="test", action="test", source="test", repository="test/repo", signature_valid=True)

    await bus.publish(event1)
    await bus.publish(event2)
    await bus.publish(event1)  # 重复事件

    stats = bus.get_stats()
    assert stats["published_count"] == 2
    assert stats["duplicate_count"] == 1
    assert stats["dropped_count"] == 0
    assert stats["queue_size"] == 2
    assert "uptime_seconds" in stats
