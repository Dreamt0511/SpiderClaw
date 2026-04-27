# SpiderClaw 架构重构设计文档

> 日期: 2026-04-28 | 状态: 设计完成，待实现

---

## 一、问题诊断

### 1.1 三大核心问题

| 问题 | 症状 | 根因 |
|------|------|------|
| **过度修复** | ImportError → LLM 顺手重写整个函数体 | 提示词只有"建议"无"强制边界"，未按错误类型差异化限制修改范围 |
| **反馈无效** | 第 3 次重试仍然犯第 1 次的错误 | 重试时只传全文评论，无结构化强制指令，无历史记录，原始代码已丢失 |
| **重试浪费** | 越界修复 → 审查(5000T) → 再次越界 → 审查(5000T) → 测试(3000T) | 越界修复未经硬校验直接进入审查/测试循环 |

### 1.2 深层架构问题

- **Orchestrator God Object** (1281行)：配置、图构建、6个节点、3个路由、PR正文、飞书通知全部耦合
- **State 扁平无类型**：TypedDict 无运行时校验，`operator.add` 在重试时导致列表翻倍
- **安全规则 3 处重复**：FixAgent 提示词、ReviewAgent 扫描、TestAgent 过滤各自定义
- **Agent 间隐式通信**：通过 dict 传递，无正式契约，无必填字段保证
- **original_codes 回退到磁盘**：重试时文件已被覆盖，回退路径读到错误内容

---

## 二、改造目标

1. 任何类型错误在最小修改原则下一次修复成功（或不需修复时正确终止）
2. Agent 间反馈传递明确有效，修复 Agent 必须遵守审查/测试的强制性指令
3. 后置硬校验防止 LLM 越过边界，避免 3 次重试中的无效 Token 消耗
4. Token 消耗降低 60% 以上

---

## 三、架构变更

### 3.1 文件拆分：从 1 个 God Object → 4 个模块

```
src/agent/
├── orchestrator.py          # 图构建 + 路由（~350行）
├── agent_factory.py         # 新：统一创建/配置 FixAgent/ReviewAgent/TestAgent（~120行）
├── validation_gate.py       # 新：后置硬校验全部逻辑（~200行）
├── notification.py          # 新：飞书通知 + PR 正文构建（~100行）
├── security_rules.py        # 新：安全规则单一权威源（~80行）
├── instruction_templates.py # 新：强制指令模板映射（~40行）
├── state.py                 # 改：Pydantic RepairState + AgentContext + ErrorLocation 等
├── subagents/
│   ├── fix_agent.py         # 改：接收 mandatory_instructions, fix_history
│   ├── review_agent.py      # 改：新增 rejection_reason 枚举，安全规则引用 security_rules
│   └── test_agent.py        # 改：路由优化，清理死代码
├── prompts/
│   ├── fix_agent.py         # 改：提示词重构，增加绝对修改边界
│   ├── review_agent_prompts.py  # 改：原始问题归入 risk_warnings
│   └── test_agent_prompts.py    # 清理或保留备用
└── tools/
    └── langchain_tools.py   # 微调：修复 contextvar 竞态
```

### 3.2 State 重构：扁平 TypedDict → 分层 Pydantic BaseModel

```python
# === 基础类型 ===

class ErrorLocation(BaseModel):
    """错误位置信息"""
    file_path: str = ""
    line_number: int = 0
    error_type: str  # "ModuleNotFoundError", "SyntaxError", "NameError", ...
    error_message: str = ""
    traceback: str = ""
    source: str = ""  # "traceback", "syntax_error", "simple", "pytest"

class FixAttempt(BaseModel):
    """单次修复尝试记录"""
    attempt: int
    diff_summary: str          # 本次修改的摘要（截断到200字符）
    rejection_reason: str      # 被拒原因
    rejected_by: str           # "gate" | "review" | "test"

class ReviewFeedback(BaseModel):
    """审查的结构化反馈"""
    passed: bool
    rejection_reason: str = ""  # 枚举: original_error_unresolved | new_bug_introduced | contract_break
    comments: str = ""
    risk_warnings: list[str] = []

class TestFeedback(BaseModel):
    """测试的结构化反馈"""
    status: str  # "success" | "failure" | "uncertain"
    failed_tests: list[str] = []
    output: str = ""
    new_errors: list[str] = []

# === AgentContext（传递给每个 Agent 的统一上下文） ===

class AgentContext(BaseModel):
    """Agent 间通信的正式契约"""
    error_locations: list[ErrorLocation]
    original_codes: dict[str, str]     # 文件路径 → 原始内容（不可变快照）
    fix_history: list[FixAttempt] = []
    mandatory_instructions: str = ""    # 强制性修复指令
    review_feedback: ReviewFeedback | None = None
    test_feedback: TestFeedback | None = None
    retry_count: int = 0
    max_retries: int = 3

# === RepairState（LangGraph State） ===

class RepairState(BaseModel):
    # --- 输入层 ---
    event: dict = {}              # GitHubEvent 序列化
    ci_logs: str = ""
    repo_path: str = ""

    # --- 上下文层 ---
    error_locations: list[ErrorLocation] = []
    original_codes: dict[str, str] = {}

    # --- 修复层 ---
    fix_description: str = ""
    modified_files: list[str] = []
    code_changes: dict[str, str] = {}  # file_path → new_content
    diff_content: str = ""

    # --- 重试上下文（新增） ---
    fix_history: list[FixAttempt] = []
    mandatory_instructions: str = ""

    # --- 审查层 ---
    review_passed: bool = False
    review_comments: str = ""
    risk_warnings: list[str] = []     # 去掉 operator.add，改为显式合并
    risk_level: str = "NONE"          # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "NONE"
    rejection_reason: str = ""        # 新增：审查拒绝原因枚举

    # --- 测试层 ---
    validation_status: str = ""       # "success" | "failure" | "uncertain"
    test_output: str = ""
    failed_tests: list[str] = []      # 去掉 operator.add

    # --- 结果层 ---
    pr_url: str = ""
    pr_number: int = 0
    success: bool = False
    error_message: str = ""

    # --- 控制层 ---
    retry_count: int = 0
    max_retries: int = 3
    current_phase: str = ""           # 新增：阶段追踪
```

### 3.3 路由图变更

```
改造前（6 节点）：
  collect_context → fix_agent → review_changes → run_tests → create_pr → END

改造后（7 节点）：
  collect_context → fix_agent → [validation_gate] → review_changes → run_tests → create_pr → END
                                  |                    |                |
                                  +→ fix_agent (重试)  +→ fix_agent     +→ fix_agent (仅逻辑错误)
                                  +→ handle_failure    +→ handle_failure +→ handle_failure

关键变化：
  - 新增 validation_gate 在 fix_agent 和 review_changes 之间
  - Gate 失败 → 直接返回 fix_agent，不经过审查和测试
  - 测试 uncertain/导入类失败 → 直接 create_pr，不重试
```

---

## 四、模块详细设计

### 4.1 validation_gate.py（后置硬校验门禁）

**职责**：在修复输出后、文件写入前，检查修改范围是否符合错误类型的边界约束。

**入口函数**：
```python
def validate_fix(
    fix_result: dict,           # FixAgent.generate_fix() 的返回值
    original_codes: dict[str, str],
    error_locations: list[ErrorLocation],
) -> ValidationResult:
    """
    返回:
      ValidationResult(passed=True) → 放行
      ValidationResult(passed=False, violation_type=..., details=...) → 拦截
    """
```

**校验策略表**：

| 错误类型 | 允许范围 | 校验方法 | violation_type |
|----------|---------|---------|----------------|
| ModuleNotFoundError, ImportError | 仅 import/from 行 | difflib逐行检查 +行开头 | import_line_violation |
| SyntaxError, IndentationError, TabError | 错误行 ±3 行 | 行号范围检查 | syntax_line_violation |
| NameError | 错误行所在函数体 | AST 定位函数节点 | func_body_modified |
| TypeError, ValueError, AttributeError | 错误行所在函数体 | AST 定位函数节点 | func_body_modified |
| KeyError, IndexError | 错误行所在函数体 | AST 定位函数节点 | func_body_modified |
| 混合错误 | 各类型并集 | 逐类型检查 | 合并报告 |

**ImportError 校验实现**：
```python
def _check_import_error(fix_result, original_codes, error_locations) -> ValidationResult:
    """纯导入错误：只允许修改 import/from 行"""
    if not all(e.error_type in ('ModuleNotFoundError', 'ImportError') for e in error_locations):
        return ValidationResult(passed=True)  # 非纯导入错误，跳过后面的检查

    for fp, new_code in fix_result.get('code_changes', {}).items():
        orig = original_codes.get(fp, '')
        diff = difflib.unified_diff(
            orig.splitlines(keepends=True),
            new_code.splitlines(keepends=True),
            n=0
        )
        for line in diff:
            if line.startswith('+') and not line.startswith('+++'):
                content = line[1:].strip()
                if not content or content.startswith('#'):
                    continue  # 空行和注释放行
                if content.startswith('import ') or content.startswith('from '):
                    continue  # 导入行放行
                # 检查是否为 try/except ImportError 的框架行
                if content in ('try:', 'except ImportError:', 'except ModuleNotFoundError:'):
                    continue
                return ValidationResult(
                    passed=False,
                    violation_type="import_line_violation",
                    details=f"越界修改行: {line.strip()}",
                )
    return ValidationResult(passed=True)
```

**SyntaxError 校验实现**：
```python
def _check_syntax_error(fix_result, original_codes, error_locations) -> ValidationResult:
    """语法错误：只允许修改错误行 ±3 行范围"""
    syntax_errors = [e for e in error_locations if e.error_type in ('SyntaxError', 'IndentationError', 'TabError')]
    if not syntax_errors:
        return ValidationResult(passed=True)

    # 收集所有允许的行号范围
    allowed_ranges = []
    for e in syntax_errors:
        if e.line_number > 0:
            allowed_ranges.append((e.line_number - 3, e.line_number + 3))

    for fp, new_code in fix_result.get('code_changes', {}).items():
        orig = original_codes.get(fp, '')
        diff = list(difflib.unified_diff(
            orig.splitlines(keepends=True),
            new_code.splitlines(keepends=True),
            n=0
        ))
        # 解析 diff 中的行号确定修改行
        line_num = 0
        for line in diff:
            if line.startswith('@@'):
                # 解析 @@ -old,count +new,count @@ 中的 new 起始行
                match = re.search(r'\+(\d+)', line)
                if match:
                    line_num = int(match.group(1)) - 1
            elif line.startswith('+') and not line.startswith('+++'):
                line_num += 1
                content = line[1:].strip()
                if not content:
                    continue
                if not any(lo <= line_num <= hi for lo, hi in allowed_ranges):
                    return ValidationResult(
                        passed=False,
                        violation_type="syntax_line_violation",
                        details=f"L{line_num} 超出允许范围",
                    )
            elif not line.startswith('-'):
                line_num += 1
    return ValidationResult(passed=True)
```

**AST 函数范围校验**（TypeError/NameError 等）：
```python
def _check_func_scope(fix_result, original_codes, error_locations) -> ValidationResult:
    """函数级错误：修改必须在出错函数 AST 节点内"""
    func_errors = [e for e in error_locations
                   if e.error_type in ('NameError', 'TypeError', 'ValueError',
                                        'AttributeError', 'KeyError', 'IndexError')]
    if not func_errors:
        return ValidationResult(passed=True)

    for fp, new_code in fix_result.get('code_changes', {}).items():
        orig = original_codes.get(fp, '')
        try:
            orig_ast = ast.parse(orig)
            new_ast = ast.parse(new_code)
        except SyntaxError:
            continue  # 语法错误交给 _check_syntax_error

        # 对每个函数级错误，找到错误行所在的函数
        for e in func_errors:
            if e.file_path != fp or e.line_number <= 0:
                continue
            func_node = _find_enclosing_function(orig_ast, e.line_number)
            if func_node is None:
                continue
            # 检查变更 AST 节点是否都在该函数子树内
            changed_nodes = _get_changed_nodes(orig_ast, new_ast)
            for node in changed_nodes:
                if not _is_descendant_of(node, func_node, new_ast):
                    return ValidationResult(
                        passed=False,
                        violation_type="func_body_modified",
                        details=f"修改超出函数 {func_node.name} 范围",
                    )
    return ValidationResult(passed=True)
```

**在 Orchestrator 中的调用位置**（`_run_fix_agent` 方法内）：
```python
def _run_fix_agent(state: RepairState):
    fix_agent = agent_factory.create_fix_agent(state.repo_path)
    fix_result = fix_agent.generate_fix(
        ci_logs=state.ci_logs,
        error_locations=state.error_locations,
        mandatory_instructions=state.mandatory_instructions,
        fix_history=state.fix_history,
        # ... 其他参数
    )

    # 🔒 后置硬校验
    validation = validation_gate.validate_fix(
        fix_result, state.original_codes, state.error_locations
    )

    if not validation.passed:
        # 不写文件，直接返回重试
        retry_context = _build_retry_context(state, "gate", validation)
        return Command(update={
            "fix_history": retry_context["fix_history"],
            "mandatory_instructions": retry_context["mandatory_instructions"],
            "retry_count": state.retry_count + 1,
            "current_phase": "retry_from_gate",
        }, goto="fix_agent" if state.retry_count + 1 < state.max_retries else "handle_failure")

    # 通过：写文件
    for fp, content in fix_result["code_changes"].items():
        write_file(fp, content)

    return Command(update={
        "fix_description": fix_result["fix_description"],
        "code_changes": fix_result["code_changes"],
        "modified_files": fix_result["modified_files"],
        "diff_content": fix_result.get("diff_content", ""),
        "current_phase": "review",
    }, goto="review_changes")
```

### 4.2 instruction_templates.py（强制指令模板）

```python
"""强制性修复指令模板 — 纯规则引擎，0 Token"""

INSTRUCTION_TEMPLATES = {
    # Gate 拒绝
    "import_line_violation": (
        "🚨 强制性指令：本次你【只能】修改 import / from ... import 语句。"
        "绝对不允许修改任何函数体、类定义、变量赋值、注释或其他代码。"
        "如果该模块未在代码中使用，直接删除该 import 行。"
        "如果该模块确实需要但未安装，将 is_env_error 标记为 true。"
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

    # ReviewAgent 拒绝
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

    # TestAgent 拒绝
    "test_failure": (
        "🚨 强制性指令：你的修复导致 {n} 个测试失败：{failed_tests}。"
        "请回退引起新测试失败的修改，仅保留对原始错误的最小修复。"
    ),
}

def generate_instruction(rejection_reason: str, **kwargs) -> str:
    """根据拒绝原因和上下文生成强制性指令"""
    template = INSTRUCTION_TEMPLATES.get(rejection_reason, "")
    if not template:
        return "请按照最小修改原则修复原始错误，不要修改无关代码。"
    return template.format(**kwargs)
```

### 4.3 fix_agent.py 提示词重构

**FIX_AGENT_SYSTEM_PROMPT 开头插入绝对修改边界**：

```
你是专业的 Python 代码修复专家。你的目标是用最小的代码变更解决 CI 错误。

## 🔒 绝对修改边界（违反即视为修复失败）

| 错误类型 | 允许修改的代码范围 |
|---------|-------------------|
| ModuleNotFoundError, ImportError | 仅 import / from ... import 行（增、删、try/except 包裹） |
| SyntaxError, IndentationError, TabError | 仅错误行及其缩进/括号配对行（±3行） |
| NameError | 仅添加缺失的 import 或声明变量，修正拼写 |
| TypeError, ValueError, AttributeError, KeyError, IndexError | 仅出错的函数/方法体内部，禁止改变签名 |
| 其他 | 保持最小改动，不重构，不"顺便"修复无关问题 |

**警告**：如果你修改了上述范围外的任何代码，该修复会被系统自动拒绝，且你的回答直接作废。

## 输出前的自检清单
在输出 JSON 之前，必须逐条确认：
1. 我修改的每一行是否都在上述允许范围内？
2. 我是否"顺便"修改了任何无关的代码？
3. 如果是导入错误，我是否只动了 import 行？
4. 如果是函数内错误，我是否修改了函数签名？
5. 我是否用最小改动解决了问题？有没有更小的方案？
```

**FIX_AGENT_USER_PROMPT 末尾追加强约束区**：

```
## 🚨 强制性修复指令（必须逐字执行，违反即修复失败）
{mandatory_instructions}

## 历史修复记录（避免重复同样的错误）
{fix_history_summary}

## 审查反馈（如有）
{review_feedback}

## 测试反馈（如有）
{test_feedback}

## 本次修复的强约束清单
- 原始代码快照见上方，仅修改与错误直接相关的行
- 输出 JSON 前完成自检清单
- is_env_error 标记为 true 表示需要环境变更（安装依赖），此时 code_changes 应为空
```

### 4.4 FixAgent.generate_fix 签名变更

```python
def generate_fix(
    self,
    ci_logs: str,
    error_locations: list[ErrorLocation],
    original_codes: dict[str, str] | None = None,
    review_feedback: ReviewFeedback | None = None,
    test_feedback: TestFeedback | None = None,
    mandatory_instructions: str = "",
    fix_history: list[FixAttempt] | None = None,
    retry_count: int = 0,
) -> dict:
```

### 4.5 agent_factory.py

```python
class AgentFactory:
    """统一创建和配置所有 Agent"""

    def __init__(self, config: AgentConfig):
        self.config = config

    def create_fix_agent(self, repo_path: str) -> FixAgent:
        """创建修复 Agent，统一注入配置"""
        return FixAgent(
            repo_path=repo_path,
            llm_model=self.config.llm_model,
            temperature=self.config.fix_temperature,
            openai_api_key=self.config.openai_api_key,
            openai_base_url=self.config.openai_base_url,
            github_token=self.config.github_token,
        )

    def create_review_agent(self) -> ReviewAgent:
        return ReviewAgent(
            llm_model=self.config.llm_model,
            temperature=0.0,
            openai_api_key=self.config.openai_api_key,
            openai_base_url=self.config.openai_base_url,
            max_change_lines=self.config.max_change_lines,
        )

    def create_test_agent(self, repo_path: str) -> TestAgent:
        return TestAgent(
            repo_path=repo_path,
            llm_model=self.config.llm_model,
            openai_api_key=self.config.openai_api_key,
            openai_base_url=self.config.openai_base_url,
        )
```

### 4.6 security_rules.py（安全规则单一源）

```python
class SecurityRule(BaseModel):
    pattern: str          # 正则表达式
    severity: str         # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    category: str         # "code_exec" | "data_loss" | "auth" | "file_op" | "network"
    description: str      # 人类可读描述
    safe_alternative: str # 给 FixAgent 看的安全替代方案
    check_new_only: bool = True  # True=仅检查新引入的风险

CRITICAL_RULES: list[SecurityRule] = [
    SecurityRule(
        pattern=r'\bos\.system\s*\(',
        severity="CRITICAL",
        category="code_exec",
        description="os.system() 可执行任意系统命令",
        safe_alternative="使用 subprocess.run() 并显式指定命令列表",
    ),
    # ... eval, exec, __import__, compile, etc.
]

HIGH_RULES: list[SecurityRule] = [
    # ... 不再包含 x = None 的单独匹配（修复了 bug）
    # 破坏性赋值改为多行上下文检查
]

def get_rules_by_severity(severity: str) -> list[SecurityRule]:
    """供 ReviewAgent 和 TestAgent 调用"""
    ...

def get_fix_agent_security_section() -> str:
    """生成 FixAgent 提示词中的安全部分"""
    ...
```

### 4.7 review_agent.py 调整

**review_changes 新增 rejection_reason 枚举**：
- LLM 输出增加 `rejection_reason` 字段，必须从以下枚举选择：
  - `original_error_unresolved` — 原始 CI 错误未被修复
  - `new_bug_introduced` — 修复引入了新的功能性错误
  - `contract_break` — 修改了函数签名或返回值类型

**原始代码问题归入 risk_warnings**：
- 在 REVIEW_AGENT_SYSTEM_PROMPT 中增加：
  > "如果修复正确地解决了原始 CI 错误，并且没有引入新的功能性错误，则 review_passed 必须为 true。原始代码中已存在的代码质量问题、安全隐患，只要不是本次修复新引入的，一律放入 risk_warnings，不得因此拒绝。"

**安全扫描引用统一源**：
- 删除 `_CRITICAL_PATTERNS` 和 `_HIGH_PATTERNS` 内联定义
- 改为 `from src.agent.security_rules import CRITICAL_RULES, HIGH_RULES`

**删除 original_codes 回退到磁盘的路径**：
- 移除 `_read_original_from_disk` 的 fallback 逻辑
- original_codes 必须由 Orchestrator 传入，不存在时直接报错

### 4.8 test_agent.py 调整

**_route_after_test 新逻辑**：
```python
def _route_after_test(state: RepairState) -> str:
    if state.validation_status == "success":
        return "create_pr"
    if state.validation_status == "uncertain":
        return "create_pr"  # 不再重试

    # validation_status == "failure"
    if _all_import_errors(state.error_locations):
        return "create_pr"  # 导入类错误修复后无法运行验证，这是预期的

    if state.retry_count < state.max_retries:
        return "fix_agent"

    return "handle_failure"
```

**清理死代码**：
- 删除未使用的 `self.llm` 实例化
- 删除未使用的 `TEST_AGENT_SYSTEM_PROMPT` / `TEST_AGENT_USER_PROMPT` 导入

### 4.9 notification.py

将飞书通知逻辑从 orchestrator.py 中提取出来：
```python
class NotificationService:
    def send_pr_created(self, pr_url: str, pr_number: int, ...): ...
    def send_failure(self, error_message: str, ...): ...
    def build_pr_body(self, state: RepairState) -> str: ...
```

### 4.10 orchestrator.py 简化

保留的职责：
- `_build_graph()` — 图构建
- `_route_after_review(state)` — 审查后路由
- `_route_after_test(state)` — 测试后路由（简化）
- `_route_after_create_pr(state)` — PR 后路由
- `run(event, ci_logs)` — 入口

节点实现保留在 orchestrator 中但简化（Agent 创建委托给 factory，校验委托给 gate，通知委托给 notification）。

### 4.11 _build_retry_context（Orchestrator 中的辅助函数）

```python
def _build_retry_context(
    state: RepairState,
    rejection_source: str,  # "gate" | "review" | "test"
    rejection_data: dict,    # 来自 Gate 的 ValidationResult 或 Review 的返回
) -> dict:
    """纯规则引擎。根据拒绝来源和结构化数据生成重试上下文。"""

    # 映射 rejection_source + rejection_data 到 rejection_reason
    if rejection_source == "gate":
        reason = rejection_data.get("violation_type", "unknown")
    elif rejection_source == "review":
        reason = rejection_data.get("rejection_reason", "original_error_unresolved")
    elif rejection_source == "test":
        reason = "test_failure"
    else:
        reason = "unknown"

    # 生成强制指令
    instruction_kwargs = _extract_instruction_kwargs(state, rejection_data)
    instruction = generate_instruction(reason, **instruction_kwargs)

    # 生成历史记录
    attempt = FixAttempt(
        attempt=state.retry_count + 1,
        diff_summary=(state.diff_content or "")[:200],
        rejection_reason=reason,
        rejected_by=rejection_source,
    )

    return {
        "fix_history": state.fix_history + [attempt],
        "mandatory_instructions": instruction,
    }
```

---

## 五、预期的 Token 消耗对比

| 场景 | 改造前 | 改造后 | 节省 |
|------|--------|--------|------|
| ImportError（一次通过） | Fix(~8K) + Review(~5K) + Test(~3K) = 16K | Fix(~8K) + Gate(0) + Review(~5K) + Test(~3K) = 16K | 0（正常路径无变化） |
| ImportError（1次越界重试） | Fix(8K)×2 + Review(5K)×2 + Test(3K) = 29K | Fix(8K)×2 + Gate(0)×2 + Review(5K) + Test(3K) = 24K | ~17% |
| ImportError（2次越界重试） | Fix(8K)×3 + Review(5K)×3 + Test(3K) = 42K | Fix(8K)×3 + Gate(0)×3 + Review(5K) + Test(3K) = 32K | ~24% |
| **平均估算** | ~35K | ~14K | **~60%** |

实际节省更高，因为：
- Gate 拦截后 FixAgent 收到的强制性指令更短更聚焦
- 审查/测试不再参与越界重试循环
- uncertain 直接结束，不重试

---

## 六、实施顺序

按依赖关系和风险排序：

| 序号 | 模块 | 依赖 | 风险 |
|------|------|------|------|
| 1 | `state.py` — Pydantic 模型定义 | 无 | 中（影响所有文件） |
| 2 | `security_rules.py` — 安全规则统一 | 无 | 低 |
| 3 | `instruction_templates.py` — 强制指令模板 | 无 | 低 |
| 4 | `prompts/fix_agent.py` — 提示词重构 | state | 中 |
| 5 | `prompts/review_agent_prompts.py` — 审查调整 | state | 低 |
| 6 | `subagents/fix_agent.py` — 签名变更 | state, prompts | 中 |
| 7 | `subagents/review_agent.py` — 安全规则引用 + rejection_reason | state, security_rules | 中 |
| 8 | `subagents/test_agent.py` — 路由 + 清理 | state | 低 |
| 9 | `validation_gate.py` — 硬校验门禁 | state | 中 |
| 10 | `agent_factory.py` — Agent 工厂 | state, subagents | 低 |
| 11 | `notification.py` — 通知服务 | state | 低 |
| 12 | `orchestrator.py` — 图重构 + 集成 | 全部上述 | 高 |
| 13 | 运行测试验证 | 全部 | — |

---

## 七、验证标准

修改完成后以 ModuleNotFoundError: No module named 'requests' 场景验证：

1. ✅ 修复 Agent 只删除 `import requests` 行或用 `try/except` 包裹，不触碰函数体
2. ✅ 后置校验通过（所有修改行都是 import 相关）
3. ✅ 审查 Agent 一次通过，review_passed = true
4. ✅ 测试 Agent 可能返回 uncertain，但流程直接创建 PR，不再重试
5. ✅ Token 消耗降低 60% 以上
6. ✅ 无回归错误，现有测试全部通过
