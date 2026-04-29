"""代码上下文提取 — 从原始代码中提取与错误相关的代码片段而非完整文件"""

import ast
import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_error_context_section(
    original_codes: dict[str, str],
    error_locations: list[Any],
    target_files: list[str],
    context_lines: int = 8,
) -> str:
    """构建错误代码上下文区块（仅展示与错误相关的代码片段）。

    对每个目标文件，根据 ErrorLocation 的行号定位到函数/类体，
    提取语义完整的代码片段，而非完整文件。
    """
    if not original_codes or not target_files or not error_locations:
        return ""

    # 构建文件→错误列表的映射
    file_errors: dict[str, list[dict]] = {}
    for err in error_locations:
        fp = ""
        if hasattr(err, "file_path"):
            fp = err.file_path
        elif isinstance(err, dict):
            fp = err.get("file_path", "")

        if not fp:
            continue

        lineno = 0
        if hasattr(err, "line_number"):
            lineno = err.line_number
        elif isinstance(err, dict):
            lineno = err.get("line_number", 0)

        etype = ""
        if hasattr(err, "error_type"):
            etype = err.error_type
        elif isinstance(err, dict):
            etype = err.get("error_type", "")

        emsg = ""
        if hasattr(err, "error_message"):
            emsg = err.error_message
        elif isinstance(err, dict):
            emsg = err.get("error_message", "")

        is_root = False
        if hasattr(err, "is_root_cause"):
            is_root = err.is_root_cause
        elif isinstance(err, dict):
            is_root = err.get("is_root_cause", False)

        file_errors.setdefault(fp, []).append({
            "line_number": lineno,
            "error_type": etype,
            "error_message": emsg,
            "is_root_cause": is_root,
        })

    blocks = []
    for fp in target_files:
        source = original_codes.get(fp, "")
        if not source:
            blocks.append(f"### {fp}\n_（原始内容为空）_\n")
            continue

        source_lines = source.splitlines()
        err_list = file_errors.get(fp, [])

        # 目标文件中没有错误 → 简短注释
        if not err_list:
            blocks.append(f"### {fp}\n_（目标文件中无直接错误，可按需使用 `read_file` 查看）_\n")
            continue

        # 尝试 AST 解析
        try:
            tree = ast.parse(source)
        except SyntaxError:
            # 语法错误文件 → 行级窗口 + 完整文件内容
            line_numbers = sorted(set(
                err["line_number"] for err in err_list if err["line_number"] > 0
            ))
            snippet = _extract_window(source_lines, line_numbers, context_lines)
            full_block = (
                f"{snippet}\n\n"
                f"#### 完整文件内容（供参考）\n"
                f"```python\n{source}\n```"
            )
            header = f"### {fp} ⚠️ 语法错误（行级上下文 + 完整文件）"
            blocks.append(_format_block(header, full_block, err_list))
            logger.info(f"文件 {fp} 有语法错误，使用行级窗口 + 完整文件回退")
            continue

        # 按错误行号分组：函数内 / 类方法内 / 模块级
        func_boundaries = _find_func_boundaries(tree)
        cls_boundaries = _find_class_boundaries(tree)

        processed_funcs: set[int] = set()
        processed_classes: set[tuple[str, int]] = set()
        module_lines: set[int] = set()

        for err in err_list:
            lineno = err["line_number"]
            if lineno <= 0:
                module_lines.add(lineno)
                continue

            # 检查是否在最内层函数内
            func_node = _find_func_at_lineno(func_boundaries, lineno)
            if func_node is not None:
                func_key = id(func_node)
                if func_key in processed_funcs:
                    continue
                processed_funcs.add(func_key)
                text = _extract_node_text(source_lines, func_node)
                header = f"### {fp} — `{func_node.name}`"
                blocks.append(_format_block(header, text, err_list))
                continue

            # 检查是否在类内（但不在方法内，如类属性赋值）
            cls_node = _find_class_at_lineno(cls_boundaries, lineno)
            if cls_node is not None:
                cls_key = (fp, cls_node.lineno)
                if cls_key in processed_classes:
                    continue
                processed_classes.add(cls_key)
                text = _extract_class_snippet(source_lines, tree, cls_node, err_list)
                header = f"### {fp} — 类 `{cls_node.name}`"
                blocks.append(_format_block(header, text, err_list))
                continue

            module_lines.add(lineno)

        # 模块级代码（不在任何函数或类内）
        if module_lines:
            filtered = [ln for ln in module_lines if ln > 0]
            if filtered:
                snippet = _extract_window(source_lines, sorted(filtered), context_lines)
                header = f"### {fp} — 模块级代码"
                blocks.append(_format_block(header, snippet, err_list))

        # 如果文件有错误列表但没有任何匹配的代码被提取
        if err_list and not processed_funcs and not processed_classes and not module_lines:
            snippets = []
            for err in err_list:
                if err["line_number"] > 0:
                    snippets.append(_extract_window(
                        source_lines, [err["line_number"]], context_lines
                    ))
            if snippets:
                header = f"### {fp}"
                blocks.append(_format_block(header, "\n\n".join(snippets), err_list))

    if not blocks:
        return ""

    # 构建最终输出
    sections = "\n\n".join(blocks)
    section = (
        "## 📂 错误代码上下文（仅展示与错误相关的代码片段，非完整文件）\n\n"
        + sections
        + "\n\n_💡 如需查看完整文件内容，请使用 `read_file` 工具。_"
    )
    return section


# ==================== 辅助：AST 提取 ====================


def _find_func_boundaries(tree: ast.AST) -> list[tuple[int, int, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """返回 (start_line, end_line, node) 列表，按 start_line 排序"""
    boundaries = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = _get_node_end(node)
            boundaries.append((node.lineno, end, node))
    boundaries.sort(key=lambda x: x[0])
    return boundaries


def _find_class_boundaries(tree: ast.AST) -> list[tuple[int, int, ast.ClassDef]]:
    """返回 (start_line, end_line, node) 列表"""
    boundaries = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            end = _get_node_end(node)
            boundaries.append((node.lineno, end, node))
    boundaries.sort(key=lambda x: x[0])
    return boundaries


def _find_func_at_lineno(
    boundaries: list[tuple[int, int, ast.FunctionDef | ast.AsyncFunctionDef]],
    lineno: int,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """找到包含 lineno 的最内层函数"""
    result = None
    for start, end, node in boundaries:
        if start <= lineno <= end:
            if result is None or node.lineno > result.lineno:
                result = node
    return result


def _find_class_at_lineno(
    boundaries: list[tuple[int, int, ast.ClassDef]],
    lineno: int,
) -> ast.ClassDef | None:
    """找到包含 lineno 的最内层类"""
    result = None
    for start, end, node in boundaries:
        if start <= lineno <= end:
            if result is None or node.lineno > result.lineno:
                result = node
    return result


def _get_node_end(node) -> int:
    """获取 AST 节点的结束行号"""
    if hasattr(node, "end_lineno") and node.end_lineno is not None:
        return node.end_lineno
    if hasattr(node, "body") and node.body:
        last = node.body[-1]
        return _get_node_end(last)
    return node.lineno


def _extract_node_text(source_lines: list[str], node) -> str:
    """从源代码行中提取 AST 节点的文本"""
    start = node.lineno - 1
    end = _get_node_end(node)
    return "\n".join(source_lines[start:end])


def _extract_class_snippet(
    source_lines: list[str],
    tree: ast.AST,
    cls_node: ast.ClassDef,
    err_list: list[dict],
) -> str:
    """提取类头部 + 受影响的方法，跳过无关方法以节省 token"""
    affected_lines = {e["line_number"] for e in err_list if e["line_number"] > 0}
    # 找到该类的子节点
    class_body_ends: list[int] = []
    for node in ast.walk(cls_node):
        if node is cls_node:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            class_body_ends.append(_get_node_end(node))

    # 收集受影响的方法
    affected_methods = []
    for node in ast.walk(cls_node):
        if node is cls_node:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for al in affected_lines:
                if node.lineno <= al <= _get_node_end(node):
                    affected_methods.append(node)
                    break

    # 提取类头部行（从 class 行到第一个方法/类属性的起始行）
    if class_body_ends:
        first_body_start = min(class_body_ends)
    else:
        first_body_start = _get_node_end(cls_node)

    cls_header = cls_node.lineno - 1
    # 只取类定义行 + 文档字符串（如果有）
    class_docstring_end = cls_header
    if (cls_node.body and
            isinstance(cls_node.body[0], ast.Expr) and
            isinstance(cls_node.body[0].value, (ast.Constant, ast.Str))):
        class_docstring_end = _get_node_end(cls_node.body[0])

    lines = [source_lines[cls_header]]
    lines.append("    ...")

    for method in affected_methods:
        lines.append("")
        lines.append(_extract_node_text(source_lines, method))

    if not affected_methods:
        lines.append("    # （未定位到具体方法，请使用 read_file 查看完整内容）")

    return "\n".join(lines)


def _extract_window(
    source_lines: list[str],
    line_numbers: list[int],
    context_lines: int = 8,
) -> str:
    """行级窗口提取（合并重叠范围）

    Args:
        source_lines: 原始文件行列表
        line_numbers: 错误行号列表（1-indexed）
        context_lines: 每侧额外的行数
    """
    if not line_numbers:
        return ""

    n = len(source_lines)
    # 构建合并范围
    ranges = []
    for ln in sorted(line_numbers):
        lo = max(0, ln - 1 - context_lines)
        hi = min(n, ln + context_lines)
        if ranges and lo <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], hi)
        else:
            ranges.append((lo, hi))

    # 提取合并后的片段
    parts = []
    for lo, hi in ranges:
        part_lines = source_lines[lo:hi]
        # 添加行号注释
        numbered = []
        for i, line in enumerate(part_lines, start=lo + 1):
            marker = ">>>" if i in line_numbers else "   "
            numbered.append(f"{marker} L{i}: {line}")
        parts.append("\n".join(numbered))

    return "\n\n".join(parts)


def _format_block(header: str, code_content: str, err_list: list[dict]) -> str:
    """格式化为 markdown 区块"""
    err_lines = []
    for err in err_list:
        prefix = "🔴 [根因] " if err.get("is_root_cause") else ""
        ln = err.get("line_number", 0)
        et = err.get("error_type", "")
        em = (err.get("error_message", "") or "")[:200]
        if ln > 0:
            err_lines.append(f"- {prefix}[{et}] L{ln}: {em}")
        elif et:
            err_lines.append(f"- {prefix}[{et}]: {em}")

    err_block = "\n".join(err_lines) if err_lines else ""

    parts = [header]
    if err_block:
        parts.append(err_block)
    parts.append(f"```python\n{code_content}\n```")
    return "\n".join(parts)
