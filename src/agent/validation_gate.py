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
    max_change_lines: int = 50,
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
    result = _check_file_scope(fix_result, errors)
    if not result.passed:
        return result

    result = _check_import_error(fix_result, original_codes, errors)
    if not result.passed:
        return result

    result = _check_syntax_error(fix_result, original_codes, errors)
    if not result.passed:
        return result

    result = _check_func_scope(fix_result, original_codes, errors)
    if not result.passed:
        return result

    result = _check_change_limit(fix_result, original_codes, max_change_lines)
    if not result.passed:
        return result

    return ValidationResult(passed=True)


def _check_file_scope(
    fix_result: dict,
    error_locations: list[dict],
) -> ValidationResult:
    """检查修改的文件是否在错误列表中"""
    allowed_files = set()
    for e in error_locations:
        fp = e.get("file_path", "")
        if fp and fp != "<string>":
            allowed_files.add(fp)

    if not allowed_files:
        return ValidationResult(passed=True)

    modified_files = set(fix_result.get("modified_files", []))
    invalid_files = modified_files - allowed_files

    if invalid_files:
        return ValidationResult(
            passed=False,
            violation_type="wrong_file_modified",
            details=f"修改了不在错误列表中的文件: {', '.join(invalid_files)}",
            error_context={"invalid_files": list(invalid_files)},
        )

    return ValidationResult(passed=True)


def _check_import_error(
    fix_result: dict,
    original_codes: dict[str, str],
    error_locations: list[dict],
) -> ValidationResult:
    """纯导入错误：去导入内容比较 + 语义变更阈值

    比较策略：移除所有 import/from 导入行后，对比核心业务代码的差异。
    允许 ≤3 行的噪音变化（空白符、注释位置等 LLM 输出不稳定性），
    超过则判定为越界修改。
    """
    if not error_locations:
        return ValidationResult(passed=True)

    if not all(
        e.get("error_type") in ("ModuleNotFoundError", "ImportError")
        for e in error_locations
    ):
        return ValidationResult(passed=True)

    for fp, new_code in fix_result.get("code_changes", {}).items():
        orig = original_codes.get(fp, "")
        if not orig:
            continue

        orig_core = _strip_import_lines(orig)
        new_core = _strip_import_lines(new_code)

        if orig_core == new_core:
            continue

        changed = _count_semantic_changes(orig_core, new_core)
        if changed > 3:
            logger.warning(
                f"ImportError 越界修改: 核心代码差异 {changed} 行（阈值 3）"
            )
            return ValidationResult(
                passed=False,
                violation_type="import_line_violation",
                details=f"核心代码变更 {changed} 行，超过允许的 3 行阈值",
            )

    return ValidationResult(passed=True)


def _strip_import_lines(code: str) -> list[str]:
    """移除所有 import 行，返回剩余的非空、非纯注释的代码行"""
    result = []
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("import ") or stripped.startswith("from "):
            continue
        result.append(stripped)
    return result


def _count_semantic_changes(orig_lines: list[str], new_lines: list[str]) -> int:
    """计算两个核心代码列表之间的差异行数（近似编辑距离）"""
    matcher = difflib.SequenceMatcher(None, orig_lines, new_lines)
    diff_lines = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            diff_lines += max(i2 - i1, j2 - j1)
        elif tag == "delete":
            diff_lines += i2 - i1
        elif tag == "insert":
            diff_lines += j2 - j1
    return diff_lines


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


def _check_change_limit(
    fix_result: dict,
    original_codes: dict[str, str],
    max_allowed: int = 50,
) -> ValidationResult:
    """检查总修改行数是否超过上限，防止过度修复"""
    for fp, new_code in fix_result.get("code_changes", {}).items():
        orig = original_codes.get(fp, "")
        if not orig:
            continue

        diff = list(difflib.unified_diff(
            orig.splitlines(keepends=True),
            new_code.splitlines(keepends=True),
            n=0,
        ))
        added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
        total = added + removed

        if total > max_allowed:
            logger.warning(
                f"修改行数超限: {fp} 变动 {total} 行（+{added}/-{removed}），"
                f"超过上限 {max_allowed}"
            )
            return ValidationResult(
                passed=False,
                violation_type="change_limit_exceeded",
                details=f"文件 {fp} 变动 {total} 行（+{added}/-{removed}），超过上限 {max_allowed} 行",
            )

    return ValidationResult(passed=True)


def _is_import_line(code_line: str) -> bool:
    """判断一行是否为 import 语句"""
    stripped = code_line.strip()
    return stripped.startswith("import ") or stripped.startswith("from ")


def _is_nameerror_only(errors: list[dict]) -> bool:
    """判断所有错误是否都是 NameError"""
    return all(e.get("error_type") == "NameError" for e in errors)


def _check_func_scope(
    fix_result: dict,
    original_codes: dict[str, str],
    error_locations: list[dict],
) -> ValidationResult:
    """函数级错误：修改必须在出错函数 AST 节点内

    例外：NameError 允许在模块级添加 import 语句（这是正确修复方式）
    """
    func_errors = [
        e for e in error_locations
        if e.get("error_type") in (
            "NameError", "TypeError", "ValueError",
            "AttributeError", "KeyError", "IndexError",
        )
    ]
    if not func_errors:
        return ValidationResult(passed=True)

    is_name_err = _is_nameerror_only(func_errors)

    for fp, new_code in fix_result.get("code_changes", {}).items():
        orig = original_codes.get(fp, "")
        if not orig:
            continue

        try:
            orig_ast = ast.parse(orig)
        except SyntaxError:
            continue

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
                    # NameError 允许在模块级添加 import 语句
                    if is_name_err and changed_lineno < func_start:
                        new_lines = new_code.splitlines()
                        if 1 <= changed_lineno <= len(new_lines):
                            changed_line = new_lines[changed_lineno - 1]
                            if _is_import_line(changed_line):
                                continue  # 允许：NameError 需要添加 import
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
