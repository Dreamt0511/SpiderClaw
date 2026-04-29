# 修复 Agent 与审查 Agent 职责分离设计

## 背景

当前 SpiderClaw 修复流程存在根本性的架构问题：FixAgent 被要求同时完成两个互斥的目标——"最小变更修复 CI 错误"和"顺手修复已有安全问题"。这导致：

1. 变更行数超限：安全修复增加了不必要的变更行数，触发门禁拒绝
2. 重试绕不开：重试时跳过安全修复 → 门禁通过 → 审查 Agent 发现残留风险 → 再次拒绝
3. 不可能三角：修复所有错误 vs 控制在行数限制内 vs 顺手修安全问题

## 新架构：关注点分离

将修复和安全职责拆分为两个独立的 Agent：

| Agent | 职责 | 工具权限 | 触发条件 |
|-------|------|---------|---------|
| **FixAgent** | 只修复 CI 错误 | read_file, search_files | 错误事件到达 |
| **ReviewAgent Phase 1** | 审查修复正确性 | 无（直接 LLM 调用） | FixAgent 门禁通过 |
| **ReviewAgent Phase 2** | （按需）修复已有安全问题 | write_file (通过中间件授权) | Phase 1 通过 + 存在 kept_risks |

## 流程图

```
START → collect_context → fix_agent → fix_gate → review_agent → run_tests → create_pr → END
                                        ↑            ↓ (review failed)
                                        └── fix_agent ←──┘
```

其中 `review_agent` 内部：

```
review_changes()
  │
  ├─ 静态安全检查 (Python, 区分 new_risks / kept_risks)
  │
  ├─ Phase 1: LLM 审查 (直接调用, 无工具)
  │    ├─ review_passed=false → 返回 orchestrator 路由到 fix_agent 重试
  │    └─ review_passed=true → 继续
  │
  ├─ 检查是否触发 Phase 2
  │    ├─ kept_risks 为空 → 直接返回 review_passed=true
  │    └─ kept_risks 存在 (critical/high) → 进入 Phase 2
  │
  └─ Phase 2: 安全修复 (按需)
       ├─ 中间件启用 write_file 权限
       ├─ create_agent(model, tools=[read_file, write_file], middleware=...)
       ├─ Agent 读取文件并修复安全问题
       ├─ 重跑静态检查 (确保无新风险)
       ├─ 重跑文件完整性 (确保未遗漏错误文件)
       └─ 返回最终结果
```

## 门禁设计

### Fix 门禁 (validation_gate.py)

保留现有检查：
- `_check_import_error` — import 越界检查
- `_check_syntax_error` — syntax 行范围检查
- `_check_change_limit` — 变更行数限制

新增：
- `_check_file_completeness` — 所有错误文件必须在 code_changes 中

### Review 门禁 (内置在 ReviewAgent)

- `_check_security` — 重跑静态检查，确认安全修复未引入新风险
- `_check_file_completeness` — 安全修复未遗漏错误文件
- `_check_syntax` — 语法正确性

## 组件变更详情

### 1. prompts/fix_agent.py — FixAgent 提示词精简

**删除**：
- `{get_fix_agent_security_section()}` 整个安全敏感操作区域
- 输出前自检清单（与门禁硬约束重复）
- 函数契约保护大段内容（精简到 1-2 句）
- 重复的最小修改说明
- 修复策略中的示例代码

**保留**：
- 角色定义（1 句）
- 绝对修改边界表（按错误类型限定的范围）
- 根因优先处理规则
- 门禁硬约束（`__MAX_CHANGE_LINES__` 占位符）
- 无 file_path 处理策略（精简）
- 禁止行为（精简到 3-4 条）
- 输出格式

目标：从 ~170 行精简到 ~80 行。

### 2. security_rules.py — 安全规则源

- `get_fix_agent_security_section()` 不再被 FixAgent 调用
- 该函数改为 ReviewAgent 专用，用于安全修复 prompt
- 优先级规则改为面向 ReviewAgent：
  1. 修复检测到的安全问题
  2. 不修改与安全问题无关的代码
  3. 不修改 FixAgent 的修复结果（增加新修复而非覆盖）

### 3. subagents/review_agent.py — 重写为带中间件的 Agent

**变更**：
- 从直接 LLM 调用改为 `create_agent(model, tools=[read_file, write_file, search_files], middleware=[ReviewPhaseAuth()])`
- 静态安全检查逻辑保留（Python 代码）
- 新增 Phase 2 安全修复逻辑

**ReviewPhaseAuth 中间件**：

```python
class ReviewPhaseAuth(AgentMiddleware):
    """根据阶段控制 write_file 工具可用性"""
    def __init__(self):
        self.security_fix_mode = False

    def enable_security_fix_mode(self):
        """Phase 2 前调用，授权 write_file"""
        self.security_fix_mode = True

    def disable_security_fix_mode(self):
        """Phase 2 后调用，回收 write_file"""
        self.security_fix_mode = False

    def wrap_model_call(self, request, handler):
        if not self.security_fix_mode:
            tools = [t for t in request.tools if t.name != "write_file"]
            return handler(request.override(tools=tools))
        return handler(request)
```

**review_changes() 新流程**：

```python
async def review_changes(self, ...):
    # 1. 静态安全检查
    static_result = self._static_security_check(...)
    
    # 2. Phase 1: LLM 审查（无工具）
    if static_result["has_critical_risks"]:
        return {"review_passed": False, ...}
    
    review_result = await self._llm_review(static_result, ...)
    
    if not review_result["review_passed"]:
        return review_result
    
    # 3. Phase 2: 安全修复（按需）
    kept_risks = static_result.get("kept_risks", {})
    if self._has_kept_risks(kept_risks):
        self._auth.enable_security_fix_mode()
        fix_result = await self.agent.ainvoke({
            "input": self._build_security_fix_prompt(kept_risks, code_changes)
        })
        self._auth.disable_security_fix_mode()
        
        # 更新 code_changes
        updated_changes = self._apply_security_fixes(fix_result, code_changes)
        
        # 重跑检查
        post_check = self._static_security_check(updated_changes, original_codes)
        completeness = self._check_file_completeness(updated_changes, error_locations)
        
        review_result["code_changes"] = updated_changes
        review_result["security_fixes_applied"] = True
    
    return review_result
```

### 4. prompts/review_agent_security_fix.py (新增)

安全修复专用 prompt，包含：
- 当前代码中的安全问题列表（kept_risks）
- 要求用 write_file 工具修复
- 禁止修改与安全问题无关的代码
- 禁止修改 FixAgent 已修复的部分（除非修复涉及同一行）

### 5. validation_gate.py — 新增文件完整性检查

```python
def _check_file_completeness(
    fix_result: dict,
    error_locations: list[ErrorLocation],
) -> ValidationResult:
    """检查所有错误文件是否都在 code_changes 中"""
    expected = set()
    for err in error_locations:
        fp = err.file_path if hasattr(err, "file_path") else err.get("file_path", "")
        if fp and fp != "<string>":
            expected.add(fp.replace("\\", "/").removeprefix("./"))
    
    actual = set(fix_result.get("code_changes", {}).keys())
    missing = expected - actual
    
    if missing:
        return ValidationResult(
            passed=False,
            violation_type="file_incomplete",
            details=f"遗漏文件: {', '.join(sorted(missing))}",
            error_context={"missing_files": list(missing)},
        )
    return ValidationResult(passed=True)
```

在 `validate_fix()` 中调用。

### 6. orchestrator.py — 路由调整

- `_run_fix_agent`：去掉安全内容注入（system_override 不再需要说"忽略安全建议"）
- `_review_changes`：适配新的两阶段返回结果
- `_route_after_review`：路由逻辑不变（review_passed 决定是否重试）
- 门禁增加文件完整性检查

### 7. agent_factory.py

```python
def create_review_agent(self) -> ReviewAgent:
    return ReviewAgent(
        llm_model=self.config.llm_model,
        temperature=self.config.review_temperature,
        openai_api_key=self.config.openai_api_key,
        openai_base_url=self.config.openai_base_url,
        max_change_lines=self.config.max_change_lines,
        github_token=self.config.github_token,  # 新增：ReviewAgent 需要 token 用于文件操作
    )
```

## 边界情况处理

| 场景 | 处理方式 |
|------|---------|
| 无 kept_risks | Phase 2 跳过，直接去 run_tests |
| Phase 2 安全修复引入新风险 | 重跑静态检查捕获 → 记录警告但不阻止流程（安全修复利大于弊） |
| Phase 2 修复导致文件被遗漏 | 文件完整性检查捕获 → 返回 warning，ReviewAgent 再次尝试 |
| FixAgent 遗漏了错误文件 | Fix 门禁的文件完整性检查拒绝 → 自动补齐（现有逻辑） |
| kept_risks 过多超出变更行数限制 | Phase 2 也有行数预算控制，超限则跳过剩余风险 |

## 验证方法

1. **单元测试**：`pytest tests/ -v` 确保所有现有测试通过
2. **FixAgent prompt 验证**：确认 get_fix_agent_security_section() 不再出现在提示词中
3. **ReviewAgent 权限验证**：
   - Phase 1 调用 write_file → 被中间件拒绝
   - Phase 2 调用 write_file → 正常执行
4. **文件完整性验证**：构造缺少文件的修复 → 门禁正确拒绝
5. **端到端流程验证**：实际触发一个包含安全问题的 CI 错误 → FixAgent 只修错误 → ReviewAgent 审查 → 安全修复
