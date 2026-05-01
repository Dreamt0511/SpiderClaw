"""路径映射单元测试"""
import pytest
from src.utils.path_mapping import apply_path_mapping


def test_basic_mapping():
    mapping = {"/app/": "src/"}
    assert apply_path_mapping("/app/services/order.py", mapping) == "src/services/order.py"


def test_longest_prefix_wins():
    mapping = {"/app/": "src/", "/app/services/": "src/core/"}
    assert apply_path_mapping("/app/services/order.py", mapping) == "src/core/order.py"


def test_no_mapping_returns_original():
    mapping = {"/app/": "src/"}
    assert apply_path_mapping("services/order.py", mapping) == "services/order.py"


def test_empty_mapping():
    assert apply_path_mapping("/app/order.py", {}) == "/app/order.py"


def test_absolute_path_no_match():
    mapping = {"/app/": "src/"}
    assert apply_path_mapping("/other/order.py", mapping) == "/other/order.py"


def test_multiple_mappings():
    mapping = {"/app/": "src/", "/shared-lib/": "lib/"}
    assert apply_path_mapping("/shared-lib/utils.py", mapping) == "lib/utils.py"


def test_mapping_preserves_suffix():
    mapping = {"/app/": "src/"}
    assert apply_path_mapping("/app/a/b/c.py", mapping) == "src/a/b/c.py"
