"""版本管理单元测试"""
import pytest
from src.utils.version_manager import VERSION_UNKNOWN, is_version_known


def test_version_unknown_constants():
    assert "unknown" in VERSION_UNKNOWN
    assert "" in VERSION_UNKNOWN
    assert None in VERSION_UNKNOWN


def test_is_version_known_with_sha():
    assert is_version_known("a1b2c3d4e5f6") is True


def test_is_version_known_with_unknown():
    assert is_version_known("unknown") is False


def test_is_version_known_with_empty():
    assert is_version_known("") is False


def test_is_version_known_with_none():
    assert is_version_known(None) is False
