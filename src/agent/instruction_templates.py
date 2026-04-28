"""强制性修复指令模板 — 纯规则引擎，0 Token 消耗"""

INSTRUCTION_TEMPLATES = {
    # === Gate 拒绝（validation_gate.py） ===

    "import_line_violation": (
        "🚨 强制性指令：本次你【只能】修改 import / from ... import 语句。\n"
        "上一轮违规内容：{details}\n"
        "绝对不允许修改任何函数体、类定义、变量赋值、注释或其他代码。\n"
        "如果该模块未在代码中使用，直接删除该 import 行。\n"
        "如果该模块确实需要但未安装，将 is_env_error 标记为 true。\n"
        "再次越界则修复直接失败。"
    ),

    "syntax_line_violation": (
        "🚨 强制性指令：你只能修改第 {error_lines} 行及其上下各3行的范围。"
        "禁止修改此范围之外的任何代码。再次越界则修复直接失败。"
    ),

    "func_body_modified": (
        "🚨 强制性指令：你只能在函数 {func_name} 的内部修改代码。"
        "不得修改函数签名（参数列表、返回值类型）、不得修改其他函数、不得修改类定义。"
        "再次越界则修复直接失败。"
    ),

    # === ReviewAgent 拒绝 ===

    "original_error_unresolved": (
        "🚨 强制性指令：原始错误 {error_type} 在 {file_path}:L{line_number} 仍未被修复。"
        "你必须精准定位到该位置处理此错误。不得修改其他无关代码。"
    ),

    "new_bug_introduced": (
        "🚨 强制性指令：你的修复引入了新的问题。请回退引起新错误的修改，"
        "仅保留对原始错误的最小修复。新问题：{issue}"
    ),

    "contract_break": (
        "🚨 强制性指令：你修改了函数 {func_name} 的签名或返回值类型。"
        "必须恢复原始签名，只允许在函数体内部做最小修改。"
    ),

    # === TestAgent 拒绝 ===

    "test_failure": (
        "🚨 强制性指令：你的修复导致 {n} 个测试失败：{failed_tests}。"
        "请回退引起新测试失败的修改，仅保留对原始错误的最小修复。"
    ),

    # === ValidationGate 其他 ===
    "boundary_violation": (
        "🚨 强制性指令：你的修改超出了允许的范围。"
        "请严格限制在最小修改原则内，仅修改与原始错误直接相关的代码行。"
        "再次越界则修复直接失败。"
    ),
}


def generate_instruction(rejection_reason: str, **kwargs) -> str:
    """根据拒绝原因和上下文生成强制性指令（纯规则引擎，不调用 LLM）"""
    template = INSTRUCTION_TEMPLATES.get(rejection_reason, "")
    if not template:
        return "请按照最小修改原则修复原始错误，不要修改无关代码。"
    try:
        return template.format(**kwargs)
    except KeyError:
        return template
