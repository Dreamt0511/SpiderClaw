"""审查Agent实现 - LangChain标准版本"""

from typing import Dict, Any, List
import ast
import logging
import difflib
import re
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from src.agent.prompts.review_agent_prompts import REVIEW_AGENT_SYSTEM_PROMPT, REVIEW_AGENT_USER_PROMPT
from src.agent.tools import set_tool_context, read_file

logger = logging.getLogger(__name__)


class ReviewAgent:
    """审查Agent，使用LangChain标准工具调用模式"""

    def __init__(
        self,
        llm_model: str = "gpt-4o",
        temperature: float = 0.0,
        openai_api_key: str = None,
        openai_base_url: str = "https://api.openai.com/v1",
        max_change_lines: int = 20,
    ):
        """
        初始化审查Agent

        Args:
            llm_model: LLM模型名称
            temperature: 温度参数，审查需要严格，所以设为0
            openai_api_key: OpenAI API密钥
            openai_base_url: OpenAI API基础URL
            max_change_lines: 最大允许变更行数
        """
        self.max_change_lines = max_change_lines

        # 初始化LLM
        self.llm = ChatOpenAI(
            model=llm_model,
            temperature=temperature,
            api_key=openai_api_key,
            base_url=openai_base_url,
        )
        self.system_prompt = REVIEW_AGENT_SYSTEM_PROMPT

    # ===== 风险分级模式库 =====
    # CRITICAL（致命）：立即终止修复流程，绝不创建PR
    _CRITICAL_PATTERNS = [
        r"\beval\s*\(",                       # 动态执行任意代码
        r"\bexec\s*\(",                       # 执行任意代码
        r"\bcompile\s*\(",                    # 编译任意代码
        r"\b__import__\s*\(",                 # 动态导入
        r"\bos\.system\s*\(",                 # 系统命令执行
        r"\bos\.popen\s*\(",                  # 管道命令执行
        r"\bsubprocess\.call\s*\(\s*shell\s*=\s*True",  # shell执行
        r"\bsubprocess\.Popen\s*\(\s*shell\s*=\s*True", # shell执行
        r"rm\s+-rf\s+/",                      # 递归删除根目录
        r"shutil\.rmtree\s*\(",               # 删除目录树
        r"__import__\s*\(\s*os\s*\)\.system",  # 混淆导入
    ]

  # HIGH（高危）：强制重试，重试用尽后创建带"禁止合并"标签的PR
    _HIGH_PATTERNS = [
        r"\bos\.remove\s*\(",                        # 删除文件
        r"\bos\.unlink\s*\(",                        # 删除链接
        r"\bos\.rmdir\s*\(",                         # 删除目录
        r"\bopen\s*\([^)]*['\"]w['\"]",             # 写入模式打开文件（可能覆盖用户数据）
        # 硬编码密钥/凭证
        r"\b(?:api_key|secret_key|private_key)\s*=\s*['\"]",  # API/私钥硬编码
        r"\b(?:password|passwd|pwd)\s*=\s*['\"]",    # 密码硬编码
        r"\b(?:token|access_token|auth_token)\s*=\s*['\"]",  # Token硬编码
        # SQL注入：f-string拼接SQL查询
        r"(?:execute|executemany|raw_sql|raw_input)\s*\(\s*(?:f['\"]|['\"]f\s*)",  # f-string SQL拼接
        r"cursor\.execute\s*\(\s*f['\"]",           # cursor.execute f-string
        r"\.execute\s*\(\s*(?:f['\"]|['\"].*\{\s*\w+\s*\}.*['\"])",  # SQL字符串拼接注入
        # 外部HTTP请求（SSRF / 数据泄露风险）
        r"\b(?:requests|httpx|urllib\.request)\.(?:get|post|put|delete|patch)\s*\(",  # HTTP请求（SSRF风险）
        r"\bhttp\.client\.(?:HTTPConnection|HTTPSConnection)\s*\(",  # 底层HTTP连接
        # 逻辑破坏：集合/列表/字典整体置空
        r"\w+\s*=\s*None\s*$",                      # 变量整体置为None（结合diff上下文判断）
    ]

    # MEDIUM（中危）：仅作审查意见记录，不阻止流程
    _MEDIUM_PATTERNS = [
        r"\bpickle\.load",                    # 不安全的反序列化
        r"\byaml\.load\s*\(",                 # 不安全的YAML加载（非SafeLoader）
        r"except\s*:",                        # 裸except（可能隐藏错误）
        r"except\s+Exception\s*:",            # 过于宽泛的异常捕获
    ]

    # INFO（低风险信息）：不影响流程，视为安全改进（如 subprocess.run 替换 os.system）
    _INFO_PATTERNS = [
        r"\bsubprocess\.(?:run|call|Popen)\s*\(",   # 子进程调用（将 os.system 替换为 subprocess.run 视为改进）
        r"\bast\.literal_eval\s*\(",                # ast.literal_eval（比 eval 安全）
    ]

    # LOW（低风险）：仅记录日志，不影响流程
    _LOW_PATTERNS = [
        r"#\s*TODO",                          # TODO注释
        r"#\s*FIXME",                         # FIXME注释
        r"#\s*HACK",                          # HACK注释
    ]

    def _strip_line_comment(self, line: str) -> str:
        """去除单行中的 Python 注释部分（保留行内 # 前的代码）"""
        in_string = False
        string_char = None
        for i, c in enumerate(line):
            if c in ('"', "'") and (i == 0 or line[i-1] != '\\'):
                in_string = not in_string
                string_char = c
            elif c == '#' and not in_string:
                return line[:i]
        return line

    def _line_has_pattern(self, line: str, pattern: str) -> bool:
        """检查一行中是否包含特定模式（排除注释后的代码部分）"""
        code_part = self._strip_line_comment(line)
        return bool(re.search(pattern, code_part))

    def _scan_patterns_in_content(
        self, file_path: str, content: str, patterns: list, label: str
    ) -> list:
        """在文件内容中扫描模式，返回匹配结果列表"""
        results = []
        for line_num, line in enumerate(content.split('\n'), 1):
            for pattern in patterns:
                if self._line_has_pattern(line, pattern):
                    results.append(
                        f"[{label}] 文件 {file_path}:{line_num} "
                        f"包含敏感操作: {pattern}"
                    )
        return results

    def _static_security_check(
        self, code_changes: Dict[str, str], diff_content: str,
        original_codes: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """
        对比式静态安全检查：扫描原始代码和修复后代码，判断风险变化。

        判定规则：
        - 原始有风险 → 修复已移除 → ✅ 好，不警告
        - 原始有风险 → 修复还在   → ⚠️ 警告，不阻断
        - 原始无风险 → 修复新增   → ❌ 按等级阻断

        Args:
            code_changes: 修复后代码字典
            diff_content: diff内容
            original_codes: 原始代码字典（用于对比）

        Returns:
            Dict: 检查结果，包含 risk_level 和 risk_comparison 字段
        """
        change_lines = 0
        for line in diff_content.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                change_lines += 1
            elif line.startswith("-") and not line.startswith("---"):
                change_lines += 1

        # 分级扫描：分别扫描原始代码和修复后代码
        def _scan_all_levels(file_path: str, content: str) -> Dict[str, list]:
            return {
                "critical": self._scan_patterns_in_content(file_path, content, self._CRITICAL_PATTERNS, "CRITICAL"),
                "high": self._scan_patterns_in_content(file_path, content, self._HIGH_PATTERNS, "HIGH"),
                "medium": self._scan_patterns_in_content(file_path, content, self._MEDIUM_PATTERNS, "MEDIUM"),
                "info": (self._scan_patterns_in_content(file_path, content, self._INFO_PATTERNS, "INFO")
                         + self._scan_patterns_in_content(file_path, content, self._LOW_PATTERNS, "INFO")),
            }

        LEVELS = ("critical", "high", "medium", "info")

        # 扫描修复后代码
        fixed_risks: Dict[str, list] = {k: [] for k in LEVELS}
        for file_path, content in code_changes.items():
            file_risks = _scan_all_levels(file_path, content)
            for level in LEVELS:
                fixed_risks[level].extend(file_risks[level])
            if not file_path.endswith(".py"):
                fixed_risks["medium"].append(f"[MEDIUM] 修改了非Python文件: {file_path}")

        # 扫描原始代码（如果有），计算风险变化
        orig_risks: Dict[str, list] = {k: [] for k in LEVELS}
        if original_codes:
            for file_path in code_changes:
                orig_content = original_codes.get(file_path, "")
                if orig_content:
                    file_risks = _scan_all_levels(file_path, orig_content)
                    for level in LEVELS:
                        orig_risks[level].extend(file_risks[level])

        # 判断新引入/残留/已移除的风险
        new_risks: Dict[str, list] = {k: [] for k in LEVELS}
        kept_risks: Dict[str, list] = {k: [] for k in LEVELS}
        removed_risks: Dict[str, list] = {k: [] for k in LEVELS}

        for level in ["critical", "high", "medium", "info"]:
            if not original_codes:
                # 无原始代码时，全部视为新引入（保守策略）
                new_risks[level] = fixed_risks[level]
            else:
                # 去重函数：提取风险描述中的模式名用于匹配
                def _risk_pattern(r: str) -> str:
                    m = re.search(r'包含敏感操作:\s*(.+)', r)
                    return m.group(1) if m else r

                orig_patterns = {_risk_pattern(r) for r in orig_risks[level]}
                fixed_patterns = {_risk_pattern(r) for r in fixed_risks[level]}

                for r in fixed_risks[level]:
                    pat = _risk_pattern(r)
                    if pat in orig_patterns:
                        kept_risks[level].append(r)
                    else:
                        new_risks[level].append(r)

                for r in orig_risks[level]:
                    pat = _risk_pattern(r)
                    if pat not in fixed_patterns:
                        removed_risks[level].append(r)

        # 契约变更检测：原始代码中的高风险
        contract_warnings = []
        if original_codes:
            contract_warnings = self._detect_contract_changes(code_changes, original_codes)
        # 契约变更视为新引入的高风险
        new_risks["high"].extend(contract_warnings)

        # 变更行数警告
        if change_lines > self.max_change_lines:
            new_risks["info"].append(
                f"[LOW] 变更行数超过建议值: {change_lines} 行，"
                f"建议不超过 {self.max_change_lines} 行"
            )

        # 确定风险等级：只关心会阻断的级别（CRITICAL/HIGH/MEDIUM），INFO 不阻断
        if new_risks["critical"]:
            risk_level = "CRITICAL"
        elif new_risks["high"]:
            risk_level = "HIGH"
        elif new_risks["medium"]:
            risk_level = "MEDIUM"
        elif kept_risks["critical"] or kept_risks["high"]:
            # 只有残留风险 → 降级为 MEDIUM（警告不阻断）
            risk_level = "MEDIUM"
        else:
            risk_level = "NONE"

        # 合并保留的和新引入的为 risk_warnings（新引入在前，强调严重性）
        all_warnings = []
        for level in ["critical", "high", "medium", "info"]:
            all_warnings.extend(new_risks[level])
            all_warnings.extend(kept_risks[level])

        return {
            "change_lines": change_lines,
            "risk_warnings": all_warnings,
            "risk_level": risk_level,
            "new_risks": new_risks,
            "kept_risks": kept_risks,
            "removed_risks": removed_risks,
            "has_critical_risks": len(new_risks["critical"]) > 0,
            "has_high_risks": len(new_risks["high"]) > 0,
        }

    def _detect_contract_changes(
        self, code_changes: Dict[str, str], original_codes: Dict[str, str]
    ) -> list:
        """检测修复是否改变了原函数的契约（签名、返回类型、副作用）"""
        warnings = []
        if not original_codes:
            return warnings

        for file_path, fixed_code in code_changes.items():
            original = original_codes.get(file_path, "")
            if not original:
                continue

            try:
                orig_tree = ast.parse(original)
                fixed_tree = ast.parse(fixed_code)
            except SyntaxError:
                continue

            # 提取函数定义：名称 + 参数列表
            def _extract_func_signatures(tree):
                sigs = {}
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        arg_names = [a.arg for a in node.args.args]
                        defaults_count = len(node.args.defaults)
                        sigs[node.name] = {
                            "args": arg_names,
                            "defaults_count": defaults_count,
                            "lineno": node.lineno,
                        }
                return sigs

            orig_sigs = _extract_func_signatures(orig_tree)
            fixed_sigs = _extract_func_signatures(fixed_tree)

            # 检查共享函数的签名是否被修改
            for func_name in orig_sigs:
                if func_name not in fixed_sigs:
                    continue
                orig_sig = orig_sigs[func_name]
                fixed_sig = fixed_sigs[func_name]

                # 参数列表改变 → 契约破坏
                if orig_sig["args"] != fixed_sig["args"]:
                    warnings.append(
                        f"[HIGH] 函数契约变更: {file_path}:{orig_sig['lineno']} "
                        f"函数 `{func_name}` 参数列表被修改 "
                        f"(原始: {orig_sig['args']}, 修复后: {fixed_sig['args']})"
                    )
                # 默认参数数量改变 → 契约破坏
                elif orig_sig["defaults_count"] != fixed_sig["defaults_count"]:
                    warnings.append(
                        f"[HIGH] 函数契约变更: {file_path}:{orig_sig['lineno']} "
                        f"函数 `{func_name}` 默认参数数量被修改 "
                        f"(原始: {orig_sig['defaults_count']}, "
                        f"修复后: {fixed_sig['defaults_count']})"
                    )
        return warnings

    def _auto_compare_codes(
        self, original_codes: Dict[str, str], code_changes: Dict[str, str], modified_files: List[str]
    ) -> Dict[str, Any]:
        """
        自动对比原始代码和修复后代码，独立于LLM判断

        Returns:
            Dict: 包含每文件的对比结果和总体判断
        """

        all_identical = True
        changed_files_count = 0
        comparison_details = []

        for file_path in modified_files:
            original = original_codes.get(file_path, "")
            fixed = code_changes.get(file_path, "")

            if original == fixed:
                comparison_details.append({
                    "file_path": file_path,
                    "changed": False,
                    "reason": "代码完全相同"
                })
            else:
                all_identical = False
                changed_files_count += 1

                # 计算差异行数
                diff_lines = list(difflib.unified_diff(
                    original.splitlines(), fixed.splitlines(),
                    n=0
                ))
                added = sum(1 for l in diff_lines if l.startswith('+') and not l.startswith('+++'))
                removed = sum(1 for l in diff_lines if l.startswith('-') and not l.startswith('---'))
                comparison_details.append({
                    "file_path": file_path,
                    "changed": True,
                    "added_lines": added,
                    "removed_lines": removed,
                })

        return {
            "all_identical": all_identical,
            "changed_files_count": changed_files_count,
            "details": comparison_details
        }

    async def review_changes(
        self,
        error_locations: List[Dict],
        fix_description: str,
        modified_files: List[str],
        code_changes: Dict[str, str],
        diff_content: str,
        repo_path: str,
        original_codes: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """
        审查代码变更

        Args:
            error_locations: 原始错误位置列表
            fix_description: 修复描述
            modified_files: 修改的文件列表
            code_changes: 代码变更字典
            diff_content: diff内容
            repo_path: 仓库路径

        Returns:
            Dict: 审查结果
        """
        try:
            logger.info("运行审查Agent")

            # 先执行静态检查（传入原始代码用于契约变更检测）
            static_result = self._static_security_check(
                code_changes, diff_content, original_codes=original_codes
            )

            # CRITICAL → 立即终止，绝不创建PR
            if static_result["risk_level"] == "CRITICAL":
                logger.error(f"发现致命安全风险，终止流程: {static_result['new_risks']['critical']}")
                return {
                    "review_passed": False,
                    "review_comments": "发现致命安全风险，修复流程终止: "
                    + "; ".join(static_result["new_risks"]["critical"]),
                    "change_lines": static_result["change_lines"],
                    "risk_warnings": static_result["risk_warnings"],
                    "has_critical_risks": True,
                    "has_high_risks": False,
                    "risk_level": "CRITICAL",
                }

            # HIGH → 拦截但不终止，触发重试
            if static_result["has_high_risks"]:
                logger.warning(f"发现高危风险: {static_result['new_risks']['high']}")
                return {
                    "review_passed": False,
                    "review_comments": "发现高危风险: "
                    + "; ".join(static_result["new_risks"]["high"]),
                    "change_lines": static_result["change_lines"],
                    "risk_warnings": static_result["risk_warnings"],
                    "has_critical_risks": False,
                    "has_high_risks": True,
                    "risk_level": "HIGH",
                }

            # MEDIUM/LOW → 记录但继续LLM审查
            if static_result["risk_warnings"]:
                logger.info(
                    f"发现风险警告（不阻止流程）: {len(static_result['risk_warnings'])} 条"
                )

            # 静态检查对比摘要
            removed_count = sum(len(v) for v in static_result.get("removed_risks", {}).values())
            kept_count = sum(len(v) for v in static_result.get("kept_risks", {}).values())
            new_count = sum(len(v) for v in static_result.get("new_risks", {}).values())
            if removed_count > 0:
                logger.info(f"安全改进: 移除了 {removed_count} 个风险点")
            if kept_count > 0:
                logger.info(f"残留风险: {kept_count} 个风险点（原始代码已有，不阻断）")
            if new_count > 0:
                logger.info(f"新风险: {new_count} 个风险点（将按等级处理）")

            # 设置工具上下文
            set_tool_context({"repo_path": repo_path})

            # 1. 格式化原始错误信息
            error_info = []
            for error in error_locations:
                if error.get("file_path") and error.get("line_number"):
                    error_info.append(
                        f"{error['file_path']}:{error['line_number']} {error['error_type']}: {error['error_message']}"
                    )
                else:
                    error_info.append(f"{error['error_type']}: {error['error_message']}")
            error_info_str = "\n".join(error_info) if error_info else "无明确错误位置"

            # 2. 使用传入的原始代码，避免读取到已修改的文件
            if original_codes is None:
                original_codes = {}
                # 如果没有传入原始代码，再尝试读取（兼容旧调用方式）
                for file_path in modified_files:
                    try:
                        original_content = read_file.invoke({"file_path": file_path})
                        if not original_content.startswith("Error:"):
                            original_codes[file_path] = original_content
                        else:
                            original_codes[file_path] = f"无法读取原始文件: {original_content}"
                    except Exception as e:
                        original_codes[file_path] = f"读取原始文件失败: {str(e)}"

            # 3. 自动化代码对比（独立于LLM，确保基本正确性）
            auto_result = self._auto_compare_codes(original_codes, code_changes, modified_files)
            logger.info(f"自动代码对比结果: 完全相同={auto_result['all_identical']}, "
                        f"已修改文件数={auto_result['changed_files_count']}/{len(modified_files)}")
            for detail in auto_result["details"]:
                if detail["changed"]:
                    logger.info(f"  ✓ {detail['file_path']}: 修改了 {detail.get('added_lines',0)} 行增加, "
                                f"{detail.get('removed_lines',0)} 行删除")
                else:
                    logger.warning(f"  ✗ {detail['file_path']}: 未修改 (代码完全相同)")

            # 如果代码完全相同，自动拒绝，不需要调用LLM
            if auto_result["all_identical"]:
                logger.warning("自动对比发现所有文件代码完全相同，拒绝通过审查")
                return {
                    "review_passed": False,
                    "review_comments": "修复后的代码与原始代码完全相同，没有做任何有效修改。请实际修改代码中的错误行。",
                    "change_lines": static_result["change_lines"],
                    "risk_warnings": static_result["risk_warnings"] + ["所有文件代码完全相同，未做任何有效修改"],
                }

            # 4. 构建代码对比部分
            code_comparison_sections = []
            for file_path in modified_files:
                code_comparison_sections.append(f"\n## 原始代码 - {file_path}")
                code_comparison_sections.append("```python")
                code_comparison_sections.append(original_codes.get(file_path, "无原始代码"))
                code_comparison_sections.append("```")

                code_comparison_sections.append(f"\n## 修复后代码 - {file_path}")
                code_comparison_sections.append("```python")
                code_comparison_sections.append(code_changes.get(file_path, "无修复代码"))
                code_comparison_sections.append("```")

                # 添加差异摘要
                if not auto_result["all_identical"]:
                    for detail in auto_result["details"]:
                        if detail["file_path"] == file_path and detail["changed"]:
                            code_comparison_sections.append(
                                f"\n> 差异：{detail.get('added_lines', 0)} 行增加, {detail.get('removed_lines', 0)} 行删除"
                            )
            code_comparison_section = "\n".join(code_comparison_sections)

            # 构建静态警告部分
            if static_result["risk_warnings"]:
                # 构建对比摘要
                removed = static_result.get("removed_risks", {})
                kept = static_result.get("kept_risks", {})
                new_r = static_result.get("new_risks", {})
                removed_all = sum(len(v) for v in removed.values())
                kept_all = sum(len(v) for v in kept.values())
                new_all = sum(len(v) for v in new_r.values())

                summary_parts = []
                if removed_all > 0:
                    summary_parts.append(f"\n✅ 已移除的风险点 ({removed_all} 个)：")
                    for level in ["critical", "high", "medium", "info"]:
                        for r in removed[level]:
                            summary_parts.append(f"  - {r}")
                if kept_all > 0:
                    summary_parts.append(f"\n⚠️ 残留的原始风险 ({kept_all} 个，不阻断)：")
                    for level in ["critical", "high", "medium", "info"]:
                        for r in kept[level]:
                            summary_parts.append(f"  - {r}")
                if new_all > 0:
                    summary_parts.append(f"\n❌ 新引入的风险 ({new_all} 个)：")
                    for level in ["critical", "high", "medium", "info"]:
                        for r in new_r[level]:
                            summary_parts.append(f"  - {r}")

                risk_comparison = "\n".join(summary_parts)

                static_warnings_section = f"""
## 静态检查：风险变化对比
{risk_comparison}

注意：已移除的风险视为安全改进；残留的原始风险不影响通过；新引入的风险将根据等级决定是否阻止。
"""
            else:
                static_warnings_section = ""

            # 使用模板构建用户输入
            user_input = REVIEW_AGENT_USER_PROMPT.format(
                error_info_str=error_info_str,
                code_comparison_section=code_comparison_section,
                fix_description=fix_description,
                static_warnings_section=static_warnings_section
            )

            # 诊断日志：记录发送给LLM的完整对比内容
            logger.info(f"发送给审查LLM的用户输入前500字符:\n{user_input[:500]}...")
            logger.info(f"对比部分前1000字符:\n{code_comparison_section[:1000]}...")

            # 直接调用LLM，不使用Agent框架，避免工具调用
            messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=user_input)
            ]
            result = await self.llm.ainvoke(messages)

            # 解析结果
            response_content = result.content
            logger.info(f"审查Agent原始响应: {response_content[:500]}")

            # 尝试提取JSON
            import json
            import re

            json_match = re.search(
                r"```json\s*(.*?)\s*```", response_content, re.DOTALL
            )
            if json_match:
                json_content = json_match.group(1)
            else:
                json_content = response_content.strip()

            review_result = json.loads(json_content)

            # 合并静态检查结果
            review_result["change_lines"] = static_result["change_lines"]
            if "risk_warnings" not in review_result:
                review_result["risk_warnings"] = []
            review_result["risk_warnings"].extend(static_result["risk_warnings"])

            # 最终review_passed由LLM判断，静态警告不强制拦截
            llm_passed = bool(review_result.get("review_passed", False))

            # 关键逻辑：如果LLM说未通过但自动对比确认代码已修改，记录警告但仍然信任LLM
            if not llm_passed and not auto_result["all_identical"]:
                logger.warning(
                    f"LLM审查未通过，但自动对比确认代码已修改 "
                    f"({auto_result['changed_files_count']}/{len(modified_files)} 文件已修改)"
                )

            review_result["review_passed"] = llm_passed
            # 保留静态检查确定的风险等级
            review_result["risk_level"] = static_result["risk_level"]
            review_result["has_critical_risks"] = static_result["has_critical_risks"]
            review_result["has_high_risks"] = static_result["has_high_risks"]
            logger.info(f"审查完成. 通过: {review_result['review_passed']}, 风险等级: {static_result['risk_level']}")
            return review_result

        except Exception as e:
            logger.error(f"审查Agent执行失败: {e}", exc_info=True)
            return {
                "review_passed": False,
                "review_comments": f"审查过程出错: {str(e)}",
                "change_lines": 0,
                "risk_warnings": [str(e)],
                "has_critical_risks": False,
                "has_high_risks": False,
                "risk_level": "NONE",
            }
