"""修复流程状态模型 — Pydantic BaseModel，运行时类型校验"""

from typing import Any
from pydantic import BaseModel, Field


# === 基础类型 ===

class ErrorLocation(BaseModel):
    """错误位置信息"""
    file_path: str = ""
    line_number: int = 0
    error_type: str = ""   # "ModuleNotFoundError", "SyntaxError", "NameError", ...
    error_message: str = ""
    traceback: str = ""
    source: str = ""       # "traceback", "syntax_error", "simple", "pytest"
    is_root_cause: bool = False         # 是否为链式错误的根因
    chain_consequence: str = ""         # 由根因导致的后果错误描述
    ci_stage: str = ""     # "syntax" | "runtime" | "test" | "unknown"


class FixAttempt(BaseModel):
    """单次修复尝试记录"""
    attempt: int
    diff_summary: str = ""          # 本次修改摘要（截断到 200 字符）
    rejection_reason: str = ""      # 被拒原因
    rejected_by: str = ""           # "gate" | "review" | "test"


class ReviewFeedback(BaseModel):
    """审查的结构化反馈"""
    passed: bool
    rejection_reason: str = ""  # original_error_unresolved | new_bug_introduced | contract_break
    comments: str = ""
    risk_warnings: list[str] = []


class TestFeedback(BaseModel):
    """测试的结构化反馈"""
    status: str = ""  # "success" | "failure" | "uncertain"
    failed_tests: list[str] = []
    output: str = ""
    new_errors: list[str] = []


# === AgentContext（传递给每个 Agent 的统一上下文） ===

class AgentContext(BaseModel):
    """Agent 间通信的正式契约"""
    error_locations: list[ErrorLocation] = []
    original_codes: dict[str, str] = {}     # 文件路径 → 原始内容（不可变快照）
    fix_history: list[FixAttempt] = []
    mandatory_instructions: str = ""         # 强制性修复指令
    review_feedback: ReviewFeedback | None = None
    test_feedback: TestFeedback | None = None
    retry_count: int = 0
    max_retries: int = 3


# === RepairState（LangGraph State） ===

class RepairState(BaseModel):
    """修复流程状态 — LangGraph Pydantic State

    支持 dict 式访问以兼容现有代码: state["key"] 等价于 state.key
    """

    model_config = {"extra": "allow", "arbitrary_types_allowed": True}

    def __getitem__(self, key: str):
        return getattr(self, key)

    def __setitem__(self, key: str, value):
        setattr(self, key, value)

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    # --- 输入层 ---
    event: Any = None             # GitHubEvent（序列化后的 dict 或原始对象）
    ci_logs: str = ""
    repo_path: str = ""

    # --- 上下文层 ---
    error_locations: list[ErrorLocation] = []
    target_files: list[str] = Field(default_factory=list)  # 由 orchestrator 从 error_locations 提取的确定性文件列表
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
    risk_warnings: list[str] = []    # 显式合并，不用 operator.add
    risk_level: str = "NONE"         # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "NONE"
    rejection_reason: str = ""       # 审查拒绝原因枚举

    # --- 测试层 ---
    validation_status: str = ""      # "success" | "failure" | "uncertain"
    validation_method: str = ""      # 验证方式
    validation_command: str = ""     # 实际执行的验证命令
    test_output: str = ""
    failed_tests: list[str] = []    # 显式合并，不用 operator.add

    # --- 结果层 ---
    pr_url: str = ""
    pr_number: int = 0
    success: bool = False
    error_message: str = ""

    # --- 控制层 ---
    retry_count: int = 0
    max_retries: int = 3
    current_phase: str = ""          # 阶段追踪
    is_env_error: bool = False       # 环境/依赖错误标记，无需代码修复
