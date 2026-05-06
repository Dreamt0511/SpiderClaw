"""calculator 模块测试 — 会触发 bug"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import logger  # noqa: F401 — 初始化日志
from src.calculator import divide, average, discount, sqrt_approx


def test_divide_normal():
    assert divide(10, 2) == 5.0


def test_divide_by_zero():
    """触发 ZeroDivisionError"""
    result = divide(10, 0)
    assert result == float('inf')


def test_average_normal():
    assert average([1, 2, 3, 4, 5]) == 3.0


def test_average_empty_list():
    """触发 ZeroDivisionError: 空列表"""
    result = average([])
    assert result == 0


def test_discount_normal():
    result = discount(100, 0.2)
    assert result == 80.0


def test_discount_negative_rate():
    """rate 为负数时产生错误结果"""
    result = discount(100, -0.5)
    assert result < 100  # 预期失败: 实际是 150


def test_sqrt_normal():
    result = sqrt_approx(4)
    assert abs(result - 2.0) < 0.001


def test_sqrt_negative():
    """触发异常: 负数的平方根"""
    result = sqrt_approx(-1)
    assert isinstance(result, float)
