"""版本管理单元测试"""
import pytest
from unittest.mock import patch, MagicMock
from src.utils.version_manager import ensure_repo_with_version


@pytest.mark.asyncio
@patch("src.utils.version_manager._ensure_repo_sync")
async def test_returns_path_and_not_degraded(mock_sync):
    mock_sync.return_value = ("/tmp/repo", False)
    path, degraded = await ensure_repo_with_version(
        repo_url="https://github.com/test/repo.git",
        local_path="/tmp/repo",
        version="abc123",
    )
    assert path == "/tmp/repo"
    assert degraded is False


@pytest.mark.asyncio
@patch("src.utils.version_manager._ensure_repo_sync")
async def test_returns_degraded_when_checkout_fails(mock_sync):
    mock_sync.return_value = ("/tmp/repo", True)
    path, degraded = await ensure_repo_with_version(
        repo_url="https://github.com/test/repo.git",
        local_path="/tmp/repo",
        version="nonexistent",
        branch="main",
    )
    assert degraded is True
