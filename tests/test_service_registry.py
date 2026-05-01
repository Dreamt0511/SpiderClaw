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
    registry = ServiceRegistry(services_yaml)
    assert len(registry.list_services()) == 2
    assert "order-service" in registry.list_services()


def test_get_existing_service(services_yaml):
    registry = ServiceRegistry(services_yaml)
    svc = registry.get("order-service")
    assert svc is not None
    assert svc.repo_url == "https://github.com/test/order.git"
    assert svc.path_mapping == {"/app/": "src/"}


def test_get_nonexistent_service(services_yaml):
    registry = ServiceRegistry(services_yaml)
    assert registry.get("nonexistent") is None


def test_rate_limit_config(services_yaml):
    registry = ServiceRegistry(services_yaml)
    assert registry.rate_limit.max_fixes_per_minute == 5
    assert registry.rate_limit.max_fixes_per_hour == 30


def test_empty_config(tmp_path):
    path = tmp_path / "services.yaml"
    with open(path, "w") as f:
        f.write("services: []\n")
    registry = ServiceRegistry(str(path))
    assert registry.list_services() == []
