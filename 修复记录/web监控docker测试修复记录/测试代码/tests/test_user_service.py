"""user_service 模块测试 — 会触发 bug"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import logger  # noqa: F401 — 初始化日志
from src.user_service import get_user, get_user_email, create_user, delete_user


def test_get_user_existing():
    user = get_user(1)
    assert user["name"] == "Alice"


def test_get_user_not_found():
    """触发 KeyError: user_id=999 不存在"""
    user = get_user(999)
    assert user is None


def test_get_user_email_existing():
    email = get_user_email(2)
    assert email == "bob@example.com"


def test_get_user_email_not_found():
    """触发 KeyError: user_id=999 不存在"""
    email = get_user_email(999)
    assert email is None


def test_create_user():
    new_id = create_user("Charlie", "charlie@example.com", "user")
    assert new_id == 3


def test_create_user_duplicate_email():
    """bug: 未检查 email 唯一性，重复 email 静默成功"""
    new_id = create_user("Dave", "alice@example.com", "user")
    user = get_user(new_id)
    assert user["email"] != "alice@example.com"  # 预期失败: 实际是 alice@example.com


def test_delete_user_existing():
    # 先创建再删除
    new_id = create_user("Temp", "temp@example.com", "user")
    result = delete_user(new_id)
    assert result is True


def test_delete_user_not_found():
    """触发 KeyError: user_id=999 不存在"""
    result = delete_user(999)
    assert result is False
