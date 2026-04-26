"""修复流程状态模型 - 遵循LangGraph最佳实践"""
from typing import List, Dict, Optional, Annotated, TypedDict
import operator
from src.bus.schemas import GitHubEvent


class RepairState(TypedDict):
    """修复流程状态模型，定义整个修复过程中的所有数据

    遵循LangGraph规范：
    - 使用TypedDict而非Pydantic BaseModel
    - 对列表字段使用Annotated + operator.add作为reducer，实现追加而非覆盖
    """

    # 输入事件（单次写入，无reducer）
    event: GitHubEvent

    # 上下文收集阶段
    ci_logs: str  # CI失败日志内容
    repo_path: str  # 本地仓库临时路径
    error_locations: Annotated[List[Dict], operator.add]  # 错误位置列表，支持追加

    # 修复阶段
    fix_description: str  # 修复方案描述
    modified_files: List[str]  # 修改的文件路径列表（节点返回时直接替换，不追加）
    code_changes: Dict[str, str]  # 代码变更内容，key为文件路径，value为修改后的完整内容
    original_codes: Dict[str, str]  # 原始代码，key为文件路径，value为修复前的原始内容
    diff_content: str  # 修复的diff内容

    # 审查阶段
    review_passed: bool  # 审查是否通过
    review_comments: str  # 审查意见
    change_lines: int  # 变更总行数（新增+删除）
    risk_warnings: Annotated[List[str], operator.add]  # 风险警告列表，支持追加
    has_critical_risks: bool  # 是否存在致命风险（CRITICAL级）
    has_high_risks: bool  # 是否存在高危风险（HIGH级）
    risk_level: str  # 最高风险等级: CRITICAL / HIGH / MEDIUM / LOW / NONE

    # 测试阶段
    test_passed: bool  # 测试是否通过（向后兼容）
    test_output: str  # 测试输出内容
    failed_tests: Annotated[List[str], operator.add]  # 失败的测试用例列表，支持追加

    # 动态验证字段（v2 核心改进）
    validation_status: str  # "success" | "failure" | "uncertain" | ""
    validation_method: str  # "command" | "ast" | "fallback_test" | "none" | ""
    validation_command: str  # 实际执行的验证命令

    # 最终结果
    pr_url: Optional[str]  # 生成的PR链接
    pr_number: Optional[int]  # PR编号
    success: bool  # 整体修复流程是否成功
    error_message: str  # 流程失败时的错误信息

    # 流程控制字段
    retry_count: int  # 修复重试次数，默认0（不使用operator.add，节点返回直接替换）
    max_retries: int  # 最大重试次数，默认3

