"""修复流程编排器 - 使用LangChain标准工具"""
import os
import asyncio
import datetime
import logging
import re
from typing import Dict, Any
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command

from .state import RepairState
from src.agent.subagents.fix_agent import FixAgent
from src.agent.subagents.review_agent import ReviewAgent
from src.agent.subagents.test_agent import TestAgent
from src.agent.tools import (
    set_tool_context,
    clone_repository,
    download_ci_logs,
    parse_python_errors,
    get_diff,
    create_branch,
    commit_changes,
    push_branch,
    create_pull_request
)
from src.bus.schemas import GitHubEvent

logger = logging.getLogger(__name__)


class RepairOrchestrator:
    """修复流程编排器，使用LangChain标准工具"""

    def __init__(
        self,
        github_token: str,
        openai_api_key: str,
        openai_base_url: str = "https://api.openai.com/v1",
        llm_model: str = "gpt-4o",
        max_retries: int = 3,
        max_change_lines: int = 20
    ):
        """
        初始化编排器

        Args:
            github_token: GitHub访问令牌
            openai_api_key: OpenAI API密钥
            openai_base_url: OpenAI API基础URL
            llm_model: LLM模型名称
            max_retries: 最大修复重试次数
            max_change_lines: 最大允许变更行数
        """
        self.github_token = github_token
        self.openai_api_key = openai_api_key
        self.openai_base_url = openai_base_url
        self.llm_model = llm_model
        self.max_retries = max_retries
        self.max_change_lines = max_change_lines

        # 事件去重：已处理的PR或提交SHA
        self.processed_events = set()
        self.lock = asyncio.Lock()

        # 构建状态图
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """
        构建修复流程状态图

        Returns:
            StateGraph: 编译后的状态图
        """
        workflow = StateGraph(RepairState)

        # 添加节点
        workflow.add_node("collect_context", self._collect_context)
        workflow.add_node("fix_agent", self._run_fix_agent)
        workflow.add_node("review_changes", self._review_changes)
        workflow.add_node("run_tests", self._run_tests)
        workflow.add_node("create_pr", self._create_pull_request)
        workflow.add_node("handle_failure", self._handle_failure)

        # 定义边
        workflow.add_edge(START, "collect_context")
        workflow.add_edge("collect_context", "fix_agent")
        workflow.add_edge("fix_agent", "review_changes")

        # 审查后的条件路由
        workflow.add_conditional_edges(
            "review_changes",
            self._route_after_review,
            ["fix_agent", "run_tests", "handle_failure"]
        )

        # 测试后的条件路由
        workflow.add_conditional_edges(
            "run_tests",
            self._route_after_test,
            ["fix_agent", "create_pr", "handle_failure"]
        )

        workflow.add_edge("create_pr", END)
        workflow.add_edge("handle_failure", END)

        # 编译图
        return workflow.compile()

    async def _collect_context(self, state: RepairState) -> Dict[str, Any]:
        """
        收集上下文节点：下载CI日志、克隆仓库、解析错误

        Args:
            state: 当前状态

        Returns:
            Dict: 状态更新
        """
        event: GitHubEvent = state["event"]
        logger.info(f"收集上下文: {event.event_id}, 仓库: {event.repository}")

        try:
            # 事件去重：同一PR的同一分支只处理一次
            # 使用PR编号+分支作为key，确保同一PR的多个事件（check_run, workflow_run）只处理一次
            event_key = None
            if event.pr_number and event.branch:
                # 优先使用PR+分支作为去重key
                event_key = f"{event.repository}:pr:{event.pr_number}:branch:{event.branch}"
            elif event.pr_number:
                event_key = f"{event.repository}:pr:{event.pr_number}"
            elif event.branch and hasattr(event, 'head_sha') and event.head_sha:
                event_key = f"{event.repository}:branch:{event.branch}:sha:{event.head_sha}"
            elif isinstance(event.payload, dict) and 'head_sha' in event.payload:
                event_key = f"{event.repository}:sha:{event.payload['head_sha']}"
            elif isinstance(event.payload, dict) and 'check_run' in event.payload and 'head_sha' in event.payload['check_run'].get('check_suite', {}):
                # check_run事件的head_sha在check_suite里
                event_key = f"{event.repository}:sha:{event.payload['check_run']['check_suite']['head_sha']}"
            elif isinstance(event.payload, dict) and 'workflow_run' in event.payload:
                # workflow_run事件的head_sha在workflow_run里
                head_sha = event.payload['workflow_run'].get('head_sha', '')
                head_branch = event.payload['workflow_run'].get('head_branch', '')
                if head_sha and head_branch:
                    event_key = f"{event.repository}:branch:{head_branch}:sha:{head_sha}"
                else:
                    event_key = f"{event.repository}:sha:{head_sha or 'unknown'}"
            else:
                # fallback 用 event_id
                event_key = f"{event.repository}:event:{event.event_id}"

            # 原子操作：检查和标记在同一个锁块中，避免并行重复处理
            async with self.lock:
                if event_key in self.processed_events:
                    logger.info(f"事件 {event_key} 已处理过，跳过")
                    return {
                        "success": False,
                        "error_message": "事件已处理过，跳过重复执行"
                    }
                # 立即标记为处理中，避免其他并行请求重复处理
                self.processed_events.add(event_key)

            # 设置工具上下文
            set_tool_context({
                "github_token": self.github_token
            })

            # 1. 获取CI日志：如果状态中已经有了就直接使用（本地测试场景），否则下载
            ci_logs = state.get("ci_logs", "")
            if not ci_logs and event.logs_url:
                # 没有提供CI日志，需要从URL下载
                logs_result = download_ci_logs.invoke({"logs_url": event.logs_url})
                if not logs_result.startswith("Error:"):
                    ci_logs = logs_result
                else:
                    logger.warning(f"下载日志失败: {logs_result}")

            # 2. 解析错误
            error_locations = []
            if ci_logs:
                error_locations = parse_python_errors.invoke({"log_content": ci_logs})

            logger.info(f"解析到Python错误数量: {len(error_locations)}")
            if len(error_locations) > 0:
                import re
                # 过滤ANSI颜色代码和特殊字符
                ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[.*?[a-zA-Z])')
                for i, err in enumerate(error_locations):
                    file_path = err.get('file_path', 'unknown')
                    line_number = err.get('line_number', 0)
                    error_type = err.get('error_type', 'UnknownError')
                    error_msg = err.get('error_message', '')[:100]
                    # 清理特殊字符
                    error_msg = ansi_escape.sub('', error_msg)
                    error_msg = error_msg.encode('gbk', errors='ignore').decode('gbk')
                    logger.info(f"  错误{i+1}: {file_path}:{line_number} {error_type}: {error_msg}")

            if not error_locations:
                logger.warning("日志中未解析到任何Python错误，无需修复")
                # 没有错误，直接结束流程
                async with self.lock:
                    if event_key in self.processed_events:
                        self.processed_events.remove(event_key)
                return {
                    "success": False,
                    "error_message": "日志中未检测到Python错误，无需修复"
                }

            # 3. 克隆仓库（必须在任何需要访问仓库文件的操作之前执行）
            repo_path = ""
            if event.clone_url and event.branch:
                clone_result = clone_repository.invoke({
                    "clone_url": event.clone_url,
                    "branch": event.branch
                })
                if not clone_result.startswith("Error:"):
                    repo_path = clone_result
                else:
                    logger.error(f"克隆仓库失败: {clone_result}")
                    # 克隆失败，移除去重标记，允许后续重试
                    async with self.lock:
                        if event_key in self.processed_events:
                            self.processed_events.remove(event_key)
                    return {
                        "success": False,
                        "error_message": clone_result
                    }
            else:
                logger.error(f"缺少克隆信息: clone_url={event.clone_url}, branch={event.branch}")
                async with self.lock:
                    if event_key in self.processed_events:
                        self.processed_events.remove(event_key)
                return {
                    "success": False,
                    "error_message": "仓库克隆地址或分支为空，无法修复"
                }

            # 确保仓库路径有效
            if not repo_path:
                logger.error("仓库路径为空，无法继续修复")
                async with self.lock:
                    if event_key in self.processed_events:
                        self.processed_events.remove(event_key)
                return {
                    "success": False,
                    "error_message": "仓库克隆失败，路径为空"
                }

            # 设置工具上下文的仓库路径，供后续工具使用
            set_tool_context({
                "github_token": self.github_token,
                "repo_path": repo_path
            })

            # 过滤掉不存在的文件路径的错误（只保留实际存在的文件）
            valid_error_locations = []
            for err in error_locations:
                file_path = err.get("file_path", "")
                if not file_path or file_path == "<string>":
                    continue

                # 清理路径中的相对路径符号和多余的分隔符
                cleaned_file_path = file_path.lstrip("./\\").replace("\\", "/")

                # 检查文件是否存在于克隆的仓库中
                full_file_path = os.path.abspath(os.path.join(repo_path, cleaned_file_path))
                repo_path_abs = os.path.abspath(repo_path)

                # 确保文件在仓库目录内，并且确实存在
                if (full_file_path.startswith(repo_path_abs) and
                    os.path.exists(full_file_path) and
                    os.path.isfile(full_file_path)):
                    valid_error_locations.append(err)
                    # 更新错误中的文件路径为相对路径（统一格式）
                    relative_path = os.path.relpath(full_file_path, repo_path_abs).replace("\\", "/")
                    err["file_path"] = relative_path
                else:
                    logger.info(f"  过滤不存在的文件错误: {file_path} -> 清理后: {cleaned_file_path} -> 完整路径: {full_file_path}")

            error_locations = valid_error_locations
            logger.info(f"过滤后有效Python错误数量: {len(error_locations)}")
            for i, err in enumerate(error_locations):
                logger.info(f"  有效错误{i+1}: {err.get('file_path', 'unknown')}:{err.get('line_number', 0)} {err.get('error_type', 'UnknownError')}: {err.get('error_message', '')[:100]}")

            if not error_locations:
                logger.warning("过滤后没有有效错误，无需修复")
                # 没有有效错误，直接结束流程
                async with self.lock:
                    if event_key in self.processed_events:
                        self.processed_events.remove(event_key)
                return {
                    "success": False,
                    "error_message": "过滤后没有检测到需要修复的有效Python错误"
                }

            # 错误分类前置处理
            is_syntax_error = False
            missing_file_path = None
            is_dependency_error = False
            syntax_error_files = []  # 所有语法错误文件列表

            for err in error_locations:
                error_type = err.get("error_type", "").lower()
                error_msg = err.get("error_message", "")
                file_path = err.get("file_path")

                # B类错误：语法/逻辑错误（最高优先级，不允许跳过）
                if error_type == "syntaxerror" or "syntaxerror" in error_msg.lower():
                    is_syntax_error = True
                    if file_path and file_path not in syntax_error_files:
                        syntax_error_files.append(file_path)

            if syntax_error_files:
                logger.info(f"检测到语法错误，涉及文件: {syntax_error_files}")

                # A类错误：文件缺失
                if "no such file or directory" in error_msg.lower() or "could not open" in error_msg.lower():
                    # 提取缺失的文件路径
                    import re
                    file_match = re.search(r"No such file or directory: '([^']+)'", error_msg)
                    if not file_match:
                        file_match = re.search(r"Could not open '([^']+)'", error_msg)
                    if file_match:
                        missing_file_path = file_match.group(1)
                        # 特殊处理：缺失requirements.txt属于A类
                        if "requirements.txt" in missing_file_path.lower():
                            logger.info(f"检测到缺失文件: {missing_file_path}")

                # C类错误：依赖问题
                if error_type == "modulenotfounderror" or "modulenotfounderror" in error_msg.lower():
                    # 排除缺失requirements.txt的情况
                    if "requirements.txt" not in error_msg.lower():
                        is_dependency_error = True
                        logger.info("检测到依赖缺失错误")

            # 处理A类错误：缺失文件
            if missing_file_path:
                logger.info(f"创建缺失文件: {missing_file_path}")
                from src.agent.tools.langchain_tools import write_file
                # 写入空文件
                write_result = write_file.invoke({
                    "file_path": f"{repo_path}/{missing_file_path}",
                    "content": ""
                })
                if write_result == "Success":
                    logger.info(f"成功创建缺失文件: {missing_file_path}")
                    return {
                        "success": True,
                        "fix_description": f"创建缺失文件: {missing_file_path}",
                        "modified_files": [missing_file_path],
                        "code_changes": {
                            missing_file_path: ""
                        }
                    }
                else:
                    logger.error(f"创建缺失文件失败: {write_result}")
                    return {
                        "success": False,
                        "error_message": f"创建缺失文件失败: {write_result}"
                    }

            # 处理C类错误：依赖问题
            elif is_dependency_error:
                logger.info("检测到依赖/配置错误，不需要修改代码")
                return {
                    "success": True,
                    "error_message": "检测到依赖/环境错误，需要安装对应的依赖包，无需修改代码",
                    "is_env_error": True
                }

            # 处理语法错误无文件路径的情况，主动扫描仓库中的Python文件
            elif is_syntax_error and not syntax_error_files:
                logger.info("语法错误未关联到具体文件，开始扫描仓库中的Python文件")
                from src.agent.tools.langchain_tools import search_files, read_file
                # 搜索所有Python文件
                py_files = search_files.invoke({"pattern": "**/*.py"})
                # 对每个文件进行语法检查
                import ast
                for file_path in py_files:
                    try:
                        # 读取文件内容
                        full_file_path = f"{repo_path}/{file_path}"
                        content = read_file.invoke({"file_path": full_file_path})
                        if not content.startswith("Error:"):
                            # 尝试解析语法
                            ast.parse(content)
                    except SyntaxError as e:
                        # 找到有语法错误的文件
                        if file_path not in syntax_error_files:
                            syntax_error_files.append(file_path)
                        logger.info(f"找到语法错误文件: {file_path}:{e.lineno}")
                        # 更新错误信息
                        for err in error_locations:
                            # 只要错误信息中包含SyntaxError，就更新文件路径
                            if (err.get("error_type") == "SyntaxError" or
                                "syntaxerror" in err.get("error_message", "").lower()):
                                err["file_path"] = file_path
                                err["line_number"] = e.lineno
                                err["error_message"] = str(e)
                                # 确保错误类型正确
                                err["error_type"] = "SyntaxError"
                        # 更新CI日志，添加详细的错误位置信息
                        syntax_error_details = f"""
File "{file_path}", line {e.lineno}
{e.text}
{' ' * (e.offset - 1)}^
SyntaxError: {e.msg}
"""
                        ci_logs = ci_logs + "\n\n" + "="*50 + "\n"
                        ci_logs += "自动扫描找到的语法错误详情：\n"
                        ci_logs += syntax_error_details
                        ci_logs += "\n" + "="*50 + "\n"
                if not syntax_error_files:
                    logger.error("无法定位到用户代码中的语法错误文件，可能错误发生在系统库中")
                    return {
                        "success": False,
                        "error_message": "语法错误发生在Python系统库中，无法自动修复"
                    }

            return {
                "ci_logs": ci_logs,
                "repo_path": repo_path,
                "error_locations": error_locations,
                "retry_count": 0,
                "max_retries": self.max_retries,
                "review_comments": "",  # 初始化审查反馈字段
                "test_output": "",      # 初始化测试反馈字段
                "risk_warnings": [],    # 初始化风险警告字段
                "failed_tests": []      # 初始化失败测试字段
            }

        except Exception as e:
            logger.error(f"收集上下文失败: {e}", exc_info=True)
            return {
                "success": False,
                "error_message": f"收集上下文失败: {str(e)}"
            }

    async def _run_fix_agent(self, state: RepairState) -> Command:
        """
        运行修复Agent节点

        Args:
            state: 当前状态

        Returns:
            Command: 状态更新和路由指令
        """
        logger.info("运行修复Agent")

        # 检查必要上下文
        if not state["repo_path"] or not state["error_locations"] or not state["ci_logs"]:
            error_msg = ""
            if not state["repo_path"]:
                error_msg = "缺少仓库路径，无法生成修复"
            elif not state["error_locations"]:
                error_msg = "缺少错误位置信息，无法生成修复"
            elif not state["ci_logs"]:
                error_msg = "缺少CI日志，无法生成修复"

            return Command(
                update={
                    "success": False,
                    "error_message": error_msg
                },
                goto="handle_failure"
            )

        try:
            # 重试时回滚所有变更，避免旧修复叠加
            if state.get("retry_count", 0) > 0:
                logger.info(f"检测到第 {state['retry_count']} 次重试，回滚所有本地变更")
                from git import Repo
                repo = Repo(state["repo_path"])
                repo.git.checkout("--", ".")

            # 创建修复Agent
            fix_agent = FixAgent(
                repo_path=state["repo_path"],
                llm_model=self.llm_model,
                openai_api_key=self.openai_api_key,
                openai_base_url=self.openai_base_url,
                github_token=self.github_token
            )

            # 准备额外上下文：审查和测试反馈（如果有）
            extra_context = {}
            review_comments = state.get("review_comments", "")
            risk_warnings = state.get("risk_warnings", [])
            test_output = state.get("test_output", "")
            failed_tests = state.get("failed_tests", [])

            if review_comments:
                extra_context["review_feedback"] = review_comments
                logger.info(f"传入审查反馈: {review_comments[:200]}...")
            if risk_warnings:
                extra_context["risk_warnings"] = risk_warnings
                logger.info(f"传入风险警告: {risk_warnings}")
            if test_output:
                extra_context["test_output"] = test_output
                logger.info(f"传入测试输出: {test_output[:200]}...")
            if failed_tests:
                extra_context["failed_tests"] = failed_tests
                logger.info(f"传入失败测试: {failed_tests}")

            # 生成修复
            fix_result = await fix_agent.generate_fix(
                ci_logs=state["ci_logs"],
                error_locations=state["error_locations"],
                **extra_context
            )

            # 处理环境错误：不需要修复，直接成功结束
            if fix_result.get("is_env_error", False):
                logger.info("环境/配置/依赖错误，无需代码修复，流程结束")
                return Command(
                    update={
                        "success": True,
                        "error_message": fix_result["fix_description"]
                    },
                    goto=END
                )

            # 修复失败：没有生成有效的代码变更
            if not fix_result.get("code_changes"):
                return Command(
                    update={
                        "success": False,
                        "error_message": "修复Agent未能生成有效修复代码"
                    },
                    goto="handle_failure"
                )

            # 先读取所有需要修改文件的原始内容
            from src.agent.tools.langchain_tools import read_file, write_file, get_diff
            import difflib
            original_codes = {}
            for file_path in fix_result["code_changes"].keys():
                try:
                    original_content = read_file.invoke({"file_path": file_path})
                    if not original_content.startswith("Error:"):
                        original_codes[file_path] = original_content
                except Exception as e:
                    logger.warning(f"读取原始文件 {file_path} 失败: {str(e)}")

            # 验证修复是否真的修改了代码
            logger.info("=== 代码变更验证 ===")
            for file_path in fix_result["code_changes"].keys():
                original = original_codes.get(file_path, "")
                fixed = fix_result["code_changes"].get(file_path, "")
                original_len = len(original)
                fixed_len = len(fixed)
                is_identical = (original == fixed)
                logger.info(f"文件 {file_path}: 原始={original_len}字符, 修复={fixed_len}字符, 完全相同={is_identical}")
                if not is_identical:
                    # 显示实际差异（前后各50字符）
                    diff = difflib.unified_diff(
                        original.splitlines(True), fixed.splitlines(True),
                        fromfile=f"原始/{file_path}", tofile=f"修复/{file_path}",
                        n=2
                    )
                    diff_text = "".join(diff)
                    logger.info(f"文件 {file_path} 的差异:\n{diff_text[:1000]}")

            # 检查是否所有修复文件都没有变化
            unchanged_files = [
                fp for fp in fix_result["code_changes"].keys()
                if original_codes.get(fp, "") == fix_result["code_changes"].get(fp, "")
            ]
            if unchanged_files and len(unchanged_files) == len(fix_result["code_changes"]):
                logger.error(f"修复Agent返回的代码与原始代码完全一致，没有实际修改！文件: {unchanged_files}")
                # 直接让审查阶段处理（auto_compare_codes会检测到并拒绝）
                # 但为了更好的错误信息，修改fix_description
                fix_result["fix_description"] = f"WARNING: 所有修复文件代码与原始代码完全一致，没有实际修改"
                error_details = []
                for err in state.get("error_locations", []):
                    error_details.append(
                        f"  {err.get('file_path','?')}:{err.get('line_number','?')} {err.get('error_type','?')}: {err.get('error_message','?')[:100]}"
                    )
                logger.error(f"检测到的原始错误:\n" + "\n".join(error_details))

            # 应用修复到本地仓库
            for file_path, content in fix_result["code_changes"].items():
                write_result = write_file.invoke({
                    "file_path": file_path,
                    "content": content
                })
                if write_result != "Success":
                    logger.warning(f"写入文件 {file_path} 失败: {write_result}")

            # 获取diff
            diff_content = get_diff.invoke({"base_branch": state["event"].branch})

            # 只返回状态更新，由固定边跳转到review_changes
            update_state = {
                "fix_description": fix_result["fix_description"],
                "modified_files": fix_result["modified_files"],
                "code_changes": fix_result["code_changes"],
                "diff_content": diff_content,
                "original_codes": original_codes,
                "retry_count": state["retry_count"] + 1
            }

            # 只有在首次修复且没有任何反馈时才重置反馈字段
            # 重试时保留所有反馈信息，用于指导下一次修复
            if state["retry_count"] == 0 and not state.get("review_comments") and not state.get("test_output"):
                update_state.update({
                    "review_comments": "",
                    "test_output": "",
                    "risk_warnings": [],
                    "failed_tests": []
                })

            return Command(update=update_state)

        except Exception as e:
            logger.error(f"修复Agent执行失败: {e}", exc_info=True)
            return Command(
                update={
                    "success": False,
                    "error_message": f"修复Agent执行失败: {str(e)}"
                },
                goto="handle_failure"
            )

    async def _review_changes(self, state: RepairState) -> Dict[str, Any]:
        """
        审查代码变更节点（使用审查Agent）

        Args:
            state: 当前状态

        Returns:
            Dict: 状态更新
        """
        logger.info("运行审查Agent")

        try:
            # 创建审查Agent
            review_agent = ReviewAgent(
                llm_model=self.llm_model,
                openai_api_key=self.openai_api_key,
                openai_base_url=self.openai_base_url,
                max_change_lines=self.max_change_lines
            )

            # 执行审查
            review_result = await review_agent.review_changes(
                error_locations=state["error_locations"],
                fix_description=state["fix_description"],
                modified_files=state["modified_files"],
                code_changes=state["code_changes"],
                diff_content=state["diff_content"],
                repo_path=state["repo_path"],
                original_codes=state.get("original_codes")
            )

            return review_result

        except Exception as e:
            logger.error(f"审查Agent执行失败: {e}", exc_info=True)
            return {
                "review_passed": False,
                "review_comments": f"审查过程出错: {str(e)}",
                "change_lines": 0,
                "risk_warnings": [str(e)]
            }

    def _route_after_review(self, state: RepairState) -> str:
        """
        审查后的路由逻辑
        """
        # 如果有明确的错误消息且success为False，说明之前的节点已经失败
        if state.get("success") is False and state.get("error_message"):
            logger.error(f"流程已失败: {state.get('error_message', '未知错误')}")
            return "handle_failure"

        # 审查不通过，检查重试次数
        if not state.get("review_passed", False):
            retry_count = state.get("retry_count", 0)
            if retry_count < state["max_retries"]:
                logger.info(f"审查失败，重试修复 (第 {retry_count + 1} 次)")
                return "fix_agent"
            else:
                logger.error("超过最大重试次数，修复失败")
                return "handle_failure"

        # 审查通过，进入测试阶段
        logger.info("审查通过，进入测试阶段")
        return "run_tests"

    async def _run_tests(self, state: RepairState) -> Dict[str, Any]:
        """
        运行测试节点（使用测试Agent）

        Args:
            state: 当前状态

        Returns:
            Dict: 状态更新
        """
        logger.info("运行测试Agent")

        try:
            # 创建测试Agent
            test_agent = TestAgent(
                repo_path=state["repo_path"],
                llm_model=self.llm_model,
                openai_api_key=self.openai_api_key,
                openai_base_url=self.openai_base_url,
                test_command="pytest"
            )

            # 执行测试并验证修复
            test_result = await test_agent.verify_fix(
                error_locations=state["error_locations"],
                fix_description=state["fix_description"],
                diff_content=state["diff_content"]
            )

            return test_result

        except Exception as e:
            logger.error(f"测试Agent执行失败: {e}", exc_info=True)
            return {
                "test_passed": False,
                "test_output": f"测试执行失败: {str(e)}",
                "failed_tests": [],
                "verification_summary": f"测试过程出错: {str(e)}"
            }

    def _route_after_test(self, state: RepairState) -> str:
        """
        测试后的路由逻辑
        """
        # 如果有明确的错误消息且success为False，说明之前的节点已经失败
        if state.get("success") is False and state.get("error_message"):
            logger.error(f"流程已失败: {state.get('error_message', '未知错误')}")
            return "handle_failure"

        # 测试不通过，检查重试次数
        if not state.get("test_passed", False):
            retry_count = state.get("retry_count", 0)
            if retry_count < state["max_retries"]:
                logger.info(f"测试失败，重试修复 (第 {retry_count + 1} 次)")
                return "fix_agent"
            else:
                logger.error("超过最大重试次数，修复失败")
                return "handle_failure"

        # 测试通过，创建PR
        logger.info("测试通过，准备创建PR")
        return "create_pr"

    async def _create_pull_request(self, state: RepairState) -> Dict[str, Any]:
        """
        创建PR节点

        Args:
            state: 当前状态

        Returns:
            Dict: 状态更新
        """
        event: GitHubEvent = state["event"]
        logger.info(f"创建PR: {event.repository}")

        try:
            from git import Repo
            repo = Repo(state["repo_path"])

            # 创建新分支
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            branch_name = f"autofix/{event.event_type}_{timestamp}"

            create_branch_result = create_branch.invoke({"branch_name": branch_name})
            if create_branch_result != "Success":
                return {
                    "success": False,
                    "error_message": f"创建分支失败: {create_branch_result}"
                }

            # 提交变更
            commit_message = f"Auto-fix: {state['fix_description']}\n\nGenerated by SpiderClaw AutoFix Agent."
            commit_result = commit_changes.invoke({"message": commit_message})
            if commit_result.startswith("Error:"):
                return {
                    "success": False,
                    "error_message": f"提交变更失败: {commit_result}"
                }

            # 设置工具上下文
            set_tool_context({
                "repo_path": state["repo_path"],
                "github_token": self.github_token
            })

            # 推送分支
            push_result = push_branch.invoke({"branch_name": branch_name})
            if push_result != "Success":
                return {
                    "success": False,
                    "error_message": f"推送分支失败: {push_result}"
                }

            # 创建PR
            pr_title = f"[AutoFix] {state['fix_description']}"
            pr_body = f"""## 修复说明
{state['fix_description']}

## 变更详情
- 修复的错误类型: {', '.join(err.get('error_type', 'Unknown') for err in state['error_locations'])}
- 修改文件: {', '.join(state['modified_files'])}
- 变更行数: {state['change_lines']} 行

## 审查结果
✅ 审查通过
{'风险警告: ' + '; '.join(state['risk_warnings']) if state['risk_warnings'] else '无风险警告'}

## 测试结果
✅ 测试通过
{len(state['failed_tests'])} 个测试失败

---
此PR由SpiderClaw自动修复系统生成
"""

            pr_url = create_pull_request.invoke({
                "repo_full_name": event.repository,
                "head_branch": branch_name,
                "base_branch": event.branch,
                "title": pr_title,
                "body": pr_body
            })

            if not pr_url.startswith("Error:"):
                # 从URL中提取PR编号
                pr_number = int(pr_url.split("/")[-1]) if pr_url else None
                return {
                    "pr_url": pr_url,
                    "pr_number": pr_number,
                    "success": True,
                    "error_message": ""
                }
            else:
                return {
                    "success": False,
                    "error_message": pr_url
                }

        except Exception as e:
            logger.error(f"创建PR失败: {e}", exc_info=True)
            return {
                "success": False,
                "error_message": f"创建PR失败: {str(e)}"
            }

    async def _handle_failure(self, state: RepairState) -> Dict[str, Any]:
        """
        处理失败节点
        """
        error_msg = state.get("error_message", "未知错误")
        logger.error(f"修复流程失败: {error_msg}")

        # 临时目录会在工具上下文被覆盖时自动清理
        return {
            "success": False,
            "error_message": error_msg
        }

    async def run(self, event: GitHubEvent) -> Dict[str, Any]:
        """
        运行修复流程

        Args:
            event: GitHub事件对象

        Returns:
            Dict: 最终修复结果
        """
        logger.info(f"启动修复流程: {event.event_id}")

        try:
            # 初始化状态
            initial_state: RepairState = {
                "event": event,
                "ci_logs": "",
                "repo_path": "",
                "error_locations": [],
                "fix_description": "",
                "modified_files": [],
                "code_changes": {},
                "original_codes": {},
                "diff_content": "",
                "review_passed": False,
                "review_comments": "",
                "change_lines": 0,
                "risk_warnings": [],
                "test_passed": False,
                "test_output": "",
                "failed_tests": [],
                "pr_url": None,
                "pr_number": None,
                "success": False,
                "error_message": "",
                "retry_count": 0,
                "max_retries": self.max_retries
            }

            # 运行图
            final_state = await self.graph.ainvoke(initial_state)

            logger.info(f"修复流程完成: 成功={final_state['success']}")
            if final_state.get("pr_url"):
                logger.info(f"PR地址: {final_state['pr_url']}")

            return final_state

        except Exception as e:
            logger.error(f"修复流程异常: {e}", exc_info=True)
            return {
                "success": False,
                "error_message": f"修复流程异常: {str(e)}"
            }

    async def run_repair(self, event: GitHubEvent, ci_logs: str = "") -> Dict[str, Any]:
        """
        运行修复流程，支持直接传入CI日志（用于本地测试）

        Args:
            event: GitHub事件对象
            ci_logs: 可选的CI日志内容，如果提供则不下载日志

        Returns:
            Dict: 最终修复结果
        """
        logger.info(f"启动本地修复流程: {event.event_id}")

        try:
            # 初始化状态
            initial_state: RepairState = {
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
                "change_lines": 0,
                "risk_warnings": [],
                "test_passed": False,
                "test_output": "",
                "failed_tests": [],
                "pr_url": None,
                "pr_number": None,
                "success": False,
                "error_message": "",
                "retry_count": 0,
                "max_retries": self.max_retries
            }

            # 运行图
            final_state = await self.graph.ainvoke(initial_state)

            logger.info(f"修复流程完成: 成功={final_state['success']}")
            if final_state.get("pr_url"):
                logger.info(f"PR地址: {final_state['pr_url']}")

            return final_state

        except Exception as e:
            logger.error(f"修复流程异常: {e}", exc_info=True)
            return {
                "success": False,
                "error_message": f"修复流程异常: {str(e)}"
            }
