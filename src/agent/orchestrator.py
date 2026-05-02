"""修复流程编排器 — 图构建 + 路由 + 节点实现"""

import asyncio
import ast
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
from src.notify.lark_base import init_lark_base, report_repair_record, ensure_table_ready
from src.store.repair_store import get_repair_store, compute_traceback_fingerprint, RepairLifecycleStatus
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
from src.bus.schemas import GitHubEvent, RuntimeLogEvent
from src.utils.audit import audit_logger
from src.utils.path_mapping import apply_path_mapping
from src.utils.version_manager import ensure_repo_with_version
from src.config.service_registry import get_service_registry

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

    # 事件源 → 多维表表名映射（扩展时加在这里）
    SOURCE_TABLE_MAP = {
        "github_webhook": "github-PR修复",
    }

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
        lark_base_enabled: bool = False,
        lark_base_token: str = "",
        lark_base_repair_table_id: str = "",
        lark_as_bot: bool = False,
        lark_auto_create_table: bool = True,
        lark_auto_fix_fields: bool = True,
        lark_alert_on_failure: bool = True,
        lark_alert_threshold: int = 3,
        environment: str = "development",
    ):
        self.github_token = github_token
        self.max_retries = max_retries
        self.max_change_lines = max_change_lines
        self.environment = environment

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
            base_enabled=lark_base_enabled,
            base_token=lark_base_token,
        )

        # 初始化飞书多维表格客户端
        if lark_base_enabled and lark_base_token:
            init_lark_base(
                base_token=lark_base_token,
                repair_table_id=lark_base_repair_table_id,
                as_bot=lark_as_bot,
                auto_create_table=lark_auto_create_table,
                auto_fix_fields=lark_auto_fix_fields,
                alert_on_failure=lark_alert_on_failure,
                alert_threshold=lark_alert_threshold,
                notify_users=lark_notify_users,
                notify_groups=[]  # 告警默认只发送给用户，不发送到群组
            )
            logger.info("飞书多维表格上报功能已启用")

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

        # 双入口：根据事件类型路由到不同的上下文收集节点
        workflow.add_node("collect_runtime_context", self._collect_runtime_context)
        workflow.add_conditional_edges(
            START,
            self._route_by_event,
            ["collect_context", "collect_runtime_context"],
        )
        workflow.add_conditional_edges(
            "collect_runtime_context",
            self._route_after_context,
            ["fix_agent", "handle_failure", END],
        )
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
            ["fix_agent", "run_tests", "create_pr", "handle_failure"],
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

        # Gate 拒绝使用模板指令（有明确的结构化违规类型）
        if rejection_source == "gate":
            reason = violation_type or rejection_data.get("violation_type", "boundary_violation")
            instruction_kwargs = {}
            ctx = rejection_data.get("error_context", {})
            instruction_kwargs.update(ctx)
            instruction_kwargs["details"] = rejection_data.get("details", "")
            if violation_type == "file_incomplete":
                instruction_kwargs["missing_files"] = ", ".join(ctx.get("missing_files", []))
                instruction_kwargs["all_target_files"] = ", ".join(ctx.get("all_target_files", []))
            if violation_type == "syntax_line_violation":
                instruction_kwargs["target_files"] = ", ".join(state.get("target_files", []))
            instruction = generate_instruction(reason, **instruction_kwargs)
            reason_for_history = reason

        # Review/Test 拒绝直接传递原始反馈
        elif rejection_source == "review":
            review_comments = (
                rejection_data.get("review_comments")
                or state.get("review_comments")
                or "审查未通过，但无详细反馈"
            )
            instruction = f"🚨 审查Agent拒绝了你的修复，以下是审查反馈原文：\n\n{review_comments}\n\n请根据上述反馈修正你的修复，不要重复同样的错误。"
            reason_for_history = rejection_data.get("rejection_reason", "review_rejected")

        elif rejection_source == "test":
            test_output = rejection_data.get("test_output", state.get("test_output", ""))
            failed_tests = rejection_data.get("failed_tests", state.get("failed_tests") or [])
            failed_str = "\n".join(f"- {t}" for t in failed_tests) if failed_tests else "（无具体失败用例）"
            instruction = f"🚨 测试Agent拒绝了你的修复，以下是测试失败详情：\n\n失败用例：\n{failed_str}\n\n测试输出：\n{test_output}\n\n请根据上述失败信息修正你的修复。"
            reason_for_history = "test_failure"

        else:
            instruction = "请按照最小修改原则修复原始错误，不要修改无关代码。"
            reason_for_history = "unknown"

        # 累计历史强制指令，但超过 500 字符时只保留最新 2 条防止 prompt 膨胀
        prev = state.get("mandatory_instructions", "")
        if prev:
            candidate = prev + "\n\n---\n\n" + instruction
            if len(candidate) > 500:
                # 只保留最后 2 条指令：从倒数第二个 "---" 分隔处截取
                parts = candidate.split("\n\n---\n\n")
                if len(parts) > 2:
                    instruction = "\n\n---\n\n".join(parts[-2:])
                else:
                    instruction = candidate
                logger.info(
                    f"mandatory_instructions 超 500 字符（{len(candidate)}），"
                    f"裁剪为最后 2 条（{len(instruction)} 字符）"
                )
            else:
                instruction = candidate

        attempt = FixAttempt(
            attempt=state.retry_count,
            diff_summary=(state.diff_content or "")[:200],
            rejection_reason=reason_for_history,
            rejected_by=rejection_source,
        )

        return {
            "fix_history": state.fix_history + [attempt],
            "mandatory_instructions": instruction,
        }

    # ==================== 节点: collect_context ====================

    async def _collect_context(self, state: RepairState) -> dict[str, Any]:
        audit_logger.log_event("node_enter", node="collect_context")
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

            # === 状态机查重: 基于 traceback 指纹 ===
            fingerprint = ""
            repair_record_id = 0
            try:
                store = get_repair_store()
                fingerprint = compute_traceback_fingerprint(ci_logs or "")
                if fingerprint:
                    existing = store.query_by_fingerprint(fingerprint)
                    if existing:
                        status = existing.get("status", "")
                        record_id = existing.get("id", 0)
                        if status == RepairLifecycleStatus.PENDING_DEPLOY:
                            logger.info(f"指纹 {fingerprint} 已有 pending_deploy 记录，跳过")
                            return {"success": False, "error_message": f"相同错误已有修复PR等待部署 (指纹: {fingerprint})"}
                        elif status == RepairLifecycleStatus.DEPLOYED:
                            logger.info(f"指纹 {fingerprint} 已部署，忽略")
                            return {"success": False, "error_message": f"相同错误已修复并部署 (指纹: {fingerprint})"}
                        elif status == RepairLifecycleStatus.ABANDONED:
                            logger.info(f"指纹 {fingerprint} 已放弃，忽略")
                            return {"success": False, "error_message": f"相同错误已放弃修复 (指纹: {fingerprint})"}
                        elif status == RepairLifecycleStatus.FAILED:
                            if not store.should_retry(fingerprint):
                                logger.info(f"指纹 {fingerprint} 失败退避中，暂不重试")
                                return {"success": False, "error_message": f"相同错误修复失败，退避中 (指纹: {fingerprint})"}
                            logger.info(f"指纹 {fingerprint} 退避结束，允许重试")
                            repair_record_id = record_id
                        elif status == RepairLifecycleStatus.SUPERSEDED:
                            logger.info(f"指纹 {fingerprint} 已过期，继续修复")
                            repair_record_id = record_id
            except Exception as e:
                logger.warning(f"状态机查重失败（降级为直接修复）: {e}")

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

            # 提取所有错误文件的确定性文件列表（在整个流程中不变）
            target_files_errors = classification.get("error_locations", error_locations)
            target_files = sorted(set(
                err.file_path for err in target_files_errors
                if hasattr(err, "file_path") and err.file_path and err.file_path != "<string>"
            ))
            logger.info(f"确定性目标文件列表: {target_files}")

            # 提前读取所有目标文件的原始内容，确保 prompt 中始终展示全量文件
            original_codes = {}
            for fp in target_files:
                try:
                    content = read_file.invoke({"file_path": f"{repo_path}/{fp}"})
                    if not content.startswith("Error:"):
                        original_codes[fp] = content
                except Exception as e:
                    logger.warning(f"读取目标文件 {fp} 失败: {e}")

            return {
                "ci_logs": classification.get("ci_logs", ci_logs),
                "repo_path": repo_path,
                "error_locations": target_files_errors,
                "target_files": target_files,
                "original_codes": original_codes,
                "retry_count": 0,
                "max_retries": self.max_retries,
                "review_comments": "",
                "test_output": "",
                "risk_warnings": [],
                "failed_tests": [],
                "risk_level": "NONE",
                "traceback_fingerprint": fingerprint,
                "repair_record_id": repair_record_id,
            }

        except Exception as e:
            logger.error(f"收集上下文失败: {e}", exc_info=True)
            return {"success": False, "error_message": f"收集上下文失败: {str(e)}"}
        finally:
            audit_logger.log_event("node_exit", node="collect_context")

    async def _collect_runtime_context(self, state: RepairState) -> dict[str, Any]:
        """收集运行时日志上下文 — 独立于 CI 事件的 collect_context"""
        audit_logger.log_event("node_enter", node="collect_runtime_context")
        event: RuntimeLogEvent = state["event"]
        logger.info(f"收集运行时上下文: service={event.service}")

        try:
            # 事件去重
            event_key = f"runtime:{event.service}:{event.event_id}"
            async with self.lock:
                if event_key in self.processed_events:
                    logger.info(f"事件 {event_key} 已处理过，跳过")
                    return {"success": False, "error_message": "事件已处理过"}
                self.processed_events.add(event_key)

            # 1. 检查服务注册 — 未注册则通知开发者，不修
            registry = get_service_registry()
            svc_config = registry.get(event.service)
            if not svc_config:
                logger.warning(f"服务 {event.service} 未在 services.yaml 注册")
                self.notification.send_config_needed(
                    service_name=event.service,
                    error_summary=event.log[:300],
                    reason="未在 services.yaml 中注册",
                )
                return {"success": False, "error_message": f"服务 {event.service} 未注册，请先配置 services.yaml"}

            # 2. 检查版本配置 — 未配置则通知开发者，不修
            if not svc_config.version:
                logger.warning(f"服务 {event.service} 未配置跟踪版本")
                self.notification.send_config_needed(
                    service_name=event.service,
                    error_summary=event.log[:300],
                    reason="未配置跟踪版本（version 字段为空）",
                )
                return {"success": False, "error_message": f"服务 {event.service} 未配置版本，请在 services.yaml 中设置 version"}

            set_tool_context({"github_token": self.github_token})

            # 3. 解析 Traceback
            error_locations_raw = []
            if event.log:
                error_locations_raw = parse_python_errors.invoke({"log_content": event.log})
                logger.info(f"解析到错误数量: {len(error_locations_raw)}")

            if not error_locations_raw:
                async with self.lock:
                    self.processed_events.discard(event_key)
                return {"success": False, "error_message": "日志中未检测到Python错误"}

            # === 状态机查重: 基于 traceback 指纹 ===
            fingerprint = ""
            repair_record_id = 0
            try:
                store = get_repair_store()

                # 仓库目录不存在 → 清除该服务的旧记录，避免残留指纹阻塞修复
                repo_dir = svc_config.repo_local_path
                if not os.path.isdir(os.path.join(repo_dir, ".git")):
                    store.delete_by_service(event.service)

                fingerprint = compute_traceback_fingerprint(event.log or "")
                if fingerprint:
                    existing = store.query_by_fingerprint(fingerprint)
                    if existing:
                        status = existing.get("status", "")
                        record_id = existing.get("id", 0)
                        if status == RepairLifecycleStatus.PENDING_DEPLOY:
                            logger.info(f"指纹 {fingerprint} 已有 pending_deploy 记录，跳过")
                            return {
                                "success": False,
                                "error_message": f"相同错误已有修复PR等待部署 (指纹: {fingerprint})",
                                "duplicate_info": {
                                    "fingerprint": fingerprint,
                                    "pr_url": existing.get("fix_pr_url", ""),
                                },
                            }
                        elif status == RepairLifecycleStatus.DEPLOYED:
                            logger.info(f"指纹 {fingerprint} 已部署，忽略")
                            return {"success": False, "error_message": f"相同错误已修复并部署 (指纹: {fingerprint})"}
                        elif status == RepairLifecycleStatus.ABANDONED:
                            logger.info(f"指纹 {fingerprint} 已放弃，忽略")
                            return {"success": False, "error_message": f"相同错误已放弃修复 (指纹: {fingerprint})"}
                        elif status == RepairLifecycleStatus.FAILED:
                            if not store.should_retry(fingerprint):
                                logger.info(f"指纹 {fingerprint} 失败退避中，暂不重试")
                                return {"success": False, "error_message": f"相同错误修复失败，退避中 (指纹: {fingerprint})"}
                            logger.info(f"指纹 {fingerprint} 退避结束，允许重试")
                            repair_record_id = record_id
                        elif status == RepairLifecycleStatus.SUPERSEDED:
                            logger.info(f"指纹 {fingerprint} 已过期，继续修复")
                            repair_record_id = record_id
            except Exception as e:
                logger.warning(f"状态机查重失败（降级为直接修复）: {e}")

            # 4. 路径映射
            for err in error_locations_raw:
                fp = err.get("file_path", "")
                if fp:
                    err["file_path"] = apply_path_mapping(fp, svc_config.path_mapping)

            # 5. 确保本地仓库可用（使用配置的版本，不猜测）
            repo_path, degraded = await ensure_repo_with_version(
                repo_url=svc_config.repo_url,
                local_path=svc_config.repo_local_path,
                version=svc_config.version,
                branch=svc_config.git_branch,
            )
            repo = Repo(repo_path)
            set_tool_context({"github_token": self.github_token, "repo_path": repo_path, "repo": repo})

            if degraded:
                logger.warning(f"版本降级：使用最新 {svc_config.git_branch} 分支代码")

            # 6. 过滤有效错误
            error_locations = self._filter_valid_errors(error_locations_raw, repo_path)
            if not error_locations:
                async with self.lock:
                    self.processed_events.discard(event_key)
                return {"success": False, "error_message": "过滤后没有有效错误"}

            # 7. 提取目标文件 + 读取源码
            target_files = sorted(set(
                err.file_path for err in error_locations
                if hasattr(err, "file_path") and err.file_path and err.file_path != "<string>"
            ))
            logger.info(f"目标文件列表: {target_files}")

            original_codes = {}
            for fp in target_files:
                try:
                    content = read_file.invoke({"file_path": f"{repo_path}/{fp}"})
                    if not content.startswith("Error:"):
                        original_codes[fp] = content
                except Exception as e:
                    logger.warning(f"读取文件 {fp} 失败: {e}")

            return {
                "ci_logs": event.log,
                "repo_path": repo_path,
                "error_locations": error_locations,
                "target_files": target_files,
                "original_codes": original_codes,
                "retry_count": 0,
                "max_retries": self.max_retries,
                "review_comments": "",
                "test_output": "",
                "risk_warnings": [],
                "failed_tests": [],
                "risk_level": "NONE",
                "degraded_version": degraded,
                "fix_source": "runtime_log",
                "service_version": svc_config.version,
                "traceback_fingerprint": fingerprint,
                "repair_record_id": repair_record_id,
            }

        except Exception as e:
            logger.error(f"收集运行时上下文失败: {e}", exc_info=True)
            return {"success": False, "error_message": f"收集运行时上下文失败: {str(e)}"}
        finally:
            audit_logger.log_event("node_exit", node="collect_runtime_context")

    @staticmethod
    def _make_event_key(event: GitHubEvent) -> str:
        # 提取 commit SHA：pull_request 在 payload.pull_request.head.sha，workflow_run 在 payload.head_sha
        head_sha = ""
        if isinstance(event.payload, dict):
            head_sha = event.payload.get("head_sha", "")
            if not head_sha:
                head_sha = event.payload.get("pull_request", {}).get("head", {}).get("sha", "")

        if event.pr_number and event.branch:
            base = f"{event.repository}:pr:{event.pr_number}:branch:{event.branch}"
            return f"{base}:sha:{head_sha}" if head_sha else base
        if event.pr_number:
            base = f"{event.repository}:pr:{event.pr_number}"
            return f"{base}:sha:{head_sha}" if head_sha else base
        if head_sha:
            return f"{event.repository}:sha:{head_sha}"
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

            # 对无 file_path 的错误，从 traceback 强制提取文件路径
            if not fp and err.get("traceback"):
                # 跳过 CI 脚本中的变量赋值（如 HAS_ERROR=0）
                err_msg = err.get("error_message", "")
                if any(kw in err_msg for kw in ["HAS_ERROR=", "HAS_WARNING=", "EXIT_CODE="]):
                    logger.info(f"丢弃错误: CI变量赋值, msg={err_msg[:80]}")
                    continue
                tb_match = re.search(r'File "([^"]+\.py)"', err["traceback"])
                if tb_match:
                    fp = tb_match.group(1)
                    base_fields["file_path"] = fp
                    ln_match = re.search(r'line\s+(\d+)', err["traceback"])
                    if ln_match:
                        base_fields["line_number"] = int(ln_match.group(1))
                    logger.info(f"从 traceback 提取文件路径: {fp}")
                else:
                    logger.info(
                        f"丢弃错误: traceback 中无文件路径, "
                        f"type={err.get('error_type')}, msg={err_msg[:80]}"
                    )

            if not fp or fp == "<string>":
                if err.get("error_type") and err.get("error_message"):
                    valid.append(ErrorLocation(file_path="", **base_fields))
                else:
                    logger.info(
                        f"丢弃错误: 无有效 error_type/error_message, "
                        f"fp={fp!r}, type={err.get('error_type')}"
                    )
                continue

            # CI 环境绝对路径 → 剥离前缀后按相对路径处理
            ci_stripped = _strip_ci_prefix(fp)
            if ci_stripped != fp:
                cleaned = ci_stripped.lstrip("/\\").replace("\\", "/")
                full = os.path.abspath(os.path.join(repo_path, cleaned))
                if full.startswith(repo_path_abs) and os.path.isfile(full):
                    rel = os.path.relpath(full, repo_path_abs).replace("\\", "/")
                    valid.append(ErrorLocation(file_path=rel, **base_fields))
                else:
                    logger.info(f"丢弃错误: CI路径文件不存在 {fp} -> {full}")
                continue

            # 本地绝对路径 → 必须在仓库目录内才是项目文件
            if os.path.isabs(fp):
                abs_fp = os.path.abspath(fp)
                if abs_fp.startswith(repo_path_abs) and os.path.isfile(abs_fp):
                    rel = os.path.relpath(abs_fp, repo_path_abs).replace("\\", "/")
                    valid.append(ErrorLocation(file_path=rel, **base_fields))
                else:
                    logger.info(f"丢弃错误: 绝对路径不在仓库内或文件不存在 {fp}")
                continue

            # 修复 lstrip 吞掉点号目录的问题（如 ./.github/tt.py → .github/tt.py 而非 github/tt.py）
            cleaned = fp.lstrip("/\\").replace("\\", "/")
            if cleaned.startswith('./') or cleaned.startswith('.\\'):
                cleaned = cleaned[2:]
            full = os.path.abspath(os.path.join(repo_path, cleaned))
            if full.startswith(repo_path_abs) and os.path.isfile(full):
                rel = os.path.relpath(full, repo_path_abs).replace("\\", "/")
                valid.append(ErrorLocation(file_path=rel, **base_fields))
            else:
                logger.info(f"丢弃错误: 相对路径文件不存在 {fp} -> {full}")

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

        # 对语法错误的文件做全量检测：迭代注释出错行，收集所有 SyntaxError
        if syntax_error_files:
            new_syntax_errors = self._exhaustive_syntax_scan(
                repo_path, syntax_error_files
            )
            if new_syntax_errors:
                error_locations.extend(new_syntax_errors)
                for se in new_syntax_errors:
                    if se.file_path and se.file_path not in syntax_error_files:
                        syntax_error_files.append(se.file_path)
                logger.info(
                    f"全量语法扫描新增 {len(new_syntax_errors)} 个错误, "
                    f"总计 {len(syntax_error_files)} 个文件"
                )

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

        # FileNotFoundError: 从 traceback 补充缺失的文件路径
        for err in error_locations:
            if err.error_type.lower() == "filenotfounderror":
                if not err.file_path and err.traceback:
                    file_match = re.search(r'File "([^"]+\.py)"', err.traceback)
                    if file_match:
                        err.file_path = file_match.group(1)
                        line_match = re.search(r'line\s+(\d+)', err.traceback)
                        if line_match:
                            err.line_number = int(line_match.group(1))
                        logger.info(
                            f"从 traceback 补充 FileNotFoundError 文件路径: "
                            f"{err.file_path}:{err.line_number}"
                        )

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
    def _exhaustive_syntax_scan(
        repo_path: str, syntax_error_files: list[str]
    ) -> list[ErrorLocation]:
        """对语法错误文件做全量检测：迭代注释出错行，收集所有 SyntaxError"""
        import ast as _ast

        new_errors = []
        for fp in syntax_error_files:
            full_path = f"{repo_path}/{fp}"
            try:
                content = read_file.invoke({"file_path": full_path})
            except Exception:
                continue
            if not content or content.startswith("Error:"):
                continue

            lines = content.splitlines()
            found_errors = set()

            while True:
                try:
                    _ast.parse("\n".join(lines))
                    break
                except SyntaxError as e:
                    err_lineno = e.lineno
                    if err_lineno is None or err_lineno <= 0:
                        break
                    # 跳过已发现的错误
                    err_key = (fp, err_lineno)
                    if err_key in found_errors:
                        break
                    found_errors.add(err_key)

                    new_errors.append(ErrorLocation(
                        file_path=fp,
                        line_number=err_lineno,
                        error_type="SyntaxError",
                        error_message=str(e),
                        source="syntax_error",
                    ))
                    logger.info(f"全量语法扫描发现: {fp}:L{err_lineno} — {e}")

                    # 注释掉该行，继续解析后续错误
                    if err_lineno - 1 < len(lines):
                        lines[err_lineno - 1] = f"# <<<SYNTAX_ERROR_LINE_{err_lineno}>>>"

        return new_errors

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
        audit_logger.log_event("node_enter", node="fix_agent")
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

            # 调试：确认 original_codes 包含哪些文件
            oc = state.get("original_codes", {})
            logger.info(f"传递给FixAgent的original_codes文件列表: {list(oc.keys())}")
            for fp, content in oc.items():
                logger.info(f"  {fp}: {len(content)} 字符")

            fix_result = await fix_agent.generate_fix(
                ci_logs=state["ci_logs"],
                error_locations=state["error_locations"],
                target_files=state.get("target_files", []),  # 确定性文件列表
                original_codes=state.get("original_codes", {}),
                mandatory_instructions=state.get("mandatory_instructions", ""),
                fix_history=state.get("fix_history", []),
                retry_count=state.get("retry_count", 0),
                **extra_context,
            )

            # 累加token用量
            token_usage = fix_result.get("token_usage", 0)
            total_token = state.get("total_token_usage", 0) + token_usage
            if token_usage > 0:
                logger.debug(f"修复Agent调用消耗token: {token_usage}，累计: {total_token}")

            # 环境错误 → 直接结束
            if fix_result.get("is_env_error", False):
                return Command(
                    update={"success": True, "error_message": fix_result["fix_description"], "total_token_usage": total_token},
                    goto=END,
                )

            if not fix_result.get("code_changes"):
                return Command(
                    update={"success": False, "error_message": "修复Agent未能生成有效修复代码", "total_token_usage": total_token},
                    goto="handle_failure",
                )

            # 读取原始代码（从 state 已有的全量 original_codes 开始，补充新文件）
            original_codes = dict(state.get("original_codes", {}))
            for fp in fix_result["code_changes"]:
                if fp not in original_codes:
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
                    "total_token_usage": total_token,
                },
                goto="validation_gate",
            )

        except Exception as e:
            logger.error(f"修复Agent执行失败: {e}", exc_info=True)
            return Command(
                update={"success": False, "error_message": f"修复Agent执行失败: {str(e)}", "total_token_usage": state.get("total_token_usage", 0)},
                goto="handle_failure",
            )
        finally:
            audit_logger.log_event("node_exit", node="fix_agent")

    # ==================== 节点: validation_gate ====================

    async def _validation_gate(self, state: RepairState) -> Command:
        audit_logger.log_event("node_enter", node="validation_gate")
        logger.info("运行校验门禁")

        # ★ 自动填充遗漏文件：LLM 常因训练先验省略"不需要改"的文件，
        #    在编排层用原始内容补全，避免 file_incomplete 死循环
        target_files = state.get("target_files", [])
        original_codes = state.get("original_codes", {})
        code_changes = state.get("code_changes", {})

        if target_files and code_changes:
            modified_files = state.get("modified_files", [])
            for fp in target_files:
                if fp not in code_changes and fp in original_codes:
                    orig = original_codes[fp]
                    # 原始内容有语法错误时不自动填充，标记为需要 LLM 修复
                    try:
                        ast.parse(orig)
                    except SyntaxError:
                        logger.warning(
                            f"文件 {fp} 原始内容有语法错误，跳过自动填充（需要修复）"
                        )
                        continue
                    code_changes[fp] = orig
                    if fp not in modified_files:
                        modified_files.append(fp)
                    logger.info(f"自动填充遗漏文件: {fp}（保留原始内容）")

        validation = validate_fix(
            fix_result={
                "code_changes": state["code_changes"],
                "fix_description": state["fix_description"],
                "modified_files": state["modified_files"],
                "is_env_error": state.get("is_env_error", False),
            },
            original_codes=state["original_codes"],
            error_locations=state["error_locations"],
            max_change_lines=state.get("max_change_lines", self.max_change_lines),
            target_files=state.get("target_files", []),
        )

        if validation.passed:
            # 通过：写入文件，进入审查
            for fp, content in state["code_changes"].items():
                write_file.invoke({"file_path": fp, "content": content})

            logger.info("校验通过，进入审查")
            audit_logger.log_event("node_exit", node="validation_gate")
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
            audit_logger.log_event("node_exit", node="validation_gate")
            return Command(
                update={
                    **retry_context,
                    "retry_count": state["retry_count"],
                    "current_phase": "retry_from_gate",
                },
                goto="fix_agent",
            )

        audit_logger.log_event("node_exit", node="validation_gate")
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
        audit_logger.log_event("node_enter", node="review_changes")
        logger.info("运行审查Agent")

        try:
            review_agent = self.agent_factory.create_review_agent(
                repo_path=state["repo_path"],
            )
            review_result = await review_agent.review_changes(
                error_locations=state["error_locations"],
                fix_description=state["fix_description"],
                modified_files=state["modified_files"],
                code_changes=state["code_changes"],
                diff_content=state["diff_content"],
                repo_path=state["repo_path"],
                original_codes=state.get("original_codes"),
            )

            # 累加审查Agent的token用量
            review_token = review_result.get("token_usage", 0)
            total_token = state.get("total_token_usage", 0) + review_token
            if review_token > 0:
                logger.debug(f"审查Agent消耗token: {review_token}，累计: {total_token}")

            # 如果 Phase 2 安全修复生成了新的 code_changes，写入文件
            if review_result.get("security_fixes_applied") and review_result.get("code_changes"):
                logger.info("Phase 2 安全修复产生变更，写入文件")
                for fp, content in review_result["code_changes"].items():
                    write_file.invoke({"file_path": fp, "content": content})
                state["code_changes"] = review_result["code_changes"]

            # 审查未通过时构建重试上下文
            if not review_result.get("review_passed", False):
                retry_context = self._build_retry_context(
                    state, "review",
                    rejection_data={
                        "rejection_reason": review_result.get("rejection_reason", "original_error_unresolved"),
                        "review_comments": review_result.get("review_comments", ""),
                    },
                )
                review_result.update(retry_context)

            review_result["total_token_usage"] = total_token
            return review_result

        except Exception as e:
            logger.error(f"审查Agent执行失败: {e}", exc_info=True)
            return {
                "review_passed": False,
                "review_comments": f"审查过程出错: {str(e)}",
                "risk_warnings": [str(e)],
                "risk_level": "NONE",
                "rejection_reason": "",
                "total_token_usage": state.get("total_token_usage", 0),
            }
        finally:
            audit_logger.log_event("node_exit", node="review_changes")

    # ==================== 节点: run_tests ====================

    async def _run_tests(self, state: RepairState) -> dict[str, Any]:
        audit_logger.log_event("node_enter", node="run_tests")
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
        finally:
            audit_logger.log_event("node_exit", node="run_tests")

    # ==================== 节点: create_pr ====================

    async def _create_pull_request(self, state: RepairState) -> dict[str, Any]:
        audit_logger.log_event("node_enter", node="create_pr")
        event: GitHubEvent = state["event"]
        logger.info(f"创建PR: {event.repository}")

        try:
            repo = Repo(state["repo_path"])
            set_tool_context({"repo": repo, "repo_path": state["repo_path"], "github_token": self.github_token})

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
            if commit_result.startswith("Warning:"):
                logger.warning(f"按文件列表提交失败，改用 git add -A: {commit_result}")
                commit_result = commit_changes.invoke(
                    {"message": commit_message, "files": None}
                )
                if commit_result.startswith("Error:"):
                    return {"success": False, "error_message": f"提交变更失败(A): {commit_result}"}

            # 提交后获取真实 diff（修复前 diff_content 在文件写入前捕获，始终为空）
            real_diff = get_diff.invoke({"base_branch": event.branch})

            set_tool_context({"repo_path": state["repo_path"], "github_token": self.github_token})

            if push_branch.invoke({"branch_name": branch_name}) != "Success":
                return {"success": False, "error_message": "推送分支失败"}

            pr_body = self.notification.build_pr_body(state, branch_name, diff_content=real_diff)

            # 远程日志降级风险标注
            if state.get("degraded_version"):
                svc_ver = state.get("service_version", "")
                ver_info = f"（配置版本: `{svc_ver}`）" if svc_ver else ""
                pr_body += (
                    f"\n\n> ⚠️ **版本降级提示**{ver_info}：无法精确 checkout 到配置的版本，"
                    "本次修复基于最新代码。修复可能不完全精确，请仔细审查。"
                )

            # 提取首个 error_type 使 PR 标题有区分度
            error_locs = state.error_locations
            if error_locs:
                first = error_locs[0]
                primary_error = first.error_type if hasattr(first, 'error_type') else first.get('error_type', 'Unknown')
            else:
                primary_error = 'Unknown'
            # 截断 fix_description 到 40 字
            raw_desc = state.fix_description or ''
            short_desc = raw_desc[:40]
            if len(raw_desc) > 40:
                short_desc = short_desc[:37] + '...'

            # PR 标题：区分来源和环境（runtime_log=生产，CI=开发）
            if getattr(event, "event_type", "") == "runtime_log":
                pr_title = f"[SpiderClaw: fix][生产] {primary_error} @{event.service}: {short_desc}"
            else:
                pr_author_title = event.payload.get('sender', {}).get('login', '未知用户')
                pr_title = f"[SpiderClaw: fix][开发] {primary_error} @{pr_author_title}: {short_desc}"

            pr_url = create_pull_request.invoke({
                "repo_full_name": event.repository,
                "head_branch": branch_name,
                "base_branch": event.branch,
                "title": pr_title,
                "body": pr_body,
            })

            if not pr_url.startswith("Error:"):
                pr_number = int(pr_url.split("/")[-1]) if pr_url else 0
                # 根据事件源确定目标表名（先在通知前解析，供通知和上报使用）
                table_name = self.SOURCE_TABLE_MAP.get(event.source, f"{event.service}-修复表")

                # 提前缓存表ID，确保通知中的链接包含 ?table= 参数
                await ensure_table_ready(table_name)

                # 根据事件类型发送不同格式的通知
                if getattr(event, "event_type", "") == "runtime_log":
                    # Web 服务运行时错误 - 使用专用通知格式
                    error_type = "Unknown"
                    error_location = ""
                    if state.error_locations:
                        first = state.error_locations[0]
                        error_type = first.error_type if hasattr(first, 'error_type') else first.get('error_type', 'Unknown')
                        fp = first.file_path if hasattr(first, 'file_path') else first.get('file_path', '')
                        ln = first.line_number if hasattr(first, 'line_number') else first.get('line_number', 0)
                        error_location = f"{fp}:{ln}" if fp else ""

                    change_lines = 0
                    if real_diff:
                        adds = len([l for l in real_diff.split('\n') if l.startswith('+') and not l.startswith('+++')])
                        deletes = len([l for l in real_diff.split('\n') if l.startswith('-') and not l.startswith('---')])
                        change_lines = adds + deletes

                    self.notification.send_runtime_pr_created(
                        service=event.service,
                        error_type=error_type,
                        error_location=error_location,
                        fix_description=state.fix_description,
                        file_count=len(state.modified_files) or 0,
                        change_lines=change_lines,
                        pr_url=pr_url,
                        table_name=table_name,
                    )
                else:
                    # CI 测试事件 - 使用原有格式
                    self.notification.send_pr_created(state, pr_url, diff_content=real_diff, table_name=table_name, environment="开发")

                # 上报修复成功记录到多维表格
                try:
                    # 计算修复耗时
                    start_time = state.get("start_time", datetime.datetime.now())
                    repair_duration = (datetime.datetime.now() - start_time).total_seconds()

                    # 提取错误类型
                    error_type = "Unknown"
                    if state.error_locations:
                        first = state.error_locations[0]
                        error_type = first.error_type if hasattr(first, 'error_type') else first.get('error_type', 'Unknown')

                    # 计算变更行数
                    change_lines = 0
                    if real_diff:
                        adds = len([l for l in real_diff.split('\n') if l.startswith('+') and not l.startswith('+++')])
                        deletes = len([l for l in real_diff.split('\n') if l.startswith('-') and not l.startswith('---')])
                        change_lines = adds + deletes

                    # 修复文件数
                    file_count = len(state.modified_files) or 0

                    # PR作者
                    pr_author = event.payload.get('sender', {}).get('login', '未知用户')

                    # 原PR链接
                    original_pr_url = ""
                    if event.repository and hasattr(event, 'pr_number') and event.pr_number:
                        original_pr_url = f"https://github.com/{event.repository}/pull/{event.pr_number}"

                    # 环境映射：runtime_log 事件为生产环境，其他根据配置
                    if getattr(event, "event_type", "") == "runtime_log":
                        environment = "生产"
                    else:
                        env_map = {
                            "development": "开发",
                            "testing": "测试",
                            "production": "生产"
                        }
                        environment = env_map.get(self.environment, "开发")

                    await report_repair_record(
                        error_type=error_type,
                        repo_name=event.repository,
                        branch_name=event.branch,
                        pr_author=pr_author,
                        original_pr_url=original_pr_url,
                        fix_pr_url=pr_url,
                        repair_success=True,
                        fix_description=state.fix_description,
                        error_message="",
                        file_count=file_count,
                        change_lines=change_lines,
                        repair_duration=repair_duration,
                        retry_count=state.get("retry_count", 0),
                        token_usage=state.get("total_token_usage", 0),
                        related_files=state.modified_files,
                        environment=environment,
                        table_name=table_name,
                    )
                except Exception as e:
                    logger.error(f"上报修复记录失败: {e}", exc_info=True)

                # === 状态机: 写入 pending_deploy 记录 ===
                try:
                    fp = state.get("traceback_fingerprint", "")
                    if fp:
                        store = get_repair_store()
                        store.upsert(
                            fp,
                            RepairLifecycleStatus.PENDING_DEPLOY.value,
                            service=getattr(event, "service", "") or event.repository,
                            error_type=error_type,
                            fix_pr_url=pr_url,
                            fix_pr_number=str(pr_number),
                            fix_description=state.fix_description,
                            service_version=state.get("service_version", ""),
                            repo_name=event.repository,
                            branch_name=event.branch,
                        )
                except Exception as e:
                    logger.error(f"状态机 pending_deploy 写入失败: {e}")

                return {"pr_url": pr_url, "pr_number": pr_number, "success": True, "diff_content": real_diff}
            else:
                return {"success": False, "error_message": pr_url}

        except Exception as e:
            logger.error(f"创建PR失败: {e}", exc_info=True)
            return {"success": False, "error_message": f"创建PR失败: {str(e)}"}
        finally:
            audit_logger.log_event("node_exit", node="create_pr")

    # ==================== 节点: handle_failure ====================

    async def _handle_failure(self, state: RepairState) -> dict[str, Any]:
        audit_logger.log_event("node_enter", node="handle_failure")
        error_msg = state.get("error_message", "未知错误")
        logger.error(f"修复流程失败: {error_msg}")

        # 根据事件源确定目标表名（先在通知前解析，供通知和上报使用）
        event = state.event
        table_name = self.SOURCE_TABLE_MAP.get(event.source, f"{event.service}-修复表")

        # 提前缓存表ID，确保通知中的链接包含 ?table= 参数
        await ensure_table_ready(table_name)

        # 根据事件类型发送不同格式的通知
        if getattr(event, "event_type", "") == "runtime_log":
            # Web 服务运行时错误 - 使用专用通知格式
            error_type = "Unknown"
            error_location = ""
            if state.error_locations:
                first = state.error_locations[0]
                error_type = first.error_type if hasattr(first, 'error_type') else first.get('error_type', 'Unknown')
                fp = first.file_path if hasattr(first, 'file_path') else first.get('file_path', '')
                ln = first.line_number if hasattr(first, 'line_number') else first.get('line_number', 0)
                error_location = f"{fp}:{ln}" if fp else ""

            self.notification.send_runtime_failure(
                service=event.service,
                error_type=error_type,
                error_location=error_location,
                error_message=state.error_message,
                table_name=table_name,
                duplicate_info=state.get("duplicate_info"),
            )
        else:
            # CI 测试事件 - 使用原有格式
            self.notification.send_failure(state, table_name=table_name, environment="开发")

        # 上报修复失败记录到多维表格
        try:
            # 计算修复耗时
            start_time = state.get("start_time", datetime.datetime.now())
            repair_duration = (datetime.datetime.now() - start_time).total_seconds()

            # 提取错误类型
            error_type = "Unknown"
            if state.error_locations:
                first = state.error_locations[0]
                error_type = first.error_type if hasattr(first, 'error_type') else first.get('error_type', 'Unknown')

            # 计算变更行数
            change_lines = 0
            if state.diff_content:
                adds = len([l for l in state.diff_content.split('\n') if l.startswith('+') and not l.startswith('+++')])
                deletes = len([l for l in state.diff_content.split('\n') if l.startswith('-') and not l.startswith('---')])
                change_lines = adds + deletes

            # 修复文件数
            file_count = len(state.modified_files) or 0

            # PR作者
            pr_author = (
                event.payload.get('sender', {}).get('login', '未知用户')
                if isinstance(event, object) and hasattr(event, 'payload')
                else event.get('payload', {}).get('sender', {}).get('login', '未知用户')
                if isinstance(event, dict)
                else '未知用户'
            )

            # 仓库名称和分支
            repo_name = (
                event.repository if isinstance(event, object) and hasattr(event, 'repository')
                else event.get('repository', '') if isinstance(event, dict)
                else ''
            )
            branch_name = (
                event.branch if isinstance(event, object) and hasattr(event, 'branch')
                else event.get('branch', '') if isinstance(event, dict)
                else ''
            )

            # 原PR链接
            original_pr_url = ""
            if repo_name and hasattr(event, 'pr_number') and event.pr_number:
                original_pr_url = f"https://github.com/{repo_name}/pull/{event.pr_number}"

            # 环境映射：runtime_log 事件为生产环境，其他根据配置
            if getattr(event, "event_type", "") == "runtime_log":
                environment = "生产"
            else:
                env_map = {
                    "development": "开发",
                    "testing": "测试",
                    "production": "生产"
                }
                environment = env_map.get(self.environment, "开发")

            await report_repair_record(
                error_type=error_type,
                repo_name=repo_name,
                branch_name=branch_name,
                pr_author=pr_author,
                original_pr_url=original_pr_url,
                fix_pr_url=state.get("pr_url", ""),
                repair_success=False,
                fix_description=state.get("fix_description", ""),
                error_message=error_msg,
                file_count=file_count,
                change_lines=change_lines,
                repair_duration=repair_duration,
                retry_count=state.get("retry_count", 0),
                token_usage=state.get("total_token_usage", 0),
                related_files=state.modified_files,
                environment=environment,
                table_name=table_name,
            )
        except Exception as e:
            logger.error(f"上报失败记录失败: {e}", exc_info=True)

        # === 状态机: 记录修复失败 ===
        try:
            fp = state.get("traceback_fingerprint", "")
            if fp:
                store = get_repair_store()
                record = store.query_by_fingerprint(fp)
                new_fail_count = (record.get("fail_count", 0) + 1) if record else 1
                new_status = (
                    RepairLifecycleStatus.ABANDONED.value
                    if new_fail_count >= 3
                    else RepairLifecycleStatus.FAILED.value
                )
                store.upsert(
                    fp,
                    new_status,
                    service=getattr(event, "service", "") or event.repository,
                    error_type=error_type,
                    service_version=state.get("service_version", ""),
                    repo_name=repo_name,
                    branch_name=branch_name,
                    increment_fail=True,
                )
                if new_fail_count >= 3:
                    logger.warning(f"指纹 {fp} 已失败 {new_fail_count} 次，标记为 abandoned")
        except Exception as e:
            logger.error(f"状态机 failed 写入失败: {e}")

        finally:
            audit_logger.log_event("node_exit", node="handle_failure")

        return {"success": False, "error_message": error_msg}

    # ==================== 路由 ====================

    def _route_by_event(self, state: RepairState) -> str:
        """入口路由：根据事件类型分发到不同的上下文收集节点"""
        event = state.get("event")
        if getattr(event, "event_type", "") == "runtime_log":
            return "collect_runtime_context"
        return "collect_context"

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
        start_time = datetime.datetime.now()
        audit_logger.log_event("system_action", action="修复流程启动", event_id=event.event_id, model_name=self.agent_factory.config.llm_model)
        audit_logger.log_event("milestone", node="repair_start", event_id=event.event_id)
        logger.info(f"启动修复流程: {event.event_id}")

        try:
            # 根据事件类型决定 ci_logs 来源
            if getattr(event, "event_type", "") == "runtime_log":
                initial_ci_logs = event.log
            else:
                initial_ci_logs = ci_logs

            initial_state = {
                "event": event,
                "ci_logs": initial_ci_logs,
                "repo_path": "",
                "error_locations": [],
                "target_files": [],
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
                "start_time": start_time,
                "total_token_usage": 0,  # 总token消耗，后续可在agent调用时累加
                "pr_number": 0,
                "success": False,
                "error_message": "",
                "retry_count": 0,
                "max_retries": self.max_retries,
                "max_change_lines": self.max_change_lines,
                "current_phase": "",
            }

            final_state = await self.graph.ainvoke(initial_state)

            audit_logger.log_event(
                "system_action", action="修复流程完成",
                success=final_state["success"],
                pr_url=final_state.get("pr_url", ""),
            )
            audit_logger.log_event(
                "milestone", node="repair_complete",
                success=final_state["success"],
                pr_url=final_state.get("pr_url", ""),
            )
            logger.info(f"修复流程完成: 成功={final_state['success']}")
            if final_state.get("pr_url"):
                logger.info(f"PR地址: {final_state['pr_url']}")

            return final_state

        except Exception as e:
            logger.error(f"修复流程异常: {e}", exc_info=True)
            audit_logger.log_event(
                "milestone", node="repair_complete",
                success=False,
            )
            return {"success": False, "error_message": f"修复流程异常: {str(e)}"}

    async def run_repair(self, event: GitHubEvent, ci_logs: str = "") -> dict[str, Any]:
        """向后兼容别名"""
        return await self.run(event, ci_logs=ci_logs)
