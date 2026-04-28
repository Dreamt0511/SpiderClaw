"""修复流程编排器 — 图构建 + 路由 + 节点实现"""

import asyncio
import datetime
import logging
import os
import re
from typing import Any

from git import Repo
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command

from src.agent.state import RepairState, ErrorLocation, FixAttempt
from src.agent.agent_factory import AgentFactory, AgentConfig
from src.agent.validation_gate import validate_fix
from src.agent.instruction_templates import generate_instruction
from src.agent.notification import NotificationService
from src.agent.tools import (
    set_tool_context,
    clone_repository,
    download_ci_logs,
    parse_python_errors,
    get_diff,
    create_branch,
    commit_changes,
    push_branch,
    create_pull_request,
)
from src.agent.tools.langchain_tools import read_file, write_file
from src.bus.schemas import GitHubEvent

logger = logging.getLogger(__name__)


def _all_import_errors(error_locations: list) -> bool:
    """检查所有错误是否都是导入类错误"""
    if not error_locations:
        return False
    for e in error_locations:
        et = e.error_type if hasattr(e, 'error_type') else e.get('error_type', '')
        if et not in ('ModuleNotFoundError', 'ImportError'):
            return False
    return True


class RepairOrchestrator:
    """修复流程编排器"""

    def __init__(
        self,
        github_token: str,
        openai_api_key: str,
        openai_base_url: str = "https://api.openai.com/v1",
        llm_model: str = "gpt-4o",
        max_retries: int = 3,
        max_change_lines: int = 50,
        lark_notify_enabled: bool = False,
        lark_notify_users: list[str] = None,
    ):
        self.github_token = github_token
        self.max_retries = max_retries

        # Agent 配置
        agent_config = AgentConfig(
            llm_model=llm_model,
            openai_api_key=openai_api_key,
            openai_base_url=openai_base_url,
            github_token=github_token,
            max_change_lines=max_change_lines,
        )
        self.agent_factory = AgentFactory(agent_config)
        self.notification = NotificationService(
            enabled=lark_notify_enabled,
            notify_users=lark_notify_users or [],
        )

        self.processed_events: set[str] = set()
        self.lock = asyncio.Lock()

        self.graph = self._build_graph()

    # ==================== 图构建 ====================

    def _build_graph(self):
        workflow = StateGraph(RepairState)

        workflow.add_node("collect_context", self._collect_context)
        workflow.add_node("fix_agent", self._run_fix_agent)
        workflow.add_node("validation_gate", self._validation_gate)
        workflow.add_node("review_changes", self._review_changes)
        workflow.add_node("run_tests", self._run_tests)
        workflow.add_node("create_pr", self._create_pull_request)
        workflow.add_node("handle_failure", self._handle_failure)

        workflow.add_edge(START, "collect_context")
        workflow.add_conditional_edges(
            "collect_context",
            self._route_after_context,
            ["fix_agent", "handle_failure", END],
        )
        # fix_agent → validation_gate (via Command)
        # validation_gate → review_changes or fix_agent (via Command)

        workflow.add_conditional_edges(
            "review_changes",
            self._route_after_review,
            ["fix_agent", "run_tests", "handle_failure"],
        )
        workflow.add_conditional_edges(
            "run_tests",
            self._route_after_test,
            ["fix_agent", "create_pr", "handle_failure"],
        )
        workflow.add_conditional_edges(
            "create_pr",
            self._route_after_create_pr,
            [END, "handle_failure"],
        )
        workflow.add_edge("handle_failure", END)

        return workflow.compile()

    # ==================== 重试上下文构建 ====================

    @staticmethod
    def _build_retry_context(
        state: RepairState,
        rejection_source: str,
        violation_type: str = "",
        rejection_data: dict | None = None,
    ) -> dict:
        """纯规则引擎。根据拒绝来源和结构化数据生成重试上下文。"""
        rejection_data = rejection_data or {}

        # 确定 rejection_reason
        if rejection_source == "gate":
            reason = violation_type or rejection_data.get("violation_type", "boundary_violation")
        elif rejection_source == "review":
            reason = rejection_data.get("rejection_reason", "original_error_unresolved")
        elif rejection_source == "test":
            reason = "test_failure"
        else:
            reason = "unknown"

        # 提取指令上下文
        instruction_kwargs = {}
        if rejection_source == "gate":
            ctx = rejection_data.get("error_context", {})
            instruction_kwargs.update(ctx)
            instruction_kwargs["details"] = rejection_data.get("details", "")
        elif rejection_source == "review":
            instruction_kwargs["error_type"] = (
                state.error_locations[0].error_type
                if state.error_locations and hasattr(state.error_locations[0], 'error_type')
                else "UnknownError"
            )
            instruction_kwargs["file_path"] = (
                state.error_locations[0].file_path
                if state.error_locations and hasattr(state.error_locations[0], 'file_path')
                else "unknown"
            )
            instruction_kwargs["line_number"] = str(
                state.error_locations[0].line_number
                if state.error_locations and hasattr(state.error_locations[0], 'line_number')
                else "?"
            )
        elif rejection_source == "test":
            ft = rejection_data.get("failed_tests", state.failed_tests or [])
            instruction_kwargs["n"] = len(ft)
            instruction_kwargs["failed_tests"] = ", ".join(ft)

        instruction = generate_instruction(reason, **instruction_kwargs)

        attempt = FixAttempt(
            attempt=state.retry_count,
            diff_summary=(state.diff_content or "")[:200],
            rejection_reason=reason,
            rejected_by=rejection_source,
        )

        return {
            "fix_history": state.fix_history + [attempt],
            "mandatory_instructions": instruction,
        }

    # ==================== 节点: collect_context ====================

    async def _collect_context(self, state: RepairState) -> dict[str, Any]:
        event: GitHubEvent = state["event"]
        logger.info(f"收集上下文: {event.event_id}, 仓库: {event.repository}")

        try:
            # 事件去重
            event_key = self._make_event_key(event)
            async with self.lock:
                if event_key in self.processed_events:
                    logger.info(f"事件 {event_key} 已处理过，跳过")
                    return {"success": False, "error_message": "事件已处理过"}
                self.processed_events.add(event_key)

            set_tool_context({"github_token": self.github_token})

            # 1. 获取 CI 日志
            ci_logs = state.get("ci_logs", "")
            if not ci_logs and event.logs_url:
                logs_result = download_ci_logs.invoke({"logs_url": event.logs_url})
                if not logs_result.startswith("Error:"):
                    ci_logs = logs_result

            # 2. 解析错误
            error_locations_raw = []
            if ci_logs:
                error_locations_raw = parse_python_errors.invoke({"log_content": ci_logs})
                # 保存到文件
                os.makedirs("src/logs", exist_ok=True)
                with open("src/logs/debug_ci_logs.txt", "a", encoding="utf-8") as f:
                    f.write(ci_logs + "\n")
                logger.info(f"CI日志已保存到 src/logs/debug_ci_logs.txt，长度: {len(ci_logs)}")

            logger.info(f"解析到Python错误数量: {len(error_locations_raw)}")
            if not error_locations_raw:
                async with self.lock:
                    self.processed_events.discard(event_key)
                return {"success": False, "error_message": "日志中未检测到Python错误"}

            # 3. 克隆仓库
            if not event.clone_url or not event.branch:
                async with self.lock:
                    self.processed_events.discard(event_key)
                return {"success": False, "error_message": "缺少克隆信息"}

            clone_result = clone_repository.invoke(
                {"clone_url": event.clone_url, "branch": event.branch}
            )
            if clone_result.startswith("Error:"):
                async with self.lock:
                    self.processed_events.discard(event_key)
                return {"success": False, "error_message": clone_result}

            repo_path = clone_result
            set_tool_context({"github_token": self.github_token, "repo_path": repo_path})

            # 4. 过滤有效错误
            error_locations = self._filter_valid_errors(error_locations_raw, repo_path)
            if not error_locations:
                async with self.lock:
                    self.processed_events.discard(event_key)
                return {"success": False, "error_message": "过滤后没有有效错误"}

            # 5. 错误分类处理
            classification = self._classify_errors(error_locations, repo_path, ci_logs)
            if classification.get("early_return"):
                # 确保 early_return 携带完整上下文，避免下游节点报"缺少必要上下文"
                return {
                    **classification["early_return"],
                    "repo_path": repo_path,
                    "ci_logs": ci_logs,
                    "error_locations": error_locations,
                }

            return {
                "ci_logs": classification.get("ci_logs", ci_logs),
                "repo_path": repo_path,
                "error_locations": classification.get("error_locations", error_locations),
                "retry_count": 0,
                "max_retries": self.max_retries,
                "review_comments": "",
                "test_output": "",
                "risk_warnings": [],
                "failed_tests": [],
                "risk_level": "NONE",
            }

        except Exception as e:
            logger.error(f"收集上下文失败: {e}", exc_info=True)
            return {"success": False, "error_message": f"收集上下文失败: {str(e)}"}

    @staticmethod
    def _make_event_key(event: GitHubEvent) -> str:
        if event.pr_number and event.branch:
            return f"{event.repository}:pr:{event.pr_number}:branch:{event.branch}"
        if event.pr_number:
            return f"{event.repository}:pr:{event.pr_number}"
        if isinstance(event.payload, dict) and "head_sha" in event.payload:
            return f"{event.repository}:sha:{event.payload['head_sha']}"
        return f"{event.repository}:event:{event.event_id}"

    @staticmethod
    def _filter_valid_errors(error_locations: list[dict], repo_path: str) -> list[ErrorLocation]:
        """过滤有效错误，处理 CI 路径前缀（/github/workspace/ 等）到本地路径的映射"""
        valid = []
        repo_path_abs = os.path.abspath(repo_path)

        # CI 环境常见路径前缀（GitHub Actions）
        CI_PATH_PREFIXES = ["/github/workspace/", "/home/runner/work/"]

        def _strip_ci_prefix(fp: str) -> str:
            for prefix in CI_PATH_PREFIXES:
                if fp.startswith(prefix):
                    stripped = fp[len(prefix):]
                    # 处理 /home/runner/work/{repo}/{repo}/./path 格式
                    dot_slash = stripped.find('/./')
                    if dot_slash != -1:
                        stripped = stripped[dot_slash + 3:]
                    return stripped
            return fp

        for err in error_locations:
            fp = err.get("file_path", "")
            base_fields = {
                "error_type": err.get("error_type", ""),
                "error_message": err.get("error_message", ""),
                "line_number": err.get("line_number", 0),
                "source": err.get("source", ""),
                "traceback": err.get("traceback", ""),
                "is_root_cause": err.get("is_root_cause", False),
                "chain_consequence": err.get("chain_consequence", ""),
                "ci_stage": err.get("ci_stage", ""),
            }
            if not fp or fp == "<string>":
                if err.get("error_type") and err.get("error_message"):
                    valid.append(ErrorLocation(file_path="", **base_fields))
                continue

            # CI 环境绝对路径 → 剥离前缀后按相对路径处理
            ci_stripped = _strip_ci_prefix(fp)
            if ci_stripped != fp:
                cleaned = ci_stripped.lstrip("/\\").replace("\\", "/")
                full = os.path.abspath(os.path.join(repo_path, cleaned))
                if full.startswith(repo_path_abs) and os.path.isfile(full):
                    rel = os.path.relpath(full, repo_path_abs).replace("\\", "/")
                    valid.append(ErrorLocation(file_path=rel, **base_fields))
                continue

            # 本地绝对路径 → 必须在仓库目录内才是项目文件
            if os.path.isabs(fp):
                abs_fp = os.path.abspath(fp)
                if abs_fp.startswith(repo_path_abs) and os.path.isfile(abs_fp):
                    rel = os.path.relpath(abs_fp, repo_path_abs).replace("\\", "/")
                    valid.append(ErrorLocation(file_path=rel, **base_fields))
                continue

            # 修复 lstrip 吞掉点号目录的问题（如 ./.github/tt.py → .github/tt.py 而非 github/tt.py）
            cleaned = fp.lstrip("/\\").replace("\\", "/")
            if cleaned.startswith('./') or cleaned.startswith('.\\'):
                cleaned = cleaned[2:]
            full = os.path.abspath(os.path.join(repo_path, cleaned))
            if full.startswith(repo_path_abs) and os.path.isfile(full):
                rel = os.path.relpath(full, repo_path_abs).replace("\\", "/")
                valid.append(ErrorLocation(file_path=rel, **base_fields))

        logger.info(f"有效错误: {len(valid)}/{len(error_locations)}")
        return valid

    def _classify_errors(
        self, error_locations: list[ErrorLocation], repo_path: str, ci_logs: str
    ) -> dict:
        """错误分类：A类(文件缺失) / B类(语法/逻辑) / C类(依赖)"""
        is_syntax_error = False
        syntax_error_files = []

        for err in error_locations:
            et = err.error_type.lower()
            em = err.error_message.lower()

            if et == "syntaxerror" or "syntaxerror" in em:
                is_syntax_error = True
                if err.file_path and err.file_path not in syntax_error_files:
                    syntax_error_files.append(err.file_path)

        # A类: 文件缺失
        for err in error_locations:
            em = err.error_message.lower()
            if "no such file or directory" in em or "could not open" in em:
                file_match = re.search(r"No such file or directory: '([^']+)'", err.error_message)
                if not file_match:
                    file_match = re.search(r"Could not open '([^']+)'", err.error_message)
                if file_match:
                    missing = file_match.group(1)
                    if "requirements.txt" in missing:
                        logger.info(f"检测到缺失文件: {missing}")
                        write_file.invoke({"file_path": f"{repo_path}/{missing}", "content": ""})
                        return {"early_return": {
                            "success": True,
                            "fix_description": f"创建缺失文件: {missing}",
                            "modified_files": [missing],
                            "code_changes": {missing: ""},
                        }}

        # C类: 依赖问题 — 不自动跳过，交给 FixAgent 判断
        # ModuleNotFoundError/ImportError 可通过代码修复（添加 import、try/except 包裹等）
        # FixAgent 如果判定确实需要环境变更，会在响应中设置 is_env_error: true
        for err in error_locations:
            et = err.error_type.lower()
            if et in ("modulenotfounderror", "importerror"):
                logger.info(f"检测到 {et}，交由 FixAgent 处理（可代码修复）")

                # 如果错误没有文件路径，尝试从 traceback 中提取
                if not err.file_path and err.traceback:
                    file_match = re.search(r'File\s+"([^"]+\.py)"', err.traceback)
                    if file_match:
                        err.file_path = file_match.group(1)
                        line_match = re.search(r'line\s+(\d+)', err.traceback)
                        if line_match:
                            err.line_number = int(line_match.group(1))
                        logger.info(f"从 traceback 补充文件路径: {err.file_path}:{err.line_number}")

        # B类: 语法错误无文件路径
        if is_syntax_error and not syntax_error_files:
            result = self._scan_syntax_files(repo_path, error_locations, ci_logs)
            if "error" in result:
                return {"early_return": {"success": False, "error_message": result["error"]}}
            return result

        return {"error_locations": error_locations, "ci_logs": ci_logs}

    @staticmethod
    def _scan_syntax_files(repo_path: str, error_locations: list[ErrorLocation], ci_logs: str) -> dict:
        """扫描仓库查找语法错误文件"""
        import ast as _ast
        from src.agent.tools.langchain_tools import search_files

        syntax_error_files = []
        py_files = search_files.invoke({"pattern": "**/*.py"})

        for file_path in py_files:
            full_path = f"{repo_path}/{file_path}"
            try:
                content = read_file.invoke({"file_path": full_path})
                if not content.startswith("Error:"):
                    _ast.parse(content)
            except SyntaxError as e:
                if file_path not in syntax_error_files:
                    syntax_error_files.append(file_path)

                updated = False
                for i, err in enumerate(error_locations):
                    if err.error_type == "SyntaxError" and not err.file_path:
                        error_locations[i] = ErrorLocation(
                            file_path=file_path,
                            line_number=e.lineno,
                            error_type="SyntaxError",
                            error_message=str(e),
                        )
                        updated = True
                        break
                if not updated:
                    error_locations.append(ErrorLocation(
                        file_path=file_path,
                        line_number=e.lineno,
                        error_type="SyntaxError",
                        error_message=str(e),
                    ))

                ci_logs += f"\n{'='*50}\n自动扫描找到的语法错误：\n{e}\n{'='*50}\n"

        if not syntax_error_files:
            return {"error": "语法错误发生在Python系统库中，无法自动修复"}
        return {"syntax_error_files": syntax_error_files, "ci_logs": ci_logs, "error_locations": error_locations}

    # ==================== 节点: fix_agent ====================

    async def _run_fix_agent(self, state: RepairState) -> Command:
        logger.info("运行修复Agent")

        # 依赖/环境错误不需要代码修复，直接结束
        if state.get("is_env_error"):
            logger.info("环境/配置/依赖错误，无需代码修复，流程结束")
            return Command(update={"success": True}, goto=END)

        if not state["repo_path"] or not state["error_locations"] or not state["ci_logs"]:
            return Command(
                update={"success": False, "error_message": "缺少必要上下文"},
                goto="handle_failure",
            )

        try:
            # 重试时回滚变更
            if state.get("retry_count", 0) > 0:
                logger.info(f"第 {state['retry_count']} 次重试，回滚本地变更")
                repo = Repo(state["repo_path"])
                repo.git.checkout("--", ".")

            # 构建系统提示词覆盖（重试时将强制指令注入系统层，优先级高于用户提示词）
            system_override = ""
            mandatory = state.get("mandatory_instructions", "")
            if mandatory:
                system_override = (
                    "## 🔴 最高优先级系统指令（覆盖所有其他规则）\n"
                    + mandatory
                    + "\n\n此指令优先级最高。忽略下方所有与此冲突的安全修复建议、代码优化建议。"
                )

            fix_agent = self.agent_factory.create_fix_agent(
                state["repo_path"], system_prompt_override=system_override
            )

            extra_context = {}
            if state.get("review_comments"):
                extra_context["review_feedback"] = state["review_comments"]
            if state.get("risk_warnings"):
                extra_context["risk_warnings"] = state["risk_warnings"]
            if state.get("test_output"):
                extra_context["test_output"] = state["test_output"]
            if state.get("failed_tests"):
                extra_context["failed_tests"] = state["failed_tests"]

            fix_result = await fix_agent.generate_fix(
                ci_logs=state["ci_logs"],
                error_locations=state["error_locations"],
                original_codes=state.get("original_codes", {}),
                mandatory_instructions=state.get("mandatory_instructions", ""),
                fix_history=state.get("fix_history", []),
                retry_count=state.get("retry_count", 0),
                **extra_context,
            )

            # 环境错误 → 直接结束
            if fix_result.get("is_env_error", False):
                return Command(
                    update={"success": True, "error_message": fix_result["fix_description"]},
                    goto=END,
                )

            if not fix_result.get("code_changes"):
                return Command(
                    update={"success": False, "error_message": "修复Agent未能生成有效修复代码"},
                    goto="handle_failure",
                )

            # 读取原始代码
            original_codes = {}
            for fp in fix_result["code_changes"]:
                try:
                    content = read_file.invoke({"file_path": fp})
                    if not content.startswith("Error:"):
                        original_codes[fp] = content
                except Exception as e:
                    logger.warning(f"读取原始文件 {fp} 失败: {e}")

            # 获取 diff
            diff_content = get_diff.invoke({"base_branch": state["event"].branch})

            # 返回更新，路由到 validation_gate
            return Command(
                update={
                    "fix_description": fix_result["fix_description"],
                    "code_changes": fix_result["code_changes"],
                    "modified_files": fix_result["modified_files"],
                    "diff_content": diff_content,
                    "original_codes": original_codes,
                    "retry_count": state.get("retry_count", 0) + 1,
                    "current_phase": "validation",
                },
                goto="validation_gate",
            )

        except Exception as e:
            logger.error(f"修复Agent执行失败: {e}", exc_info=True)
            return Command(
                update={"success": False, "error_message": f"修复Agent执行失败: {str(e)}"},
                goto="handle_failure",
            )

    # ==================== 节点: validation_gate ====================

    async def _validation_gate(self, state: RepairState) -> Command:
        logger.info("运行校验门禁")

        validation = validate_fix(
            fix_result={
                "code_changes": state["code_changes"],
                "fix_description": state["fix_description"],
                "modified_files": state["modified_files"],
                "is_env_error": state.get("is_env_error", False),
            },
            original_codes=state["original_codes"],
            error_locations=state["error_locations"],
        )

        if validation.passed:
            # 通过：写入文件，进入审查
            for fp, content in state["code_changes"].items():
                write_file.invoke({"file_path": fp, "content": content})

            logger.info("校验通过，进入审查")
            return Command(
                update={"current_phase": "review"},
                goto="review_changes",
            )

        # 不通过：构建重试上下文
        logger.warning(f"校验失败: {validation.violation_type} - {validation.details}")
        retry_context = self._build_retry_context(
            state, "gate",
            violation_type=validation.violation_type,
            rejection_data={
                "violation_type": validation.violation_type,
                "details": validation.details,
                "error_context": validation.error_context,
            },
        )

        if state["retry_count"] < state["max_retries"]:
            return Command(
                update={
                    **retry_context,
                    "retry_count": state["retry_count"],
                    "current_phase": "retry_from_gate",
                },
                goto="fix_agent",
            )

        return Command(
            update={
                **retry_context,
                "success": False,
                "error_message": f"校验失败且重试耗尽: {validation.details}",
            },
            goto="handle_failure",
        )

    # ==================== 节点: review_changes ====================

    async def _review_changes(self, state: RepairState) -> dict[str, Any]:
        logger.info("运行审查Agent")

        try:
            review_agent = self.agent_factory.create_review_agent()
            review_result = await review_agent.review_changes(
                error_locations=state["error_locations"],
                fix_description=state["fix_description"],
                modified_files=state["modified_files"],
                code_changes=state["code_changes"],
                diff_content=state["diff_content"],
                repo_path=state["repo_path"],
                original_codes=state.get("original_codes"),
            )

            # 审查未通过时构建重试上下文，让 Fix Agent 收到强信号
            if not review_result.get("review_passed", False):
                retry_context = self._build_retry_context(
                    state, "review",
                    rejection_data={
                        "rejection_reason": review_result.get("rejection_reason", "original_error_unresolved"),
                    },
                )
                review_result.update(retry_context)

            return review_result

        except Exception as e:
            logger.error(f"审查Agent执行失败: {e}", exc_info=True)
            return {
                "review_passed": False,
                "review_comments": f"审查过程出错: {str(e)}",
                "risk_warnings": [str(e)],
                "risk_level": "NONE",
                "rejection_reason": "",
            }

    # ==================== 节点: run_tests ====================

    async def _run_tests(self, state: RepairState) -> dict[str, Any]:
        logger.info("运行测试Agent")

        try:
            test_agent = self.agent_factory.create_test_agent(state["repo_path"])
            test_result = await test_agent.verify_fix(
                error_locations=state["error_locations"],
                fix_description=state["fix_description"],
                diff_content=state["diff_content"],
                ci_logs=state.get("ci_logs", ""),
            )

            # 验证失败时构建重试上下文，让 Fix Agent 收到强信号
            if test_result.get("validation_status") == "failure":
                retry_context = self._build_retry_context(
                    state, "test",
                    rejection_data={
                        "failed_tests": test_result.get("failed_tests", []),
                    },
                )
                test_result.update(retry_context)
            logger.info(
                f"验证结果: status={test_result.get('validation_status', 'N/A')}, "
                f"method={test_result.get('validation_method', 'N/A')}"
            )
            return test_result

        except Exception as e:
            logger.error(f"测试Agent执行失败: {e}", exc_info=True)
            return {
                "validation_status": "uncertain",
                "validation_method": "error",
                "test_output": f"测试执行失败: {str(e)}",
                "failed_tests": [],
            }

    # ==================== 节点: create_pr ====================

    async def _create_pull_request(self, state: RepairState) -> dict[str, Any]:
        event: GitHubEvent = state["event"]
        logger.info(f"创建PR: {event.repository}")

        try:
            repo = Repo(state["repo_path"])

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            branch_name = f"autofix/{event.event_type}_{timestamp}"

            if create_branch.invoke({"branch_name": branch_name}) != "Success":
                return {"success": False, "error_message": "创建分支失败"}

            commit_message = f"Auto-fix: {state['fix_description']}\n\nGenerated by SpiderClaw AutoFix Agent."
            commit_result = commit_changes.invoke(
                {"message": commit_message, "files": state["modified_files"]}
            )
            if commit_result.startswith("Error:"):
                return {"success": False, "error_message": f"提交变更失败: {commit_result}"}

            set_tool_context({"repo_path": state["repo_path"], "github_token": self.github_token})

            if push_branch.invoke({"branch_name": branch_name}) != "Success":
                return {"success": False, "error_message": "推送分支失败"}

            pr_body = self.notification.build_pr_body(state, branch_name)

            pr_author_title = event.payload.get('sender', {}).get('login', '未知用户')
            pr_title = f"[SpiderClaw: fix]：对 {pr_author_title} 的 PR 进行的修复"

            pr_url = create_pull_request.invoke({
                "repo_full_name": event.repository,
                "head_branch": branch_name,
                "base_branch": event.branch,
                "title": pr_title,
                "body": pr_body,
            })

            if not pr_url.startswith("Error:"):
                pr_number = int(pr_url.split("/")[-1]) if pr_url else 0
                self.notification.send_pr_created(state, pr_url)
                return {"pr_url": pr_url, "pr_number": pr_number, "success": True}
            else:
                return {"success": False, "error_message": pr_url}

        except Exception as e:
            logger.error(f"创建PR失败: {e}", exc_info=True)
            return {"success": False, "error_message": f"创建PR失败: {str(e)}"}

    # ==================== 节点: handle_failure ====================

    async def _handle_failure(self, state: RepairState) -> dict[str, Any]:
        error_msg = state.get("error_message", "未知错误")
        logger.error(f"修复流程失败: {error_msg}")

        self.notification.send_failure(state)
        return {"success": False, "error_message": error_msg}

    # ==================== 路由 ====================

    def _route_after_context(self, state: RepairState) -> str:
        """上下文收集后路由：环境错误直接结束，失败去 handle_failure，否则进入修复"""
        if state.get("success") is False and state.get("error_message"):
            return "handle_failure"
        if state.get("is_env_error"):
            logger.info("环境/依赖错误，跳过代码修复，流程结束")
            return END
        return "fix_agent"

    def _route_after_review(self, state: RepairState) -> str:
        if state.get("success") is False and state.get("error_message"):
            return "handle_failure"

        risk_level = state.get("risk_level", "NONE")
        retry_count = state.get("retry_count", 0)

        if state.get("has_critical_risks", False) or risk_level == "CRITICAL":
            logger.error(f"致命风险，终止: {state.get('risk_warnings', [])}")
            return "handle_failure"

        if state.get("has_high_risks", False) or risk_level == "HIGH":
            if retry_count < state.get("max_retries", 3):
                logger.info(f"高危风险，重试 ({retry_count + 1}/{state['max_retries']})")
                return "fix_agent"
            return "create_pr"

        if not state.get("review_passed", False):
            if retry_count < state.get("max_retries", 3):
                logger.info(f"审查未通过，重试 ({retry_count + 1}/{state['max_retries']})")
                return "fix_agent"
            return "handle_failure"

        return "run_tests"

    def _route_after_test(self, state: RepairState) -> str:
        """测试后路由 — 优化逻辑：
        - success/uncertain → create_pr（不重试）
        - failure + import错误 → create_pr（导入类修复无法验证）
        - failure + 逻辑错误 + retries < max → fix_agent 重试
        - 重试耗尽 → handle_failure
        """
        if state.get("success") is False and state.get("error_message"):
            return "handle_failure"

        validation_status = state.get("validation_status", "")

        if validation_status == "success":
            logger.info("验证通过，创建 PR")
            return "create_pr"

        if validation_status == "uncertain":
            logger.warning("验证不确定，仍创建 PR")
            return "create_pr"

        if validation_status == "failure":
            # 导入类错误修复后无法运行原代码验证，直接创建 PR
            if _all_import_errors(state.get("error_locations", [])):
                logger.info("导入类错误，验证失败是预期的，创建 PR")
                return "create_pr"

            if state.get("retry_count", 0) < state.get("max_retries", 3):
                logger.info(f"验证失败，重试 ({state.get('retry_count', 0) + 1}/{state['max_retries']})")
                return "fix_agent"

            logger.warning(f"重试 {state['max_retries']} 次后验证仍未通过，创建 PR")
            return "create_pr"

        # 向后兼容：validation_status 为空
        logger.warning("validation_status 为空，回退到旧逻辑")
        if not state.get("test_passed", False):
            if state.get("retry_count", 0) < state.get("max_retries", 3):
                return "fix_agent"
            return "create_pr"
        return "create_pr"

    def _route_after_create_pr(self, state: RepairState) -> str:
        if state.get("success") is False:
            return "handle_failure"
        return END

    # ==================== 入口 ====================

    async def run(self, event: GitHubEvent, ci_logs: str = "") -> dict[str, Any]:
        """运行修复流程"""
        logger.info(f"启动修复流程: {event.event_id}")

        try:
            initial_state = {
                "event": event,
                "ci_logs": ci_logs,
                "repo_path": "",
                "error_locations": [],
                "fix_description": "",
                "modified_files": [],
                "code_changes": {},
                "original_codes": {},
                "diff_content": "",
                "review_passed": False,
                "review_comments": "",
                "risk_warnings": [],
                "risk_level": "NONE",
                "rejection_reason": "",
                "validation_status": "",
                "validation_method": "",
                "validation_command": "",
                "test_output": "",
                "failed_tests": [],
                "fix_history": [],
                "mandatory_instructions": "",
                "pr_url": "",
                "pr_number": 0,
                "success": False,
                "error_message": "",
                "retry_count": 0,
                "max_retries": self.max_retries,
                "current_phase": "",
            }

            final_state = await self.graph.ainvoke(initial_state)

            logger.info(f"修复流程完成: 成功={final_state['success']}")
            if final_state.get("pr_url"):
                logger.info(f"PR地址: {final_state['pr_url']}")

            return final_state

        except Exception as e:
            logger.error(f"修复流程异常: {e}", exc_info=True)
            return {"success": False, "error_message": f"修复流程异常: {str(e)}"}

    async def run_repair(self, event: GitHubEvent, ci_logs: str = "") -> dict[str, Any]:
        """向后兼容别名"""
        return await self.run(event, ci_logs=ci_logs)
