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
        "禁止修改此范围之外的任何代码。再次越界则修复直接失败。\n"
        "本次必须修复的目标文件列表：{target_files}。"
        "所有目标文件都必须包含在 code_changes 中（一个都不能少）。"
    ),

    "func_body_modified": (
        "🚨 强制性指令：你只能在函数 {func_name} 的内部修改代码。"
        "不得修改函数签名（参数列表、返回值类型）、不得修改其他函数、不得修改类定义。"
        "再次越界则修复直接失败。"
    ),

    # === ReviewAgent 拒绝 ===

    "original_error_unresolved": (
        "🚨 强制性指令：审查Agent发现以下原始错误仍未被修复：\n"
        "{review_detail}\n\n"
        "你必须精准定位到上述问题位置，逐一处理。已修复正确的代码禁止回退或修改。\n"
        "再次忽略此指令则修复直接失败。"
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

    # === Gate 拒绝：变更行数超限 ===
    "change_limit_exceeded": (
        "🚨 强制性指令：上次修复修改了 {actual_changes} 行，超过上限 {max_allowed} 行，超出 {overage} 行。\n\n"
        "## 裁剪策略（必须严格执行）\n"
        "1. 只保留对 CI 错误的**直接修复**，删除所有「额外改进」\n"
        "2. 禁止：安全优化、代码风格、类重构、函数重命名、添加注释/docstring\n"
        "3. 禁止：eval→ast.literal_eval、os.system→subprocess 等安全替代\n"
        "4. 如果原始文件是模块级函数，禁止重构为类\n"
        "5. 如果原始代码已有 try/except，禁止改为更复杂的错误处理\n\n"
        "## 具体操作\n"
        "- 对比原始代码和你的修复代码，找到与 CI 错误**无关**的变更\n"
        "- 将这些无关变更**回退到原始代码**\n"
        "- 只保留修复 CI 错误所需的**最小变更**\n\n"
        "再次越界则修复直接失败。"
    ),

    "file_incomplete": (
        "🚨 强制性指令：你遗漏了以下目标文件：{missing_files}。\n"
        "本次修复的 `code_changes` 必须包含上述所有文件（一个都不能少）。\n"
        "**被遗漏的文件也必须修复**，不允许仅用原始内容填充。\n"
        "所有目标文件的完整代码已在 prompt 中提供，请基于提供的代码进行修复。\n"
        "如果再次遗漏则修复直接失败。\n"
        "全部目标文件列表：{all_target_files}。"
    ),

    "wrong_file_modified": (
        "🚨 强制性指令：你修改了以下非目标文件：{invalid_files}。\n"
        "这些文件与本次报错无关，你绝对不能修改它们。\n"
        "你必须将 code_changes 的范围限制在以下目标文件内：{all_target_files}。\n"
        "如果再次修改非目标文件则修复直接失败。"
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
