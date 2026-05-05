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
    target_files: list[str] | None = None,
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
    # 注：文件级/函数级作用域检查已移除，Agent 可跨文件修复根因
    #     保留语法/导入/行数/文件完整性限制作为基本安全护栏
    result = _check_import_error(fix_result, original_codes, errors)
    if not result.passed:
        return result

    result = _check_syntax_error(fix_result, original_codes, errors)
    if not result.passed:
        return result

    result = _check_file_completeness(fix_result, errors, target_files)
    if not result.passed:
        return result

    result = _check_error_coverage(fix_result, original_codes, errors)
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
    """语法错误：只校验修复后代码能否通过 ast.parse()，能通过即放行"""
    syntax_files = set()
    for e in error_locations:
        if e.get("error_type") in ("SyntaxError", "IndentationError", "TabError"):
            fp = e.get("file_path", "")
            if fp:
                syntax_files.add(fp)

    if not syntax_files:
        return ValidationResult(passed=True)

    for fp, new_code in fix_result.get("code_changes", {}).items():
        if fp not in syntax_files:
            continue
        try:
            ast.parse(new_code)
            logger.info(f"语法修复验证通过: {fp}")
        except SyntaxError as e:
            logger.warning(f"语法修复后仍有错误: {fp}:{e}")
            return ValidationResult(
                passed=False,
                violation_type="syntax_line_violation",
                details=f"{fp}: 修复后仍有语法错误: {e}",
                error_context={"affected_file": fp, "error_lines": str(e.lineno or "")},
            )

    return ValidationResult(passed=True)


def _check_error_coverage(
    fix_result: dict,
    original_codes: dict[str, str],
    error_locations: list[dict],
) -> ValidationResult:
    """逐错误覆盖检查：确保每个错误位置的邻近代码都发生了变更

    当多个错误集中在同一文件时，_check_file_completeness 无法检测
    LLM 是否遗漏了部分错误。本函数检查每个 error_location 的
    ±5 行范围内是否有实际代码变更，防止"部分修复"通过验证。
    """
    if not error_locations:
        return ValidationResult(passed=True)

    code_changes = fix_result.get("code_changes", {})
    if not code_changes:
        return ValidationResult(passed=True)

    WINDOW = 3
    uncovered: list[dict] = []

    for err in error_locations:
        fp = err.get("file_path", "")
        ln = err.get("line_number", 0)
        if not fp or ln <= 0 or fp == "<string>":
            continue

        # 文件未被修改 → 直接标记为遗漏
        if fp not in code_changes:
            uncovered.append(err)
            continue

        orig = original_codes.get(fp, "")
        new_code = code_changes[fp]
        if not orig:
            continue

        orig_lines = orig.splitlines()
        new_lines = new_code.splitlines()

        # 检查错误行 ±WINDOW 窗口内是否有任何行变更
        window_start = max(0, ln - WINDOW - 1)  # 0-indexed
        window_end = min(len(orig_lines), ln + WINDOW)

        changed = False
        for i in range(window_start, window_end):
            if i >= len(new_lines):
                # 行数比原始更少 → 有删除
                changed = True
                break
            if orig_lines[i] != new_lines[i]:
                changed = True
                break

        # 检查1：函数开头新增参数校验（错误行在函数内部，函数前几行有新增）
        if not changed:
            # 尝试找到错误行所在的函数
            import ast
            try:
                tree = ast.parse(orig)
                func_node = None
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if node.lineno <= ln <= _get_func_end(node):
                            func_node = node
                            break
                if func_node:
                    # 函数前10行有变更 → 视为参数校验修改
                    func_start = func_node.lineno - 1  # 0-indexed
                    func_window_end = min(func_start + 10, len(orig_lines), ln)
                    for i in range(func_start, func_window_end):
                        if i >= len(new_lines) or orig_lines[i] != new_lines[i]:
                            changed = True
                            break
            except SyntaxError:
                pass

        # 检查2：异常处理逻辑修改（try/except块内的代码变更，即使错误行本身不变）
        if not changed:
            # 查找错误行附近的try-except块
            error_line_idx = ln - 1  # 0-indexed
            # 向上查找try关键字
            try_line_idx = -1
            for i in range(max(0, error_line_idx - 20), error_line_idx):
                if orig_lines[i].strip().startswith("try:"):
                    try_line_idx = i
                    break
            if try_line_idx != -1:
                # 向下查找except关键字
                except_line_idx = -1
                for i in range(try_line_idx, min(len(orig_lines), error_line_idx + 20)):
                    if orig_lines[i].strip().startswith("except"):
                        except_line_idx = i
                        break
                if except_line_idx != -1:
                    # 检查整个try-except块是否有变更
                    block_end = min(len(orig_lines), except_line_idx + 20)
                    for i in range(try_line_idx, block_end):
                        if i >= len(new_lines) or orig_lines[i] != new_lines[i]:
                            changed = True
                            break

        # 检查3：新代码比原始多出很多行（新增代码在错误行附近）
        if not changed and len(new_lines) > len(orig_lines):
            # 窗口内原始行不变，但新代码在窗口后有新增行
            for i in range(window_start, min(window_end, len(new_lines))):
                if i >= len(orig_lines) or (i < len(orig_lines) and new_lines[i] != orig_lines[i]):
                    changed = True
                    break

        if not changed:
            uncovered.append(err)

    if uncovered:
        summary = "; ".join(
            f"{e.get('file_path', '?')}:L{e.get('line_number', 0)} {e.get('error_type', '?')}"
            for e in uncovered
        )
        logger.warning(
            f"错误覆盖检查失败: {len(uncovered)}/{len(error_locations)} 个错误位置无变更"
        )
        return ValidationResult(
            passed=False,
            violation_type="error_uncovered",
            details=f"以下 {len(uncovered)} 个错误位置的邻近代码未被修改（遗漏修复）: {summary}",
            error_context={
                "uncovered_errors": [
                    {
                        "file_path": e.get("file_path", ""),
                        "line_number": e.get("line_number", 0),
                        "error_type": e.get("error_type", ""),
                        "error_message": (e.get("error_message", "") or "")[:80],
                    }
                    for e in uncovered
                ],
            },
        )

    return ValidationResult(passed=True)


def _check_change_limit(
    fix_result: dict,
    original_codes: dict[str, str],
    max_allowed: int = 50,
) -> ValidationResult:
    """检查总修改行数是否超过上限，防止过度修复

    计数时排除非功能性行：空行、纯注释行、docstring 行、导入语句行。
    避免 LLM 因添加注释/docstring/导入语句而被误拦。
    """
    for fp, new_code in fix_result.get("code_changes", {}).items():
        orig = original_codes.get(fp, "")
        if not orig:
            continue

        diff = list(difflib.unified_diff(
            orig.splitlines(keepends=True),
            new_code.splitlines(keepends=True),
            n=0,
        ))

        # 只统计功能性代码变更，排除非功能性行
        added = 0
        removed = 0
        for l in diff:
            if l.startswith("+") and not l.startswith("+++"):
                line_content = l[1:]
                if _is_functional_line(line_content) and not _is_import_line(line_content):
                    added += 1
            elif l.startswith("-") and not l.startswith("---"):
                line_content = l[1:]
                if _is_functional_line(line_content) and not _is_import_line(line_content):
                    removed += 1

        total = added + removed

        # 小文件修复（<100行）允许超出限制20%
        orig_line_count = len(orig.splitlines())
        if orig_line_count < 100:
            max_allowed = int(max_allowed * 1.2)

        if total > max_allowed:
            logger.warning(
                f"修改行数超限: {fp} 变动 {total} 行（+{added}/-{removed}），"
                f"超过上限 {max_allowed}"
            )
            return ValidationResult(
                passed=False,
                violation_type="change_limit_exceeded",
                details=f"文件 {fp} 变动 {total} 行（+{added}/-{removed}），超过上限 {max_allowed} 行",
                error_context={
                    "actual_changes": total,
                    "max_allowed": max_allowed,
                    "file_path": fp,
                },
            )

    return ValidationResult(passed=True)


def _is_functional_line(line: str) -> bool:
    """判断一行是否为功能性代码（非空行、非纯注释、非docstring定界符）"""
    stripped = line.strip()
    if not stripped:
        return False
    # 纯注释行
    if stripped.startswith("#"):
        return False
    # docstring 定界符行（单独一行的 """ 或 '''）
    if stripped in ('"""', "'''", '"""', "'''"):
        return False
    if stripped.startswith('"""') and stripped.endswith('"""') and len(stripped) <= 6:
        return False
    if stripped.startswith("'''") and stripped.endswith("'''") and len(stripped) <= 6:
        return False
    return True


def _check_file_completeness(
    fix_result: dict,
    error_locations: list[dict],
    target_files: list[str] | None = None,
) -> ValidationResult:
    """双向检查：遗漏目标文件 + 修改了非目标文件

    优先使用 orchestrator 提供的 target_files（确定性列表），
    兜底从 error_locations 提取。
    """
    if target_files:
        expected = {fp.replace("\\", "/") for fp in target_files}
    else:
        expected = set()
        for err in error_locations:
            fp = err.get("file_path", "")
            if fp and fp != "<string>":
                expected.add(fp.replace("\\", "/"))

    if not expected:
        return ValidationResult(passed=True)

    actual = set(fix_result.get("code_changes", {}).keys())

    # 正向：遗漏目标文件
    missing = expected - actual
    if missing:
        return ValidationResult(
            passed=False,
            violation_type="file_incomplete",
            details=f"遗漏文件: {', '.join(sorted(missing))}",
            error_context={
                "missing_files": list(missing),
                "all_target_files": sorted(expected),
            },
        )

    # 反向：修改了非目标文件
    extra = actual - expected
    if extra:
        return ValidationResult(
            passed=False,
            violation_type="wrong_file_modified",
            details=f"修改了非目标文件: {', '.join(sorted(extra))}",
            error_context={
                "invalid_files": list(extra),
                "all_target_files": sorted(expected),
            },
        )

    return ValidationResult(passed=True)


def _is_import_line(code_line: str) -> bool:
    """判断一行是否为 import 语句"""
    stripped = code_line.strip()
    return stripped.startswith("import ") or stripped.startswith("from ")


def _is_nameerror_only(errors: list[dict]) -> bool:
    """判断所有错误是否都是 NameError"""
    if not errors:
        return False
    return all(e.get("error_type") == "NameError" for e in errors)


def _check_func_scope(
    fix_result: dict,
    original_codes: dict[str, str],
    error_locations: list[dict],
) -> ValidationResult:
    """函数级错误：每条修改必须在对应的错误函数内

    构建 (文件, 函数名) 白名单，允许多个不同函数的错误同时被修复。
    NameError 允许在模块级添加 import 语句。
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

    # Step 1: 构建 (文件, 函数名) 白名单
    # 从原始 AST 中提取每个错误所在的函数名
    allowed_funcs: dict[str, set[str]] = {}
    for e in func_errors:
        fp = e.get("file_path", "")
        if not fp or e.get("line_number", 0) <= 0:
            continue
        orig = original_codes.get(fp, "")
        if not orig:
            continue
        try:
            orig_ast = ast.parse(orig)
        except SyntaxError:
            allowed_funcs.setdefault(fp, set()).add("*")
            continue
        func_node = _find_enclosing_function(orig_ast, e["line_number"])
        if func_node is not None:
            allowed_funcs.setdefault(fp, set()).add(func_node.name)

    # Step 2: 逐文件检查修改行
    for fp, new_code in fix_result.get("code_changes", {}).items():
        orig = original_codes.get(fp, "")
        if not orig:
            continue

        try:
            new_ast = ast.parse(new_code)
        except SyntaxError:
            continue

        changed_line_nums = _get_changed_line_numbers(orig, new_code)
        allowed = allowed_funcs.get(fp, set())

        for changed_lineno in changed_line_nums:
            enclosing = _find_enclosing_function(new_ast, changed_lineno)

            if enclosing is None:
                # 模块级修改 — 仅 NameError 允许加 import
                if is_name_err:
                    new_lines = new_code.splitlines()
                    changed_line = new_lines[changed_lineno - 1] if 1 <= changed_lineno <= len(new_lines) else ""
                    if _is_import_line(changed_line):
                        continue
                logger.warning(
                    f"函数范围越界: L{changed_lineno} 不在任何函数内"
                )
                return ValidationResult(
                    passed=False,
                    violation_type="func_body_modified",
                    details=f"修改 L{changed_lineno} 是模块级代码，不在允许的函数范围内",
                )

            # 检查函数名是否在白名单中
            if "*" not in allowed and enclosing.name not in allowed:
                logger.warning(
                    f"函数范围越界: L{changed_lineno} 在函数 {enclosing.name} 中，"
                    f"但该函数不在错误白名单中 (允许: {allowed})"
                )
                return ValidationResult(
                    passed=False,
                    violation_type="func_body_modified",
                    details=f"修改 L{changed_lineno} 属于函数 {enclosing.name}，"
                            f"不在允许函数列表中: {allowed}",
                    error_context={"func_name": enclosing.name, "allowed": list(allowed)},
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
