"""修复Agent实现 - LangChain标准版本"""

from typing import Dict, Any, List
import json
import logging
import re
from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.tools import tool
from langchain_core.messages import AIMessage as _AIM
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

    def _create_agent(self, run_limit: int, target_constraint: str = ""):
        """创建 Agent，run_limit = 目标文件数量（每个文件最多读一次）

        系统提示词优先级（从高到低）：
        1. orchestrator 的 system_prompt_override（重试警告 + 累积的强制指令）
        2. target_constraint（目标文件硬约束，每次调用都注入）
        3. 基础系统提示词（输出格式 + 核心约束）
        """
        prompt = FIX_AGENT_SYSTEM_PROMPT.replace(
            "__MAX_CHANGE_LINES__", str(self.max_change_lines)
        )
        # 目标文件硬约束：始终注入到系统提示词中（首次调用也生效）
        if target_constraint:
            prompt = target_constraint + "\n\n" + prompt
        # orchestrator 的覆盖（重试警告等）：最高优先级
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
        MAX_ERR_MSG = 200
        trimmed = []
        for e in error_locations:
            if isinstance(e, ErrorLocation):
                d = e.model_dump()
            else:
                d = dict(e)
            msg = d.get("error_message", "")
            # 截断 error_message
            if len(msg) > MAX_ERR_MSG:
                d["error_message"] = msg.split('\n')[0][:MAX_ERR_MSG] + "..."
            trimmed.append(d)
        return json.dumps(trimmed, ensure_ascii=False, indent=2)

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

    @staticmethod
    def _build_error_summary_header(error_locations: list) -> str:
        """构建错误摘要头，替代完整的 ci_logs 展示"""
        if not error_locations:
            return "本次修复基于以下错误信息"
        types = set()
        for err in error_locations:
            et = err.error_type if isinstance(err, ErrorLocation) else err.get("error_type", "")
            if et:
                types.add(et)
        type_str = "、".join(sorted(types)) if types else "未知类型"
        return f"本次修复基于以下错误信息（共 {len(error_locations)} 个错误，类型：{type_str}）"

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
        previous_code_changes: dict[str, str] | None = None,
        previous_fix_description: str = "",
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

            # 构建动态部分（标题+内容，空时不输出）
            review_parts = []
            if review_feedback:
                review_parts.append(review_feedback)
            if risk_warnings:
                review_parts.append("\n- ".join(risk_warnings))
            review_feedback_section = (
                "## 审查反馈\n" + "\n".join(review_parts)
                if review_parts else ""
            )

            test_parts = []
            if test_output:
                test_parts.append(f"```\n{test_output}\n```")
            if failed_tests:
                test_parts.append("- " + "\n- ".join(failed_tests))
            test_feedback_section = (
                "## 测试反馈\n" + "\n".join(test_parts)
                if test_parts else ""
            )

            # 重试时展示上一轮的代码变更，让 LLM 知道自己改了什么、为什么被拒绝
            previous_changes_section = ""
            if previous_code_changes and retry_count > 0:
                prev_parts = [f"**上一轮修复描述**：{previous_fix_description}"]
                for fp, code in previous_code_changes.items():
                    # 截断过长的代码，只展示前2000字符
                    display_code = code[:2000]
                    if len(code) > 2000:
                        display_code += "\n... (已截断)"
                    prev_parts.append(f"### {fp}\n```python\n{display_code}\n```")
                previous_changes_section = (
                    "## ⚠️ 上一轮修复代码（已被拒绝，不要原样重复）\n\n"
                    + "\n\n".join(prev_parts)
                    + "\n\n**请基于上述代码进行修正，不要原样重复。**"
                )

            # 历史修复摘要
            fix_history_summary = self._build_fix_history_summary(fix_history)
            fix_history_section = (
                "## 历史修复记录（避免重复同样的错误）\n" + fix_history_summary
                if fix_history_summary != "无历史记录" else ""
            )

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

            # 统一 error_locations 的 file_path 与 target_files 格式
            # target_files 来自 orchestrator 的路径映射（如 src/calculator.py），
            # error_locations 来自 parse_python_errors（如 calculator.py），
            # 需要对齐避免 LLM 返回不一致的路径
            if tf:
                import os
                basename_to_target = {os.path.basename(f): f for f in tf}
                for err in error_locations:
                    if isinstance(err, ErrorLocation):
                        fp = err.file_path
                    else:
                        fp = err.get("file_path", "")
                    if fp and fp in basename_to_target and fp not in tf:
                        mapped = basename_to_target[fp]
                        logger.info(f"路径对齐: error_locations file_path '{fp}' → '{mapped}'")
                        if isinstance(err, ErrorLocation):
                            err.file_path = mapped
                        else:
                            err["file_path"] = mapped

            # 序列化错误位置
            error_locations_json = self._serialize_error_locations(error_locations)
            logger.info(f"找到错误数量: {len(error_locations)}")
            logger.info(f"error_locations JSON 内容:\n{error_locations_json[:1000]}")
            logger.info(f"目标修复文件列表: {tf}")

            # 构建错误代码上下文区块
            # 小文件（<5000字符）直接全量注入，大文件只给错误行附近片段
            error_context_section = ""
            if original_codes and tf and error_locations:
                SMALL_FILE_THRESHOLD = 5000
                small_files = {fp: code for fp, code in original_codes.items() if len(code) < SMALL_FILE_THRESHOLD and fp in tf}
                large_files = [fp for fp in tf if fp not in small_files]

                parts = []
                if small_files:
                    full_code_parts = []
                    for fp, code in small_files.items():
                        full_code_parts.append(f"### {fp}（完整源码）\n```python\n{code}\n```")
                    parts.append(
                        "## 📂 小文件完整源码（可直接修改）\n\n"
                        + "\n\n".join(full_code_parts)
                    )
                    logger.info(f"全量注入 {len(small_files)} 个小文件: {list(small_files.keys())}")

                if large_files:
                    snippet_section = build_error_context_section(
                        original_codes=original_codes,
                        error_locations=error_locations,
                        target_files=large_files,
                        context_lines=8,
                    )
                    if snippet_section:
                        parts.append(snippet_section)

                error_context_section = "\n\n".join(parts) if parts else ""
                logger.info(
                    f"错误代码上下文区块构建完成: {len(error_context_section)} 字符"
                )
            elif original_codes:
                logger.warning("有 original_codes 但无目标文件或错误位置，跳过上下文构建")

            # 设置目标文件 ID 映射（read_target_file 工具使用）
            self._target_file_map = {i: fp for i, fp in enumerate(tf)} if tf else {}

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

            # === 构建目标文件硬约束（注入系统提示词，最高优先级之一） ===
            target_constraint = ""
            if tf:
                files_json = '", "'.join(tf)
                if len(tf) == 1:
                    target_constraint = (
                        f"## 🔴 目标文件锁定\n"
                        f"你只能修改 `{tf[0]}`。\n"
                        f"`code_changes` 的唯一 key 必须是 `\"{tf[0]}\"`，"
                        f"`modified_files` 只能是 `[\"{tf[0]}\"]`。\n"
                        f"返回任何其他文件 = 直接失败。"
                    )
                else:
                    target_constraint = (
                        f"## 🔴 目标文件锁定\n"
                        f"你只能修改以下文件：{', '.join(tf)}。\n"
                        f"`code_changes` 的 key 集合必须恰好是 `[\"{files_json}\"]`，"
                        f"一个不能多，一个不能少。\n"
                        f"返回任何其他文件 = 直接失败。"
                    )
                logger.info(f"构建目标文件约束: {target_constraint[:200]}...")

            # === 构建动态错误类型规则（只注入当前错误类型相关的规则） ===
            error_types = set()
            for err in error_locations:
                et = err.error_type if isinstance(err, ErrorLocation) else err.get("error_type", "")
                if et:
                    error_types.add(et)

            dynamic_error_rules = ""
            rules = []
            if error_types & {"ModuleNotFoundError", "ImportError"}:
                rules.append(
                    "- **导入错误**：只允许修改 import/from 行（增、删、try/except 包裹）。"
                    "禁止修改函数体、类定义等其他代码。"
                    "优先用 try/except ImportError 包裹，禁止 pip install。"
                )
            if error_types & {"SyntaxError", "IndentationError", "TabError"}:
                rules.append(
                    "- **语法错误**：只允许修改错误行及上下各3行。"
                )
            if error_types & {"NameError"}:
                rules.append(
                    "- **NameError**：只允许添加缺失的 import 或声明变量，修正拼写。"
                )
            if error_types & {"TypeError", "ValueError", "AttributeError", "KeyError", "IndexError"}:
                rules.append(
                    "- **运行时错误**（TypeError/ValueError/AttributeError/KeyError/IndexError）："
                    "只允许修改出错的函数/方法体内部，禁止改变函数签名。"
                )
            if rules:
                dynamic_error_rules = "## 错误类型修改边界\n" + "\n".join(rules)
                logger.info(f"注入动态错误规则，涉及类型: {error_types}")

            # 创建 Agent：run_limit = 目标文件数 * 2（允许重读，应对重试和复杂场景）
            agent = self._create_agent(
                run_limit=max(len(tf) * 2, 2),
                target_constraint=target_constraint,
            )

            # ci_logs 仅记录到 debug 日志，不注入 prompt
            logger.debug(f"ci_logs 长度: {len(ci_logs)}（不注入 prompt，仅 debug）")

            # 错误摘要头（替代 ci_logs 展示）
            error_summary_header = self._build_error_summary_header(error_locations)

            # 构建文件大小约束区块 — 让 LLM 知道原始文件行数，估算修改占比
            file_size_section = ""
            if original_codes and tf:
                size_lines = []
                for fp in tf:
                    if fp in original_codes:
                        line_count = len(original_codes[fp].splitlines())
                        size_lines.append(f"| {fp} | {line_count} 行 |")
                if size_lines:
                    file_size_section = (
                        "## 📏 目标文件原始大小\n\n"
                        "| 文件 | 原始行数 |\n|------|----------|\n"
                        + "\n".join(size_lines)
                        + f"\n\n**约束**：修复后每个文件的总行数不得超过原始行数的 120%，"
                        f"且总修改行数（新增+删除）不超过 {self.max_change_lines} 行。"
                    )

            user_input = FIX_AGENT_USER_PROMPT.format(
                target_constraint=target_constraint,
                dynamic_error_rules=dynamic_error_rules,
                mandatory_instructions=mandatory_instructions or "",
                file_size_section=file_size_section,
                error_summary_header=error_summary_header,
                error_locations=error_locations_json,
                root_cause_section=root_cause_section,
                target_file_list_section=target_file_list_section,
                error_context_section=error_context_section,
                review_feedback_section=review_feedback_section,
                test_feedback_section=test_feedback_section,
                previous_changes_section=previous_changes_section,
                fix_history_section=fix_history_section,
            )

            logger.info(f"用户提示词构建完成，长度: {len(user_input)}")
            config = {"recursion_limit": 50}
            result = await agent.ainvoke({"input": user_input}, config=config)
            logger.info("Agent调用完成")

            # 获取token用量（遍历所有AIMessage累加，agent循环中每次LLM调用都有独立的token_usage）
            try:
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

                # basename 重映射：LLM 可能返回 calculator.py / services/user_service.py 等非预期路径
                import os
                basename_to_expected = {os.path.basename(f): f for f in expected_files}
                remapped_code_changes = {}
                for path, content in fix_result["code_changes"].items():
                    bn = os.path.basename(path)
                    if path not in expected_files and bn in basename_to_expected:
                        mapped = basename_to_expected[bn]
                        logger.info(f"路径重映射: code_changes '{path}' → '{mapped}'")
                        remapped_code_changes[mapped] = content
                    else:
                        remapped_code_changes[path] = content
                fix_result["code_changes"] = remapped_code_changes

                # 同步修正 modified_files
                remapped_modified = []
                for f in fix_result.get("modified_files", []):
                    bn = os.path.basename(f)
                    if f not in expected_files and bn in basename_to_expected:
                        remapped_modified.append(basename_to_expected[bn])
                    else:
                        remapped_modified.append(f)
                fix_result["modified_files"] = remapped_modified

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

            # === 预发行数自纠正 ===
            # 在返回前检查 diff 行数，超限时注入裁剪指令并重试一次
            # 不消耗 orchestrator 的 retry_count，让 LLM 在 validation_gate 前自我修正
            if original_codes and fix_result.get("code_changes") and retry_count == 0:
                import difflib
                over_limit_files = []
                for fp, new_code in fix_result["code_changes"].items():
                    orig = original_codes.get(fp, "")
                    if not orig:
                        continue
                    diff = list(difflib.unified_diff(
                        orig.splitlines(keepends=True),
                        new_code.splitlines(keepends=True),
                        n=0,
                    ))
                    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
                    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
                    total = added + removed
                    if total > self.max_change_lines:
                        over_limit_files.append((fp, total, added, removed))

                if over_limit_files:
                    details = "; ".join(
                        f"{fp} 变动 {t} 行(+{a}/-{r})"
                        for fp, t, a, r in over_limit_files
                    )
                    logger.warning(f"预发行数检查超限: {details}，执行内部自纠正")

                    # 构建裁剪指令，注入到用户提示词末尾
                    cutting_instruction = (
                        "\n\n🚨🚨🚨 预发行数检查失败：你的修复超出了行数上限！🚨🚨🚨\n"
                        f"超限详情：{details}\n"
                        f"上限：{self.max_change_lines} 行\n\n"
                        "你必须立即裁剪：\n"
                        "1. 只保留对 CI 错误的直接修复\n"
                        "2. 删除所有额外改进（安全优化、代码风格、类重构等）\n"
                        "3. 将无关变更回退到原始代码\n"
                        f"4. 总修改行数必须不超过 {self.max_change_lines} 行\n"
                    )
                    corrected_user_input = user_input + cutting_instruction

                    # 重新调用 Agent（复用同一个 agent 实例）
                    corrected_result = await agent.ainvoke(
                        {"input": corrected_user_input}, config=config
                    )
                    corrected_content = corrected_result["messages"][-1].content
                    logger.info(f"自纠正响应长度: {len(corrected_content)}")

                    # 解析自纠正响应
                    json_match2 = _re.search(
                        r"```json\s*(.*?)\s*```", corrected_content, _re.DOTALL
                    )
                    if json_match2:
                        json_content2 = json_match2.group(1)
                    else:
                        json_match2 = _re.search(r"\{.*\}", corrected_content, _re.DOTALL)
                        json_content2 = json_match2.group(0) if json_match2 else corrected_content

                    fix_result2 = self._parse_json_safely(json_content2)
                    if fix_result2 and fix_result2.get("code_changes"):
                        # 合并 token 用量
                        try:
                            for msg in corrected_result.get("messages", []):
                                if isinstance(msg, _AIM) and hasattr(msg, "response_metadata") and msg.response_metadata:
                                    usage = msg.response_metadata.get("token_usage", {})
                                    token_usage += usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
                        except Exception:
                            pass
                        # 路径规范化
                        fix_result2["modified_files"] = [
                            normalize_path(f) for f in fix_result2.get("modified_files", [])
                        ]
                        normalized2 = {}
                        for path, content in fix_result2["code_changes"].items():
                            normalized2[normalize_path(path)] = content
                        fix_result2["code_changes"] = normalized2

                        logger.info("自纠正成功，使用修正后的修复结果")
                        fix_result2["token_usage"] = token_usage
                        return fix_result2
                    else:
                        logger.warning("自纠正未能生成有效修复，回退到原始结果")

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
