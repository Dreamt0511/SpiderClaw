"""安全规则单一权威源 — FixAgent/ReviewAgent/TestAgent 全部引用此文件"""

from pydantic import BaseModel


class SecurityRule(BaseModel):
    """安全规则定义"""
    pattern: str            # 正则表达式
    severity: str           # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    category: str           # "code_exec" | "data_loss" | "auth" | "file_op" | "network"
    description: str        # 人类可读描述
    safe_alternative: str   # 给 FixAgent 看的安全替代方案
    check_new_only: bool = True  # True=仅检查新引入的风险


# === CRITICAL（致命）：立即终止，绝不创建 PR ===

CRITICAL_RULES: list[SecurityRule] = [
    SecurityRule(
        pattern=r"\beval\s*\(",
        severity="CRITICAL",
        category="code_exec",
        description="eval() 可动态执行任意代码",
        safe_alternative="使用 ast.literal_eval() 仅支持字面量求值",
    ),
    SecurityRule(
        pattern=r"\bexec\s*\(",
        severity="CRITICAL",
        category="code_exec",
        description="exec() 可执行任意代码",
        safe_alternative="重构为函数调用或静态代码",
    ),
    SecurityRule(
        pattern=r"\bcompile\s*\(",
        severity="CRITICAL",
        category="code_exec",
        description="compile() 可编译任意代码",
        safe_alternative="避免动态编译，使用静态代码",
    ),
    SecurityRule(
        pattern=r"\b__import__\s*\(",
        severity="CRITICAL",
        category="code_exec",
        description="__import__() 可动态导入任意模块",
        safe_alternative="使用标准 import 语句",
    ),
    SecurityRule(
        pattern=r"\bos\.system\s*\(",
        severity="CRITICAL",
        category="code_exec",
        description="os.system() 可执行任意系统命令",
        safe_alternative="使用 subprocess.run() 并显式指定命令列表",
    ),
    SecurityRule(
        pattern=r"\bos\.popen\s*\(",
        severity="CRITICAL",
        category="code_exec",
        description="os.popen() 可执行管道命令",
        safe_alternative="使用 subprocess.run(cmd, shell=False, capture_output=True)",
    ),
    SecurityRule(
        pattern=r"\bsubprocess\.call\s*\(\s*shell\s*=\s*True",
        severity="CRITICAL",
        category="code_exec",
        description="subprocess.call() 使用 shell=True 有命令注入风险",
        safe_alternative="使用 subprocess.run(cmd_list, shell=False)",
    ),
    SecurityRule(
        pattern=r"\bsubprocess\.Popen\s*\(\s*shell\s*=\s*True",
        severity="CRITICAL",
        category="code_exec",
        description="subprocess.Popen() 使用 shell=True 有命令注入风险",
        safe_alternative="使用 subprocess.Popen(cmd_list, shell=False)",
    ),
    SecurityRule(
        pattern=r"rm\s+-rf\s+/",
        severity="CRITICAL",
        category="data_loss",
        description="rm -rf / 递归删除根目录",
        safe_alternative="不要使用递归删除根目录的命令",
    ),
    SecurityRule(
        pattern=r"shutil\.rmtree\s*\(",
        severity="CRITICAL",
        category="data_loss",
        description="shutil.rmtree() 可删除目录树",
        safe_alternative="确认路径安全后再使用，或使用 send2trash",
    ),
    SecurityRule(
        pattern=r"__import__\s*\(\s*os\s*\)\.system",
        severity="CRITICAL",
        category="code_exec",
        description="混淆导入执行系统命令",
        safe_alternative="绝对禁止",
    ),
]


# === HIGH（高危）：强制重试，重试用尽后创建带"禁止合并"标签的 PR ===

HIGH_RULES: list[SecurityRule] = [
    SecurityRule(
        pattern=r"\bos\.remove\s*\(",
        severity="HIGH",
        category="file_op",
        description="os.remove() 删除文件",
        safe_alternative="确认文件路径安全后再操作",
    ),
    SecurityRule(
        pattern=r"\bos\.unlink\s*\(",
        severity="HIGH",
        category="file_op",
        description="os.unlink() 删除文件",
        safe_alternative="确认文件路径安全后再操作",
    ),
    SecurityRule(
        pattern=r"\bos\.rmdir\s*\(",
        severity="HIGH",
        category="file_op",
        description="os.rmdir() 删除目录",
        safe_alternative="确认目录路径安全后再操作",
    ),
    SecurityRule(
        pattern=r"\bopen\s*\([^)]*['\"]w['\"]",
        severity="HIGH",
        category="file_op",
        description="写入模式打开文件可能覆盖用户数据",
        safe_alternative="使用 'a' 追加模式或确认不会覆盖重要数据",
    ),
    # 硬编码密钥/凭证
    SecurityRule(
        pattern=r"\b(?:api_key|secret_key|private_key)\s*=\s*['\"]",
        severity="HIGH",
        category="auth",
        description="API/私钥硬编码",
        safe_alternative="使用环境变量或配置文件读取",
    ),
    SecurityRule(
        pattern=r"\b(?:password|passwd|pwd)\s*=\s*['\"]",
        severity="HIGH",
        category="auth",
        description="密码硬编码",
        safe_alternative="使用环境变量或密钥管理服务",
    ),
    SecurityRule(
        pattern=r"\b(?:token|access_token|auth_token)\s*=\s*['\"]",
        severity="HIGH",
        category="auth",
        description="Token 硬编码",
        safe_alternative="使用环境变量或配置管理",
    ),
    # SQL 注入
    SecurityRule(
        pattern=r"(?:execute|executemany|raw_sql|raw_input)\s*\(\s*(?:f['\"]|['\"]f\s*)",
        severity="HIGH",
        category="code_exec",
        description="f-string 拼接 SQL 查询 (SQL 注入风险)",
        safe_alternative="使用参数化查询: cursor.execute(sql, params)",
    ),
    SecurityRule(
        pattern=r"cursor\.execute\s*\(\s*f['\"]",
        severity="HIGH",
        category="code_exec",
        description="cursor.execute() 使用 f-string (SQL 注入风险)",
        safe_alternative="使用参数化查询",
    ),
    # 外部 HTTP 请求 (SSRF)
    SecurityRule(
        pattern=r"\b(?:requests|httpx|urllib\.request)\.(?:get|post|put|delete|patch)\s*\(",
        severity="HIGH",
        category="network",
        description="HTTP 请求 (SSRF/数据泄露风险)",
        safe_alternative="验证 URL 白名单，使用安全的 HTTP 客户端配置",
    ),
    SecurityRule(
        pattern=r"\bhttp\.client\.(?:HTTPConnection|HTTPSConnection)\s*\(",
        severity="HIGH",
        category="network",
        description="底层 HTTP 连接 (SSRF 风险)",
        safe_alternative="使用高层库如 requests 并验证 URL",
    ),
    # pickle 反序列化
    SecurityRule(
        pattern=r"\bpickle\.loads?\s*\(",
        severity="HIGH",
        category="code_exec",
        description="pickle 反序列化可导致任意代码执行",
        safe_alternative="使用 json.loads() 或限制 pickle 反序列化类型",
    ),
    # yaml.load 不安全
    SecurityRule(
        pattern=r"\byaml\.load\s*\(",
        severity="HIGH",
        category="code_exec",
        description="yaml.load() 可导致任意代码执行",
        safe_alternative="使用 yaml.safe_load()",
    ),
]


# === MEDIUM（中危）：记录审查意见，不阻止流程 ===

MEDIUM_RULES: list[SecurityRule] = [
    SecurityRule(
        pattern=r"except\s*:",
        severity="MEDIUM",
        category="code_exec",
        description="裸 except 可能隐藏错误",
        safe_alternative="捕获具体异常类型: except SpecificError:",
    ),
    SecurityRule(
        pattern=r"except\s+Exception\s*:",
        severity="MEDIUM",
        category="code_exec",
        description="过于宽泛的异常捕获",
        safe_alternative="捕获更具体的异常类型",
    ),
]


# === LOW（低风险）：记录日志，不影响流程 ===

LOW_RULES: list[SecurityRule] = [
    SecurityRule(
        pattern=r"#\s*TODO",
        severity="LOW",
        category="code_exec",
        description="TODO 注释",
        safe_alternative="—",
    ),
    SecurityRule(
        pattern=r"#\s*FIXME",
        severity="LOW",
        category="code_exec",
        description="FIXME 注释",
        safe_alternative="—",
    ),
    SecurityRule(
        pattern=r"#\s*HACK",
        severity="LOW",
        category="code_exec",
        description="HACK 注释",
        safe_alternative="—",
    ),
]


def get_rules_by_severity(severity: str) -> list[SecurityRule]:
    """按严重等级获取规则"""
    mapping = {
        "CRITICAL": CRITICAL_RULES,
        "HIGH": HIGH_RULES,
        "MEDIUM": MEDIUM_RULES,
        "LOW": LOW_RULES,
    }
    return mapping.get(severity.upper(), [])


def get_all_rules() -> list[SecurityRule]:
    """获取所有安全规则"""
    return CRITICAL_RULES + HIGH_RULES + MEDIUM_RULES + LOW_RULES


def get_patterns_list(rules: list[SecurityRule]) -> list[str]:
    """从 SecurityRule 列表提取正则字符串（兼容旧代码）"""
    return [r.pattern for r in rules]


def get_fix_agent_security_section() -> str:
    """生成 FixAgent 提示词中的安全操作识别与规避部分"""
    lines = [
        "## 安全注意事项",
        "修复代码时，请注意以下安全敏感模式。",
        "",
        "| 危险模式 | 风险等级 | 说明 |",
        "|---------|---------|------|",
    ]
    for rule in CRITICAL_RULES + HIGH_RULES:
        desc = rule.description
        lines.append(f"| `{rule.pattern}` | {rule.severity} | {desc} |")
    lines.append("")
    lines.append("**规则**：")
    lines.append("- 修复时不要**新引入** eval、exec、os.system 等危险函数")
    lines.append("- 原始代码中已有的安全风险**不在本次修复范围内**，不要修改")
    return "\n".join(lines)
