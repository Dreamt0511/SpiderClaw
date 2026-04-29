"""测试 code_context 模块：错误相关代码片段提取"""

from src.agent.code_context import build_error_context_section, _extract_window

SAMPLE_FUNCTION_CODE = """import os
import sys

def helper():
    return "ok"

def process_data(items, threshold=10):
    \"\"\"Process items with threshold filtering.\"\"\"
    result = []
    for item in items:
        if item > threshold:
            result.append(item)
    return result

class Calculator:
    \"\"\"Simple calculator.\"\"\"

    def add(self, a, b):
        return a + b

    def divide(self, a, b):
        if b == 0:
            raise ValueError("Division by zero")
        return a / b

def main():
    calc = Calculator()
    return calc.add(1, 2)
"""

SYNTAX_ERROR_CODE = """def broken(
    pass
"""

MODULE_LEVEL_CODE = """import os
import sys
from typing import List

APP_NAME = "myapp"
DEBUG = True

def run():
    pass
"""


# ==================== ErrorLocation mock helpers ====================

def make_err(file_path: str, line_number: int,
             error_type: str = "NameError",
             error_message: str = "name 'x' is not defined",
             is_root_cause: bool = False) -> dict:
    return {
        "file_path": file_path,
        "line_number": line_number,
        "error_type": error_type,
        "error_message": error_message,
        "is_root_cause": is_root_cause,
        "traceback": "",
        "source": "",
    }


# ==================== 测试: 函数体提取 ====================

class TestFunctionExtraction:
    """错误在函数内部时，应提取完整函数体"""

    def test_single_function_error(self):
        """函数内错误 → 输出包含完整函数体和错误标记"""
        result = build_error_context_section(
            original_codes={"test.py": SAMPLE_FUNCTION_CODE},
            error_locations=[make_err("test.py", 25)],  # main() 函数内
            target_files=["test.py"],
        )
        assert "main" in result, "应包含函数名 main"
        assert "Calculator" in result, "应包含类名（同一文件内）"
        assert "def main()" in result, "应包含函数签名"
        assert "process_data" not in result, "不应包含无关函数 process_data"
        assert "```python" in result, "应包含代码块标记"
        assert "read_file" in result, "应包含 read_file 提示"

    def test_multiple_errors_same_function(self):
        """同一函数的多个错误 → 函数只输出一次"""
        result = build_error_context_section(
            original_codes={"test.py": SAMPLE_FUNCTION_CODE},
            error_locations=[
                make_err("test.py", 10, "TypeError", "unsupported operand"),
                make_err("test.py", 12, "TypeError", "can't multiply"),
            ],
            target_files=["test.py"],
        )
        # process_data 是包含行 10 和 12 的函数
        assert "process_data" in result
        # 只应包含一次 process_data 定义
        assert result.count("def process_data") == 1
        assert "L10" in result or "L12" in result


# ==================== 测试: 类+方法提取 ====================

class TestClassExtraction:
    """错误在类方法内时，应提取类头部 + 受影响的方法"""

    def test_method_error(self):
        """类方法内错误 → 输出方法体（函数级提取优先于类级）"""
        result = build_error_context_section(
            original_codes={"test.py": SAMPLE_FUNCTION_CODE},
            error_locations=[make_err("test.py", 21)],  # Calculator.divide
            target_files=["test.py"],
        )
        # divide 作为独立函数被提取（函数级优先于类级）
        assert "def divide" in result
        assert "NameError" in result


# ==================== 测试: 模块级窗口 ====================

class TestModuleLevelExtraction:
    """模块级错误（不在任何函数/类内）→ 行级窗口"""

    def test_module_level_error(self):
        """模块级错误 → ±8 行窗口"""
        result = build_error_context_section(
            original_codes={"test.py": MODULE_LEVEL_CODE},
            error_locations=[make_err("test.py", 4)],  # APP_NAME 行
            target_files=["test.py"],
        )
        assert "APP_NAME" in result
        assert "DEBUG" in result  # 在窗口内
        assert "```python" in result

    def test_window_overlap_merge(self):
        """相近的多个错误行 → 窗口应合并"""
        source_lines = [f"line_{i}" for i in range(1, 50)]
        result = _extract_window(source_lines, [10, 12, 15], context_lines=3)
        # 10-3=7 到 15+3=18 应该合并为一个连续块
        assert "L10" in result
        assert "L15" in result
        # 中间不应有分隔（合并了）
        assert "\n\n" not in result


# ==================== 测试: 语法错误回退 ====================

class TestSyntaxErrorFallback:
    """文件有语法错误（AST 解析失败）→ 行级窗口回退"""

    def test_syntax_error_fallback(self):
        """语法错误文件 → 行级窗口而非崩溃"""
        result = build_error_context_section(
            original_codes={"broken.py": SYNTAX_ERROR_CODE},
            error_locations=[make_err("broken.py", 1, "SyntaxError", "invalid syntax")],
            target_files=["broken.py"],
        )
        assert "```python" in result
        assert "语法错误" in result or "行级" in result or "⚠️" in result


# ==================== 测试: 边界情况 ====================

class TestEdgeCases:

    def test_empty_source(self):
        """空文件 → 占位标记"""
        result = build_error_context_section(
            original_codes={"empty.py": ""},
            error_locations=[make_err("empty.py", 1)],
            target_files=["empty.py"],
        )
        assert "空" in result or result == "" or "```python" in result

    def test_no_target_files(self):
        """无 target_files → 返回空"""
        result = build_error_context_section(
            original_codes={"test.py": SAMPLE_FUNCTION_CODE},
            error_locations=[make_err("test.py", 25)],
            target_files=[],
        )
        assert result == ""

    def test_line_number_zero(self):
        """行号为 0 → 不会崩溃"""
        result = build_error_context_section(
            original_codes={"test.py": SAMPLE_FUNCTION_CODE},
            error_locations=[make_err("test.py", 0)],
            target_files=["test.py"],
        )
        assert result == "" or "```python" in result

    def test_file_not_in_error_locations(self):
        """存在 target_file 但不在 error_locations 中 → 简短注释或跳过"""
        result = build_error_context_section(
            original_codes={"no_error.py": "x = 1"},
            error_locations=[make_err("other.py", 1)],
            target_files=["no_error.py", "other.py"],
        )
        # no_error.py 不在 error_locations 中，应被跳过或有注释
        assert result == "" or "no_error" in result or "```python" in result


# ==================== 测试: 完整集成 ====================

class TestIntegration:
    """多文件混合场景"""

    def test_multiple_files(self):
        """多个文件，每个文件各有错误"""
        result = build_error_context_section(
            original_codes={
                "mod_a.py": SAMPLE_FUNCTION_CODE,
                "mod_b.py": MODULE_LEVEL_CODE,
            },
            error_locations=[
                make_err("mod_a.py", 25, "AttributeError"),
                make_err("mod_b.py", 4, "NameError", "name 'APP_NAME' not defined"),
            ],
            target_files=["mod_a.py", "mod_b.py"],
        )
        assert "mod_a.py" in result
        assert "mod_b.py" in result
        assert "```python" in result
        # 每个文件只展示与错误相关的代码，不是完整文件
        # mod_a.py 展示模块级窗口（含 add/divide/main），mod_b.py 展示模块级窗口（含 run）
        assert result.count("def ") <= 5

    def test_mixed_error_types(self):
        """混合的根因和非根因错误 → 根因有特殊标记"""
        result = build_error_context_section(
            original_codes={"test.py": SAMPLE_FUNCTION_CODE},
            error_locations=[
                make_err("test.py", 10, "TypeError", error_message="root cause", is_root_cause=True),
                make_err("test.py", 25, "AttributeError", error_message="consequence"),
            ],
            target_files=["test.py"],
        )
        # 根因应有关键标记
        assert "根因" in result
