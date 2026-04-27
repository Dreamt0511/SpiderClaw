"""后置硬校验门禁 — 在修复输出后、文件写入前检查修改范围"""

import ast
import difflib
import logging
import re
from typing import Any

from pydantic import BaseModel

from src.agent.state import ErrorLocation

logger = logging.getLogger(__name__)


class ValidationResult(BaseModel):
    """校验结果"""
    passed: bool
    violation_type: str = ""     # import_line_violation | syntax_line_violation | func_body_modified
    details: str = ""
    error_context: dict[str, Any] = {}  # 用于构建强制指令的上下文


def validate_fix(
    fix_result: dict,
    original_codes: dict[str, str],
    error_locations: list[ErrorLocation],
) -> ValidationResult:
    """
    后置硬校验：检查修复是否符合错误类型的边界约束。

    返回 ValidationResult(passed=True) 放行，否则拦截。
    """
    if not fix_result.get("code_changes"):
        return ValidationResult(passed=True)

    # 如果标记为环境错误，跳过校验
    if fix_result.get("is_env_error"):
        return ValidationResult(passed=True)

    # 将 ErrorLocation 转为 dict（兼容内部处理）
    errors = []
    for e in error_locations:
        if isinstance(e, ErrorLocation):
            errors.append({
                "file_path": e.file_path,
                "line_number": e.line_number,
                "error_type": e.error_type,
                "error_message": e.error_message,
            })
        else:
            errors.append(e)

    # 逐策略检查
    result = _check_import_error(fix_result, original_codes, errors)
    if not result.passed:
        return result

    result = _check_syntax_error(fix_result, original_codes, errors)
    if not result.passed:
        return result

    result = _check_func_scope(fix_result, original_codes, errors)
    if not result.passed:
        return result

    return ValidationResult(passed=True)


def _check_import_error(
    fix_result: dict,
    original_codes: dict[str, str],
    error_locations: list[dict],
) -> ValidationResult:
    """纯导入错误：只允许修改 import/from 行"""
    if not error_locations:
        return ValidationResult(passed=True)

    if not all(
        e.get("error_type") in ("ModuleNotFoundError", "ImportError")
        for e in error_locations
    ):
        return ValidationResult(passed=True)  # 非纯导入错误，跳过

    for fp, new_code in fix_result.get("code_changes", {}).items():
        orig = original_codes.get(fp, "")
        if not orig:
            continue

        diff = difflib.unified_diff(
            orig.splitlines(keepends=True),
            new_code.splitlines(keepends=True),
            n=0,
        )
        for line in diff:
            if line.startswith("+") and not line.startswith("+++"):
                content = line[1:].strip()
                if not content or content.startswith("#"):
                    continue  # 空行和注释放行
                if content.startswith("import ") or content.startswith("from "):
                    continue  # 导入行放行
                if content in ("try:", "except ImportError:", "except ModuleNotFoundError:"):
                    continue  # try/except 框架行放行

                logger.warning(f"ImportError 越界修改: {content}")
                return ValidationResult(
                    passed=False,
                    violation_type="import_line_violation",
                    details=f"越界修改行: {line.strip()}",
                )

    return ValidationResult(passed=True)


def _check_syntax_error(
    fix_result: dict,
    original_codes: dict[str, str],
    error_locations: list[dict],
) -> ValidationResult:
    """语法错误：只允许修改错误行 ±3 行范围"""
    syntax_errors = [
        e for e in error_locations
        if e.get("error_type") in ("SyntaxError", "IndentationError", "TabError")
    ]
    if not syntax_errors:
        return ValidationResult(passed=True)

    allowed_ranges = []
    for e in syntax_errors:
        ln = e.get("line_number", 0)
        if ln > 0:
            allowed_ranges.append((ln - 3, ln + 3))

    if not allowed_ranges:
        return ValidationResult(passed=True)

    for fp, new_code in fix_result.get("code_changes", {}).items():
        orig = original_codes.get(fp, "")
        if not orig:
            continue

        diff = list(difflib.unified_diff(
            orig.splitlines(keepends=True),
            new_code.splitlines(keepends=True),
            n=0,
        ))
        line_num = 0
        for line in diff:
            if line.startswith("@@"):
                match = re.search(r"\+(\d+)", line)
                if match:
                    line_num = int(match.group(1)) - 1
            elif line.startswith("+") and not line.startswith("+++"):
                line_num += 1
                content = line[1:].strip()
                if not content:
                    continue
                if not any(lo <= line_num <= hi for lo, hi in allowed_ranges):
                    logger.warning(
                        f"SyntaxError 越界: L{line_num} 不在允许范围 {allowed_ranges}"
                    )
                    return ValidationResult(
                        passed=False,
                        violation_type="syntax_line_violation",
                        details=f"L{line_num} 超出允许范围（允许: {allowed_ranges}）",
                        error_context={"error_lines": str([lo for lo, _ in allowed_ranges])},
                    )
            elif not line.startswith("-"):
                line_num += 1

    return ValidationResult(passed=True)


def _check_func_scope(
    fix_result: dict,
    original_codes: dict[str, str],
    error_locations: list[dict],
) -> ValidationResult:
    """函数级错误：修改必须在出错函数 AST 节点内"""
    func_errors = [
        e for e in error_locations
        if e.get("error_type") in (
            "NameError", "TypeError", "ValueError",
            "AttributeError", "KeyError", "IndexError",
        )
    ]
    if not func_errors:
        return ValidationResult(passed=True)

    for fp, new_code in fix_result.get("code_changes", {}).items():
        orig = original_codes.get(fp, "")
        if not orig:
            continue

        try:
            orig_ast = ast.parse(orig)
            new_ast = ast.parse(new_code)
        except SyntaxError:
            continue  # 语法错误交给 _check_syntax_error

        for e in func_errors:
            if e.get("file_path") != fp or e.get("line_number", 0) <= 0:
                continue

            func_node = _find_enclosing_function(orig_ast, e["line_number"])
            if func_node is None:
                continue

            changed_line_nums = _get_changed_line_numbers(orig, new_code)
            func_start = func_node.lineno
            func_end = _get_func_end(func_node)

            for changed_lineno in changed_line_nums:
                if changed_lineno < func_start or changed_lineno > func_end:
                    logger.warning(
                        f"函数范围越界: L{changed_lineno} 不在函数 {func_node.name} "
                        f"范围 ({func_start}-{func_end})"
                    )
                    return ValidationResult(
                        passed=False,
                        violation_type="func_body_modified",
                        details=f"修改 L{changed_lineno} 超出函数 {func_node.name} 范围",
                        error_context={"func_name": func_node.name},
                    )

    return ValidationResult(passed=True)


def _find_enclosing_function(tree: ast.AST, lineno: int) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """找到包含指定行的最内层函数节点"""
    result = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_end = _get_func_end(node)
            if node.lineno <= lineno <= func_end:
                if result is None or node.lineno > result.lineno:
                    result = node  # 取最内层
    return result


def _get_func_end(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """获取函数体的结束行号"""
    if func_node.body:
        last = func_node.body[-1]
        return getattr(last, "end_lineno", last.lineno)
    return func_node.lineno


def _get_changed_line_numbers(orig_code: str, new_code: str) -> list[int]:
    """从 diff 中提取修改的行号"""
    changed = []
    diff = list(difflib.unified_diff(
        orig_code.splitlines(keepends=True),
        new_code.splitlines(keepends=True),
        n=0,
    ))
    line_num = 0
    for line in diff:
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            if match:
                line_num = int(match.group(1)) - 1
        elif line.startswith("+") and not line.startswith("+++"):
            line_num += 1
            changed.append(line_num)
        elif not line.startswith("-"):
            line_num += 1
    return changed
