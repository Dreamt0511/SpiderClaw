"""GitHub Webhook服务单元测试"""
import hashlib
import hmac
import json
from datetime import datetime
from fastapi.testclient import TestClient
import pytest
from src.bus import EventBus, GitHubEvent
from src.monitor.webhook_server import GitHubWebhookMonitor


@pytest.fixture
def test_secret():
    return "test-secret-123"


@pytest.fixture
def event_bus():
    return EventBus(maxsize=10)


@pytest.fixture
def webhook_monitor(test_secret, event_bus):
    return GitHubWebhookMonitor(
        event_bus=event_bus,
        secret=test_secret,
        host="127.0.0.1",
        port=8000
    )


@pytest.fixture
def client(webhook_monitor):
    return TestClient(webhook_monitor.app)


def test_health_check(client):
    """测试健康检查端点"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "queue_size" in data
    assert "uptime_seconds" in data


def test_webhook_missing_headers(client):
    """测试缺少必要请求头的情况"""
    response = client.post("/webhook/github", json={})
    assert response.status_code == 400
    assert "Missing required GitHub headers" in response.text


def test_webhook_invalid_signature(client, test_secret):
    """测试无效签名"""
    payload = {"action": "completed"}
    headers = {
        "X-GitHub-Delivery": "test-123",
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": "sha256=invalid-signature"
    }

    response = client.post("/webhook/github", json=payload, headers=headers)
    assert response.status_code == 403
    assert "Invalid signature" in response.text


def test_webhook_valid_signature(client, test_secret, event_bus):
    """测试有效签名的事件处理"""
    payload = {
        "action": "completed",
        "repository": {
            "full_name": "owner/repo",
            "clone_url": "https://github.com/owner/repo.git"
        },
        "workflow_run": {
            "head_branch": "main",
            "conclusion": "failure",
            "logs_url": "https://github.com/owner/repo/actions/runs/123/logs"
        }
    }

    # 计算正确的签名
    payload_bytes = json.dumps(payload).encode()
    signature = hmac.new(test_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    headers = {
        "X-GitHub-Delivery": "test-valid-123",
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": f"sha256={signature}",
        "Content-Type": "application/json"
    }

    response = client.post(
        "/webhook/github",
        content=payload_bytes,  # 使用content发送原始bytes，避免json参数自动格式化
        headers=headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert event_bus.qsize() == 1


def test_webhook_unsupported_event_type(client, test_secret):
    """测试不支持的事件类型"""
    payload = {"action": "created"}
    payload_bytes = json.dumps(payload).encode()
    signature = hmac.new(test_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    headers = {
        "X-GitHub-Delivery": "test-unsupported",
        "X-GitHub-Event": "issues",  # 不支持的事件类型
        "X-Hub-Signature-256": f"sha256={signature}"
    }

    response = client.post(
        "/webhook/github",
        json=payload,
        headers=headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_duplicate_event(client, test_secret, event_bus):
    """测试重复事件处理"""
    payload = {
        "action": "completed",
        "repository": {
            "full_name": "owner/repo",
            "clone_url": "https://github.com/owner/repo.git"
        },
        "workflow_run": {
            "head_branch": "main",
            "conclusion": "failure",
            "logs_url": "https://github.com/owner/repo/actions/runs/123/logs"
        }
    }
    payload_bytes = json.dumps(payload).encode()
    signature = hmac.new(test_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    headers = {
        "X-GitHub-Delivery": "test-duplicate-123",
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": f"sha256={signature}",
        "Content-Type": "application/json"
    }

    # 第一次请求
    response1 = client.post("/webhook/github", content=payload_bytes, headers=headers)
    assert response1.status_code == 200
    assert event_bus.qsize() == 1

    # 第二次相同事件ID的请求
    response2 = client.post("/webhook/github", content=payload_bytes, headers=headers)
    assert response2.status_code == 200  # 重复事件返回200
    assert event_bus.qsize() == 1  # 队列大小不变


@pytest.mark.asyncio
async def test_webhook_queue_full(client, test_secret, event_bus):
    """测试队列满的情况"""
    # 填满队列
    for i in range(10):
        event = GitHubEvent(
            event_id=f"fill-{i}",
            event_type="test",
            action="test",
            source="test",
            repository="test/repo",
            signature_valid=True
        )
        await event_bus.publish(event)

    assert event_bus.qsize() == 10

    # 发送新的事件，应该返回503
    payload = {
        "action": "completed",
        "repository": {
            "full_name": "owner/repo",
            "clone_url": "https://github.com/owner/repo.git"
        },
        "workflow_run": {
            "head_branch": "main",
            "conclusion": "failure",
            "logs_url": "https://github.com/owner/repo/actions/runs/123/logs"
        }
    }
    payload_bytes = json.dumps(payload).encode()
    signature = hmac.new(test_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    headers = {
        "X-GitHub-Delivery": "test-full-queue",
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": f"sha256={signature}",
        "Content-Type": "application/json"
    }

    response = client.post("/webhook/github", content=payload_bytes, headers=headers)
    assert response.status_code == 503
    assert "Service busy" in response.text


def test_event_conversion_workflow_run(webhook_monitor):
    """测试workflow_run事件转换"""
    payload = {
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

    event = webhook_monitor._convert_to_internal_event(
        event_id="test-workflow-123",
        event_type="workflow_run",
        payload=payload,
        signature_valid=True
    )

    assert event.event_type == "workflow_run"
    assert event.action == "completed"
    assert event.repository == "owner/repo"
    assert event.clone_url == "https://github.com/owner/repo.git"
    assert event.branch == "feature/test"
    assert event.conclusion == "failure"
    assert event.logs_url == "https://github.com/owner/repo/actions/runs/123/logs"
    assert event.pr_number is None


def test_event_conversion_pull_request(webhook_monitor):
    """测试pull_request事件转换"""
    payload = {
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

    event = webhook_monitor._convert_to_internal_event(
        event_id="test-pr-123",
        event_type="pull_request",
        payload=payload,
        signature_valid=True
    )

    assert event.event_type == "pull_request"
    assert event.action == "opened"
    assert event.branch == "feature/pr-123"
    assert event.pr_number == 123
    assert event.conclusion == "open"


def test_event_conversion_check_run(webhook_monitor):
    """测试check_run事件转换"""
    payload = {
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

    event = webhook_monitor._convert_to_internal_event(
        event_id="test-check-123",
        event_type="check_run",
        payload=payload,
        signature_valid=True
    )

    assert event.event_type == "check_run"
    assert event.action == "completed"
    assert event.branch == "main"
    assert event.pr_number == 456
    assert event.conclusion == "failure"
    assert event.logs_url == "https://github.com/owner/repo/runs/123"
