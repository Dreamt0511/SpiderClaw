"""修复Agent实现 - LangChain标准版本"""

from typing import Dict, Any, List
import json
import logging
import re
from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from src.agent.state import ErrorLocation, FixAttempt
from src.agent.prompts.fix_agent import FIX_AGENT_SYSTEM_PROMPT, FIX_AGENT_USER_PROMPT
from src.agent.tools.langchain_tools import set_tool_context, search_code, search_files, read_file as _read_file
from src.agent.code_context import build_error_context_section
from src.utils.audit import AuditCallbackHandler

logger = logging.getLogger(__name__)


class FixAgent:
    """修复Agent，使用LangChain标准工具调用模式"""

    def __init__(
        self,
        repo_path: str,
        llm_model: str = "gpt-4o",
        temperature: float = 0.1,
        openai_api_key: str = None,
        openai_base_url: str = "https://api.openai.com/v1",
        github_token: str = None,
        system_prompt_override: str = "",
        max_change_lines: int = 50,
    ):
        self.repo_path = repo_path
        self.github_token = github_token
        self.max_change_lines = max_change_lines
        self.system_prompt_override = system_prompt_override

        self.llm = ChatOpenAI(
            model=llm_model,
            temperature=temperature,
            api_key=openai_api_key,
            base_url=openai_base_url,
            callbacks=[AuditCallbackHandler("修复Agent")],
        )

        # 目标文件映射：generate_fix 调用前动态设置
        # key=文件ID(0,1,2...), value=相对路径
        self._target_file_map: Dict[int, str] = {}

        # 受限的 read_file 工具：只能通过 ID 读取预设的目标文件
        agent_self = self

        @tool
        def read_target_file(id: int) -> str:
            """读取目标文件的完整代码。只能传入 prompt 中列出的文件 ID（0, 1, 2, ...）。

            Args:
                id: 文件 ID，对应 prompt 中「可读取文件列表」的编号
            """
            if id not in agent_self._target_file_map:
                available = sorted(agent_self._target_file_map.keys())
                return f"Error: 无效的文件 ID {id}。可用 ID: {available}"
            rel_path = agent_self._target_file_map[id]
            set_tool_context({"repo_path": agent_self.repo_path, "github_token": agent_self.github_token})
            return _read_file.invoke({"file_path": f"{agent_self.repo_path}/{rel_path}"})

        self.read_target_file_tool = read_target_file

    def _create_agent(self, run_limit: int):
        """创建 Agent，run_limit = 目标文件数量（每个文件最多读一次）"""
        prompt = FIX_AGENT_SYSTEM_PROMPT.replace(
            "__MAX_CHANGE_LINES__", str(self.max_change_lines)
        )
        if self.system_prompt_override:
            prompt = self.system_prompt_override + "\n\n" + prompt

        return create_agent(
            model=self.llm,
            tools=[self.read_target_file_tool],
            system_prompt=prompt,
            middleware=[
                ToolCallLimitMiddleware(
                    tool_name="read_target_file",
                    run_limit=run_limit,
                ),
            ],
        )

    @staticmethod
    def _parse_json_safely(json_str: str) -> Dict[str, Any] | None:
        """健壮的JSON解析，尝试多种策略处理LLM常见的JSON格式问题"""
        import re as _re

        json_str = json_str.strip().lstrip("﻿")

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        try:
            return json.loads(json_str, strict=False)
        except json.JSONDecodeError:
            pass

        try:
            cleaned = _re.sub(r",\s*([}\]])", r"\1", json_str)
            return json.loads(cleaned, strict=False)
        except json.JSONDecodeError:
            pass

        try:
            def _escape_code_content(m):
                prefix = m.group(1)
                content = m.group(2)
                escaped = content.replace('\\"', "\x00")
                escaped = escaped.replace('"', '\\"')
                escaped = escaped.replace("\x00", '\\"')
                return prefix + escaped + '"'

            pattern = _re.compile(
                r'("(?:code_changes|fix_description)"\s*:\s*")(.*?)(?<!\\")"(?=\s*[,}\]])',
                _re.DOTALL,
            )
            repaired = json_str
            for _ in range(3):
                new_repaired = pattern.sub(_escape_code_content, repaired)
                if new_repaired == repaired:
                    break
                repaired = new_repaired
            return json.loads(repaired, strict=False)
        except (json.JSONDecodeError, Exception):
            pass

        try:
            result = {"fix_description": "", "modified_files": [], "code_changes": {}}
            fd_match = _re.search(r'"fix_description"\s*:\s*"((?:[^"\\]|\\.)*)"', json_str)
            if fd_match:
                result["fix_description"] = fd_match.group(1)
            mf_match = _re.search(r'"modified_files"\s*:\s*\[(.*?)\]', json_str)
            if mf_match:
                result["modified_files"] = _re.findall(r'"([^"]+)"', mf_match.group(1))
            cc_match = _re.search(r'"code_changes"\s*:\s*{(.+)}', json_str, _re.DOTALL)
            if cc_match:
                files = _re.findall(r'"([^"]+\.py)"\s*:', cc_match.group(1))
                result["code_changes"] = {f: "" for f in files}
            if result["code_changes"]:
                return result
        except Exception:
            pass

        return None

    def _serialize_error_locations(self, error_locations: list) -> str:
        """将错误位置列表转换为 JSON 字符串（兼容 dict 和 ErrorLocation）"""
        if not error_locations:
            return "[]"
        if isinstance(error_locations[0], ErrorLocation):
            return json.dumps(
                [e.model_dump() for e in error_locations],
                ensure_ascii=False, indent=2,
            )
        return json.dumps(error_locations, ensure_ascii=False, indent=2)

    def _build_fix_history_summary(self, fix_history: list[FixAttempt] | None) -> str:
        """构建历史修复记录摘要"""
        if not fix_history:
            return "无历史记录"
        lines = []
        for attempt in fix_history:
            lines.append(
                f"- 第{attempt.attempt}次: 被 {attempt.rejected_by} 拒绝，"
                f"原因: {attempt.rejection_reason}，"
                f"修改摘要: {attempt.diff_summary}"
            )
        return "\n".join(lines)

    async def generate_fix(
        self,
        ci_logs: str,
        error_locations: list,
        target_files: list[str] | None = None,  # NEW: orchestrator 提取的确定性文件列表
        original_codes: dict[str, str] | None = None,
        review_feedback: str = "",
        risk_warnings: list[str] | None = None,
        test_output: str = "",
        failed_tests: list[str] | None = None,
        mandatory_instructions: str = "",
        fix_history: list[FixAttempt] | None = None,
        retry_count: int = 0,
    ) -> Dict[str, Any]:
        """
        生成修复代码

        Args:
            ci_logs: CI失败日志内容
            error_locations: 错误位置列表 (list[ErrorLocation] 或 list[dict])
            target_files: orchestrator 提取的确定性目标文件列表（优先使用，不从 error_locations 推导）
            original_codes: 原始代码快照 (file_path → content)
            review_feedback: 审查反馈（重试时提供）
            risk_warnings: 风险警告列表（重试时提供）
            test_output: 测试输出（重试时提供）
            failed_tests: 失败的测试用例列表（重试时提供）
            mandatory_instructions: 强制性修复指令（重试时由规则引擎生成）
            fix_history: 历史修复尝试记录
            retry_count: 重试次数
        """
        try:
            logger.info(f"使用LangChain Agent生成修复代码，重试次数: {retry_count}")
            token_usage = 0  # 本次调用总token消耗


            # 使用 orchestrator 提供的 target_files（确定性），兜底从 error_locations 推导
            tf = target_files if target_files else None
            if tf is None:
                tf = []
                for err in error_locations:
                    fp = err.file_path if isinstance(err, ErrorLocation) else err.get("file_path")
                    if fp and fp != "<string>" and fp not in tf:
                        tf.append(fp)

                # 无 file_path 时从 traceback 提取
                for err in error_locations:
                    if isinstance(err, ErrorLocation):
                        fp = err.file_path
                        tb = err.traceback
                    else:
                        fp = err.get("file_path", "")
                        tb = err.get("traceback", "")
                    if fp and fp != "<string>" and fp not in tf:
                        tf.append(fp)
                    elif not fp and tb:
                        tb_files = re.findall(r'File "([^"]+\.py)"', tb)
                        for p in tb_files:
                            if p != "<string>" and p not in tf:
                                tf.append(p)
                                logger.info(f"从 traceback 提取文件到目标列表: {p}")

            if tf:
                logger.info(f"目标修复文件列表: {tf}")

            # 如果没有 file_path，尝试从错误消息搜索定位
            if not tf and error_locations:
                all_error_msgs = []
                for err in error_locations:
                    msg = err.error_message if hasattr(err, 'error_message') else err.get('error_message', '')
                    etype = err.error_type if hasattr(err, 'error_type') else err.get('error_type', '')
                    if msg:
                        all_error_msgs.append(f"{etype}: {msg}")

                combined = ' '.join(all_error_msgs)

                # 策略1: 从错误消息中提取文件路径 File "xxx.py"
                file_paths_in_msg = re.findall(r'File "([^"]+\.py)"', combined)
                for p in file_paths_in_msg:
                    if p != "<string>" and p not in tf:
                        tf.append(p)
                if tf:
                    logger.info(f"从错误消息中提取到文件路径: {tf}")

                # 策略2: NameError — name 'xxx' is not defined
                if not tf:
                    name_match = re.search(r"name '(\w+)' is not defined", combined)
                    if name_match:
                        missing_name = name_match.group(1)
                        search_result = search_code.invoke({"keyword": missing_name, "file_type": "py"})
                        if search_result:
                            tf = list(set(r["file_path"] for r in search_result))
                            logger.info(f"通过搜索 '{missing_name}' 定位到文件: {tf}")

                # 策略3: ImportError — No module named 'xxx'
                if not tf:
                    import_match = re.search(r"No module named '?(\w+)'?", combined)
                    if import_match:
                        missing_module = import_match.group(1)
                        tf = [f"{missing_module.replace('.', '/')}.py"]
                        logger.info(f"通过模块名猜测文件: {tf}")

                # 策略4: 从错误消息中提取带 .py 的文件名
                if not tf:
                    py_files_in_msg = re.findall(r"(\w+\.py)", combined)
                    for f in py_files_in_msg:
                        if f not in tf:
                            tf.append(f)
                    if tf:
                        logger.info(f"从错误消息中提取 .py 文件名: {tf}")

                # 策略5: 提取错误消息中的关键词（函数名、变量名等），搜索代码库
                if not tf:
                    keywords = set()
                    for msg in all_error_msgs:
                        parts = re.split(r"['\"]", msg)
                        for part in parts:
                            if part.isidentifier() and len(part) > 2 and not part.startswith('_'):
                                keywords.add(part)
                    for kw in sorted(keywords, key=len, reverse=True)[:5]:
                        search_result = search_code.invoke({"keyword": kw, "file_type": "py"})
                        if search_result:
                            tf = list(set(r["file_path"] for r in search_result))
                            logger.info(f"通过关键词 '{kw}' 定位到文件: {tf}")
                            break

                # 策略6: 兜底 — 列出所有 Python 文件
                if not tf:
                    all_py_files = search_files.invoke({"pattern": "**/*.py"})
                    if all_py_files:
                        tf = all_py_files[:10]
                        logger.warning(f"无法定位错误文件，使用全部 Python 文件: {tf}")
                    else:
                        logger.error("仓库中无 Python 文件")
                        return {
                            "fix_description": "仓库中没有找到 Python 文件",
                            "modified_files": [],
                            "code_changes": {},
                            "error": "no_target_file",
                        }

                logger.info(f"目标文件: {tf}")

            set_tool_context(
                {"repo_path": self.repo_path, "github_token": self.github_token}
            )

            # 构建动态部分
            review_feedback_section = f"""{review_feedback}""" if review_feedback else ""

            risk_warnings_list = risk_warnings or []
            risk_warnings_section = (
                f"""{"\n- ".join(risk_warnings_list)}"""
                if risk_warnings_list else ""
            )

            test_output_section = f"""```{test_output}```""" if test_output else ""

            failed_tests_list = failed_tests or []
            failed_tests_section = (
                f"""- {"\n- ".join(failed_tests_list)}"""
                if failed_tests_list else ""
            )

            # 强制性指令
            mandatory_section = mandatory_instructions if mandatory_instructions else "无特殊指令"

            # 历史修复摘要
            fix_history_summary = self._build_fix_history_summary(fix_history)

            # 根因错误识别与优先处理
            root_cause_errors = []
            for err in error_locations:
                is_root = err.is_root_cause if isinstance(err, ErrorLocation) else err.get("is_root_cause")
                if is_root:
                    root_cause_errors.append(err)

            root_cause_section = ""
            if root_cause_errors:
                root_cause_lines = []
                for err in root_cause_errors:
                    fp = err.file_path if isinstance(err, ErrorLocation) else err.get("file_path", "未知文件")
                    et = err.error_type if isinstance(err, ErrorLocation) else err.get("error_type", "UnknownError")
                    em = err.error_message if isinstance(err, ErrorLocation) else err.get("error_message", "")
                    chain_info = ""
                    consequence = err.chain_consequence if isinstance(err, ErrorLocation) else err.get("chain_consequence")
                    if consequence:
                        chain_info = f"\n      → 导致: {consequence[:100]}"
                    root_cause_lines.append(
                        f"- **{et}**: {em[:100]}（文件: {fp}）{chain_info}"
                    )

                root_cause_section = """\
## ⚠️ 根因错误（必须优先修复）
以下错误是链式错误中的根本原因，**必须先修复它们**：

""" + "\n".join(root_cause_lines) + """

**规则**：
- 根因错误必须优先处理，后果错误（由根因导致的二次错误）会在根因修复后自动消除
- 如果根因是 ModuleNotFoundError → 使用条件导入（try/except ImportError）或移除对缺失模块的依赖
- 根因错误修复前，本次修复不被视为成功\
"""
                logger.info(f"检测到 {len(root_cause_errors)} 个根因错误")

            # 序列化错误位置
            error_locations_json = self._serialize_error_locations(error_locations)
            logger.info(f"找到错误数量: {len(error_locations)}")
            logger.info(f"目标修复文件列表: {tf}")

            # 构建错误代码上下文区块（仅展示与错误相关的代码片段，而非完整文件）
            error_context_section = ""
            if original_codes and tf and error_locations:
                error_context_section = build_error_context_section(
                    original_codes=original_codes,
                    error_locations=error_locations,
                    target_files=tf,
                    context_lines=8,
                )
                logger.info(
                    f"错误代码上下文区块构建完成: {len(error_context_section)} 字符"
                )
            elif original_codes:
                logger.warning("有 original_codes 但无目标文件或错误位置，跳过上下文构建")

            # 设置目标文件 ID 映射（read_target_file 工具使用）
            self._target_file_map = {i: fp for i, fp in enumerate(tf)} if tf else {}

            # 创建 Agent：run_limit = 目标文件数（每个文件最多读一次完整代码）
            agent = self._create_agent(run_limit=max(len(tf), 1))

            # 构建可读取文件列表（注入 prompt，让 Agent 知道可用的文件 ID）
            target_file_list_section = ""
            if self._target_file_map:
                rows = "\n".join(
                    f"| {fid} | {fpath} |" for fid, fpath in self._target_file_map.items()
                )
                target_file_list_section = (
                    "## 📂 可读取文件列表\n\n"
                    "| ID | 文件路径 |\n|----|----------|\n"
                    + rows
                    + "\n\n"
                    + "- 错误代码上下文片段已在下方展示，通常足够定位问题\n"
                    + "- 如果片段不够，使用 `read_target_file(id=N)` 读取完整文件代码\n"
                    + "- 你**只能**读取上表中的文件，ID 越界会返回错误\n"
                    + "- 你**只能**修改这些文件，`code_changes` 的 key 必须是表中的文件路径\n"
                )
                logger.info(f"目标文件 ID 映射: {self._target_file_map}")

            # 构建目标文件指令
            force_instruction_content = ""
            if tf:
                if len(tf) == 1:
                    file_path = tf[0]
                    force_instruction_content = f"""1. **唯一修复目标文件：{file_path}**
   - 你 **只能** 修复这个文件，绝对不允许修改或返回其他任何文件
   - 在你的JSON响应中，`modified_files`数组 **必须** 只包含["{file_path}"]
   - 在你的JSON响应中，`code_changes`对象的key **必须** 是"{file_path}"
2. **只修复上报的错误本身**：不要做安全替代（eval→ast.literal_eval 等）、代码优化、重构。
   只修改与错误直接相关的代码行，其他代码原样保留。
"""
                else:
                    files_str = "、".join(tf)
                    files_json = '", "'.join(tf)
                    force_instruction_content = f"""1. **修复目标文件列表：{files_str}**
   - 你 **只能** 修复列表中的这些文件，绝对不允许修改或返回其他任何文件
   - 在你的JSON响应中，`modified_files`数组 **必须** 只包含["{files_json}"]
   - 在你的JSON响应中，`code_changes`对象的key **必须** 是上述列表中的文件路径
   - **`code_changes` 必须包含列表中的每一个文件**，遗漏任何一个文件都会导致校验失败，消耗一次重试机会
2. **只修复上报的错误本身**：不要做安全替代（eval→ast.literal_eval 等）、代码优化、重构。
   只修改与错误直接相关的代码行，其他代码原样保留。

🔄 **输出前请确认**：`code_changes` 的 key 集合 = ["{files_json}"]，一个都不能少。"""
                logger.info(f"构建目标文件指令: {force_instruction_content[:300]}...")
            else:
                logger.info("没有找到带文件路径的错误，不添加目标文件指令")

            # ci_logs 截断：只保留最后 2000 字符（错误通常在末尾）
            ci_logs_display = ci_logs[-2000:] if len(ci_logs) > 2000 else ci_logs

            user_input = FIX_AGENT_USER_PROMPT.format(
                force_instruction_content=force_instruction_content,
                ci_logs=ci_logs_display,
                error_locations=error_locations_json,
                repo_path=self.repo_path,
                root_cause_section=root_cause_section,
                target_file_list_section=target_file_list_section,
                error_context_section=error_context_section,
                review_feedback_section=review_feedback_section,
                risk_warnings_section=risk_warnings_section,
                test_output_section=test_output_section,
                failed_tests_section=failed_tests_section,
                mandatory_instructions=mandatory_section,
                fix_history_summary=fix_history_summary,
            )

            logger.info(f"用户提示词构建完成，长度: {len(user_input)}")
            # 调试：打印 prompt 中代码区块（截取开头）
            if error_context_section:
                idx = user_input.find("## 📂 错误代码上下文")
                if idx >= 0:
                    snippet = user_input[idx:idx+800]
                    logger.info(f"Prompt中错误代码上下文区块:\n{snippet}...")

            config = {"recursion_limit": 50}
            result = await agent.ainvoke({"input": user_input}, config=config)
            logger.info("Agent调用完成")

            # 获取token用量（遍历所有AIMessage累加，agent循环中每次LLM调用都有独立的token_usage）
            try:
                from langchain_core.messages import AIMessage as _AIM
                for msg in result.get("messages", []):
                    if isinstance(msg, _AIM) and hasattr(msg, "response_metadata") and msg.response_metadata:
                        usage = msg.response_metadata.get("token_usage", {})
                        token_usage += usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
            except Exception as e:
                logger.debug(f"获取token用量失败: {e}")

            response_content = result["messages"][-1].content
            logger.info(f"修复Agent原始响应长度: {len(response_content)}")

            import re as _re

            json_content = ""

            json_match = _re.search(
                r"```json\s*(.*?)\s*```", response_content, _re.DOTALL
            )
            if json_match:
                json_content = json_match.group(1)
            else:
                json_match = _re.search(r"\{.*\}", response_content, _re.DOTALL)
                if json_match:
                    json_content = json_match.group(0)
                else:
                    start_idx = response_content.find("{")
                    end_idx = response_content.rfind("}")
                    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                        json_content = response_content[start_idx : end_idx + 1]
                    else:
                        json_content = response_content.strip()
                        logger.warning(f"无法提取JSON，响应内容为: {response_content}")

            fix_result = self._parse_json_safely(json_content)
            if fix_result is None:
                logger.error(f"JSON解析失败，内容: {json_content}")
                raise json.JSONDecodeError("所有修复策略均失败", json_content, 0)
            logger.info(f"JSON解析成功: {fix_result}")

            required_fields = ["fix_description", "modified_files", "code_changes"]
            for field in required_fields:
                if field not in fix_result:
                    raise ValueError(f"修复结果缺少必要字段: {field}")

            def normalize_path(path):
                return path.replace("\\", "/").removeprefix("./")

            fix_result["modified_files"] = [
                normalize_path(f) for f in fix_result["modified_files"]
            ]

            normalized_code_changes = {}
            for path, content in fix_result["code_changes"].items():
                normalized_code_changes[normalize_path(path)] = content
            fix_result["code_changes"] = normalized_code_changes

            # 修复结果验证
            if tf:
                expected_files = [f.removeprefix("./").replace("\\", "/") for f in tf]
                expected_files = list(set(expected_files))

                returned_files = [
                    f.removeprefix("./").replace("\\", "/")
                    for f in fix_result.get("code_changes", {}).keys()
                ]
                invalid_files = [
                    f for f in returned_files if f not in expected_files
                ]
                missing_files = [f for f in expected_files if f not in returned_files]

                # 移除无关文件
                if invalid_files:
                    logger.warning(
                        f"修复Agent返回了目标列表外的文件（已移除）: {invalid_files}"
                    )
                    # 从 code_changes 和 modified_files 中移除无关文件
                    for f in invalid_files:
                        fix_result["code_changes"].pop(f, None)
                    fix_result["modified_files"] = [
                        f for f in fix_result.get("modified_files", [])
                        if f not in invalid_files
                    ]

                    # 🔴 补充：强制清理 fix_description，移除包含无效文件名的行
                    desc_lines = fix_result.get("fix_description", "").split('\n')
                    cleaned_lines = []
                    for line in desc_lines:
                        if any(invalid_file in line for invalid_file in invalid_files):
                            continue
                        cleaned_lines.append(line)
                    fix_result["fix_description"] = '\n'.join(cleaned_lines)
                    logger.info(f"已从修复描述中移除无效文件相关行")

                # 🔴 新增：如果移除无关文件后，目标文件仍缺失，直接报错
                if missing_files:
                    remaining_files = list(fix_result.get("code_changes", {}).keys())
                    if not any(f in remaining_files for f in expected_files):
                        logger.error(
                            f"修复Agent未能修复任何目标文件！"
                            f"期望: {expected_files}, 实际: {remaining_files}"
                        )
                        return {
                            "fix_description": "修复Agent未能修复目标文件",
                            "modified_files": fix_result.get("modified_files", []),
                            "code_changes": fix_result.get("code_changes", {}),
                            "error": "target_files_missing",
                            "invalid_files": invalid_files,
                            "expected_files": expected_files,
                        }
                    else:
                        # 部分修复：记录但仍继续
                        logger.warning(
                            f"修复Agent遗漏了部分目标文件: {missing_files}"
                        )

            if (
                not fix_result.get("code_changes")
                or len(fix_result["code_changes"]) == 0
            ):
                logger.error("修复Agent返回了空的code_changes")
                return {
                    "fix_description": "修复Agent未能生成有效的代码变更",
                    "modified_files": fix_result.get("modified_files", []),
                    "code_changes": fix_result.get("code_changes", {}),
                    "error": "code_changes为空",
                }

            # 验证修复后的代码没有语法错误（仅记录日志，让 validation_gate 拦截）
            import ast
            for file_path, code_content in fix_result["code_changes"].items():
                try:
                    ast.parse(code_content)
                    logger.info(f"文件 {file_path} 语法检查通过")
                except SyntaxError as e:
                    logger.warning(
                        f"修复后代码仍有语法错误（交由validation_gate处理）: "
                        f"{file_path}: {e}"
                    )

            logger.info(f"修复生成成功: {fix_result['fix_description']}")
            fix_result["token_usage"] = token_usage
            return fix_result

        except Exception as e:
            logger.error(f"生成修复失败: {e}", exc_info=True)
            return {
                "fix_description": f"生成修复失败: {str(e)}",
                "modified_files": [],
                "code_changes": {},
                "error": str(e),
                "token_usage": token_usage,
            }
