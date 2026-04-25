#!/usr/bin/env python3
"""Webhook服务功能测试脚本"""
import asyncio
import json
import hashlib
import hmac
from fastapi.testclient import TestClient
from src.bus import get_event_bus
from src.monitor.webhook_server import GitHubWebhookMonitor


async def test_webhook_flow():
    """测试完整的Webhook处理流程"""
    # 初始化
    secret = "test-secret-123"
    event_bus = get_event_bus(maxsize=10)
    monitor = GitHubWebhookMonitor(
        event_bus=event_bus,
        secret=secret,
        host="127.0.0.1",
        port=8000
    )
    client = TestClient(monitor.app)

    print("[OK] 初始化Webhook服务成功")

    # 测试健康检查
    response = client.get("/health")
    assert response.status_code == 200
    health_data = response.json()
    assert health_data["status"] == "ok"
    print("[OK] 健康检查端点正常")

    # 测试Workflow Run事件
    workflow_payload = {
        "action": "completed",
        "repository": {
            "full_name": "owner/repo",
            "clone_url": "https://github.com/owner/repo.git"
        },
        "workflow_run": {
            "head_branch": "feature/test",
            "conclusion": "failure",
            "logs_url": "https://github.com/owner/repo/actions/runs/123/logs"
        }
    }

    payload_bytes = json.dumps(workflow_payload).encode()
    signature = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    headers = {
        "X-GitHub-Delivery": "test-workflow-123",
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": f"sha256={signature}",
        "Content-Type": "application/json"
    }

    response = client.post("/webhook/github", content=payload_bytes, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert event_bus.qsize() == 1
    print("[OK] Workflow Run事件处理成功")

    # 测试Pull Request事件
    pr_payload = {
        "action": "opened",
        "repository": {
            "full_name": "owner/repo",
            "clone_url": "https://github.com/owner/repo.git"
        },
        "pull_request": {
            "number": 123,
            "head": {"ref": "feature/pr-123"},
            "state": "open"
        }
    }

    payload_bytes = json.dumps(pr_payload).encode()
    signature = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    headers = {
        "X-GitHub-Delivery": "test-pr-123",
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": f"sha256={signature}",
        "Content-Type": "application/json"
    }

    response = client.post("/webhook/github", content=payload_bytes, headers=headers)
    assert response.status_code == 200
    assert event_bus.qsize() == 2
    print("[OK] Pull Request事件处理成功")

    # 测试Check Run事件
    check_payload = {
        "action": "completed",
        "repository": {
            "full_name": "owner/repo",
            "clone_url": "https://github.com/owner/repo.git"
        },
        "check_run": {
            "conclusion": "failure",
            "details_url": "https://github.com/owner/repo/runs/123",
            "check_suite": {"head_branch": "main"},
            "pull_requests": [{"number": 456}]
        }
    }

    payload_bytes = json.dumps(check_payload).encode()
    signature = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    headers = {
        "X-GitHub-Delivery": "test-check-123",
        "X-GitHub-Event": "check_run",
        "X-Hub-Signature-256": f"sha256={signature}",
        "Content-Type": "application/json"
    }

    response = client.post("/webhook/github", content=payload_bytes, headers=headers)
    assert response.status_code == 200
    assert event_bus.qsize() == 3
    print("[OK] Check Run事件处理成功")

    # 测试重复事件
    response = client.post("/webhook/github", content=payload_bytes, headers=headers)
    assert response.status_code == 200
    assert event_bus.qsize() == 3  # 队列大小不变
    print("[OK] 重复事件去重功能正常")

    # 测试无效签名
    headers["X-Hub-Signature-256"] = "sha256=invalid-signature"
    response = client.post("/webhook/github", content=payload_bytes, headers=headers)
    assert response.status_code == 403
    print("[OK] 无效签名验证正常")

    # 测试不支持的事件类型
    headers = {
        "X-GitHub-Delivery": "test-issues-123",
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": f"sha256={signature}",
        "Content-Type": "application/json"
    }
    response = client.post("/webhook/github", content=payload_bytes, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    print("[OK] 不支持的事件类型过滤正常")

    # 消费事件，验证内容
    print("\n[INFO] 事件内容验证:")
    for i in range(3):
        event = await event_bus.subscribe()
        print(f"  事件 {i+1}: {event.event_type}, ID: {event.event_id}")
        print(f"    仓库: {event.repository}")
        print(f"    分支: {event.branch}")
        if event.pr_number:
            print(f"    PR编号: {event.pr_number}")
        if event.logs_url:
            print(f"    日志URL: {event.logs_url}")
        print(f"    结果: {event.conclusion}")

    print("\n[SUCCESS] 所有测试通过！Webhook服务功能正常。")

    # 打印统计信息
    stats = event_bus.get_stats()
    print(f"\n[STATS] 统计信息:")
    print(f"  已发布事件: {stats['published_count']}")
    print(f"  重复事件: {stats['duplicate_count']}")
    print(f"  丢弃事件: {stats['dropped_count']}")
    print(f"  队列当前大小: {stats['queue_size']}")


if __name__ == "__main__":
    asyncio.run(test_webhook_flow())
