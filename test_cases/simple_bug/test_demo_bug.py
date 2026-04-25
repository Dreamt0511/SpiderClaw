import pytest
from demo_bug import divide

def test_divide_normal():
    assert divide(10, 2) == 5

def test_divide_zero():
    assert divide(10, 0) is None
