"""/webhook/log 端点集成测试"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from src.monitor.webhook_server import GitHubWebhookMonitor
from src.bus.schemas import RuntimeLogEvent


@pytest.fixture
def mock_event_bus():
    """模拟 EventBus，publish 返回 True"""
    bus = MagicMock()
    bus.publish = AsyncMock(return_value=True)
    bus.get_stats.return_value = {
        "queue_size": 0,
        "published_count": 0,
        "dropped_count": 0,
        "duplicate_count": 0,
        "processed_ids_count": 0,
        "uptime_seconds": 0,
    }
    return bus


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
    return mock_registry


@pytest.fixture
def client(mock_event_bus):
    """创建 TestClient，使用 mock event_bus"""
    monitor = GitHubWebhookMonitor(
        event_bus=mock_event_bus,
        secret="test-secret",
    )
    return TestClient(monitor.app)


def test_receive_log_success(client, mock_event_bus, mock_service_registry):
    """正常接收日志事件 → 200 + accepted"""
    with patch(
        "src.monitor.webhook_server.get_service_registry",
        return_value=mock_service_registry,
    ):
        response = client.post(
            "/webhook/log",
            json={"log": "TypeError: ...", "service": "test-service"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert "event_id" in data

    # 验证事件已发布到总线
    mock_event_bus.publish.assert_awaited_once()
    published_event = mock_event_bus.publish.call_args[0][0]
    assert isinstance(published_event, RuntimeLogEvent)
    assert published_event.log == "TypeError: ..."
    assert published_event.service == "test-service"
    assert published_event.repo_url == "https://github.com/test/repo.git"
    assert published_event.branch == "main"


def test_receive_log_missing_log_field(client, mock_service_registry):
    """缺少 log 字段 → 400"""
    with patch(
        "src.monitor.webhook_server.get_service_registry",
        return_value=mock_service_registry,
    ):
        response = client.post(
            "/webhook/log",
            json={"service": "test-service"},
        )

    assert response.status_code == 400


def test_receive_log_missing_service_field(client, mock_service_registry):
    """缺少 service 字段 → 400"""
    with patch(
        "src.monitor.webhook_server.get_service_registry",
        return_value=mock_service_registry,
    ):
        response = client.post(
            "/webhook/log",
            json={"log": "some error"},
        )

    assert response.status_code == 400


def test_receive_log_empty_fields(client, mock_service_registry):
    """log 和 service 都为空 → 400"""
    with patch(
        "src.monitor.webhook_server.get_service_registry",
        return_value=mock_service_registry,
    ):
        response = client.post(
            "/webhook/log",
            json={"log": "", "service": ""},
        )

    assert response.status_code == 400


def test_receive_log_unknown_service(client, mock_event_bus):
    """未知服务 → 200 + unknown_service"""
    mock_registry = MagicMock()
    mock_registry.get.return_value = None

    with patch(
        "src.monitor.webhook_server.get_service_registry",
        return_value=mock_registry,
    ):
        response = client.post(
            "/webhook/log",
            json={"log": "some error", "service": "nonexistent"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unknown_service"
    assert data["service"] == "nonexistent"

    # 未知服务不应发布事件
    mock_event_bus.publish.assert_not_awaited()


def test_receive_log_with_optional_fields(client, mock_event_bus, mock_service_registry):
    """携带可选字段（version, hostname）→ 正常处理"""
    with patch(
        "src.monitor.webhook_server.get_service_registry",
        return_value=mock_service_registry,
    ):
        response = client.post(
            "/webhook/log",
            json={
                "log": "OOM killed",
                "service": "test-service",
                "version": "1.2.3",
                "hostname": "prod-server-01",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"

    published_event = mock_event_bus.publish.call_args[0][0]
    assert published_event.version == "1.2.3"
    assert published_event.hostname == "prod-server-01"


def test_receive_log_invalid_json(client):
    """无效 JSON → 400"""
    response = client.post(
        "/webhook/log",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
