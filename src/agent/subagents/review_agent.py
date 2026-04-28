"""审查Agent实现 - LangChain标准版本"""

from typing import Dict, Any, List
import ast
import logging
import difflib
import re
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from src.agent.prompts.review_agent_prompts import REVIEW_AGENT_SYSTEM_PROMPT, REVIEW_AGENT_USER_PROMPT
from src.agent.security_rules import SecurityRule, CRITICAL_RULES, HIGH_RULES, MEDIUM_RULES, LOW_RULES
from src.agent.state import ErrorLocation
from src.agent.tools import set_tool_context

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
        self.max_change_lines = max_change_lines

        self.llm = ChatOpenAI(
            model=llm_model,
            temperature=temperature,
            api_key=openai_api_key,
            base_url=openai_base_url,
        )
        self.system_prompt = REVIEW_AGENT_SYSTEM_PROMPT

    # 安全规则来源统一为 security_rules.py
    _CRITICAL_RULES = CRITICAL_RULES
    _HIGH_RULES = HIGH_RULES
    _MEDIUM_RULES = MEDIUM_RULES
    _LOW_RULES = LOW_RULES

    def _strip_line_comment(self, line: str) -> str:
        """去除单行中的 Python 注释部分"""
        in_string = False
        for i, c in enumerate(line):
            if c in ('"', "'") and (i == 0 or line[i-1] != '\\'):
                in_string = not in_string
            elif c == '#' and not in_string:
                return line[:i]
        return line

    def _line_has_pattern(self, line: str, pattern: str) -> bool:
        """检查一行中是否包含特定模式（排除注释后的代码部分）"""
        code_part = self._strip_line_comment(line)
        return bool(re.search(pattern, code_part))

    def _scan_rules_in_content(
        self, file_path: str, content: str, rules: list[SecurityRule]
    ) -> list:
        """在文件内容中扫描 SecurityRule 列表，返回匹配结果"""
        results = []
        for line_num, line in enumerate(content.split('\n'), 1):
            for rule in rules:
                if self._line_has_pattern(line, rule.pattern):
                    results.append(
                        f"[{rule.severity}] 文件 {file_path}:{line_num} "
                        f"包含敏感操作: {rule.pattern}"
                    )
        return results

    def _static_security_check(
        self, code_changes: Dict[str, str], diff_content: str,
        original_codes: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """对比式静态安全检查：扫描原始代码和修复后代码，判断风险变化。"""
        change_lines = 0
        for line in diff_content.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                change_lines += 1
            elif line.startswith("-") and not line.startswith("---"):
                change_lines += 1

        def _scan_all_levels(file_path: str, content: str) -> Dict[str, list]:
            return {
                "critical": self._scan_rules_in_content(file_path, content, self._CRITICAL_RULES),
                "high": self._scan_rules_in_content(file_path, content, self._HIGH_RULES),
                "medium": self._scan_rules_in_content(file_path, content, self._MEDIUM_RULES),
                "low": (self._scan_rules_in_content(file_path, content, self._LOW_RULES)),
            }

        LEVELS = ("critical", "high", "medium", "low")

        fixed_risks: Dict[str, list] = {k: [] for k in LEVELS}
        for file_path, content in code_changes.items():
            file_risks = _scan_all_levels(file_path, content)
            for level in LEVELS:
                fixed_risks[level].extend(file_risks[level])
            if not file_path.endswith(".py"):
                fixed_risks["medium"].append(f"[MEDIUM] 修改了非Python文件: {file_path}")

        orig_risks: Dict[str, list] = {k: [] for k in LEVELS}
        if original_codes:
            for file_path in code_changes:
                orig_content = original_codes.get(file_path, "")
                if orig_content:
                    file_risks = _scan_all_levels(file_path, orig_content)
                    for level in LEVELS:
                        orig_risks[level].extend(file_risks[level])

        new_risks: Dict[str, list] = {k: [] for k in LEVELS}
        kept_risks: Dict[str, list] = {k: [] for k in LEVELS}
        removed_risks: Dict[str, list] = {k: [] for k in LEVELS}

        if not original_codes:
            logger.warning("original_codes 为空，所有风险视为新引入")
            new_risks = {k: v.copy() for k, v in fixed_risks.items()}
        else:
            for level in ["critical", "high", "medium", "low"]:
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

        contract_warnings = []
        if original_codes:
            contract_warnings = self._detect_contract_changes(code_changes, original_codes)
        new_risks["high"].extend(contract_warnings)

        # 代码退化检测：检查新引入的危险模式（原始代码中没有，修复后新增的）
        _DEGRADED_PATTERNS = [
            (r'\beval\s*\(', "eval() 是危险的代码执行函数"),
            (r'\bexec\s*\(', "exec() 是危险的代码执行函数"),
            (r'\bos\.system\s*\(', "os.system() 可被命令注入攻击"),
            (r'return\s+password\b(?!.*hash)', "密码以明文形式返回"),
            (r'f".*SELECT.*WHERE.*\{', "SQL 查询使用 f-string，存在注入风险"),
        ]
        if original_codes:
            for file_path, new_code in code_changes.items():
                orig_code = original_codes.get(file_path, "")
                for pattern, description in _DEGRADED_PATTERNS:
                    if re.search(pattern, new_code) and not re.search(pattern, orig_code):
                        new_risks["high"].append(
                            f"[HIGH] 新引入安全风险 ({file_path}): {description}"
                        )

        if change_lines > self.max_change_lines:
            new_risks["low"].append(
                f"[LOW] 变更行数超过建议值: {change_lines} 行，建议不超过 {self.max_change_lines} 行"
            )

        if new_risks["critical"]:
            risk_level = "CRITICAL"
        elif new_risks["high"]:
            risk_level = "HIGH"
        elif new_risks["medium"]:
            risk_level = "MEDIUM"
        elif kept_risks["critical"] or kept_risks["high"]:
            risk_level = "MEDIUM"
        else:
            risk_level = "NONE"

        all_warnings = []
        for level in ["critical", "high", "medium", "low"]:
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

            for func_name in orig_sigs:
                if func_name not in fixed_sigs:
                    continue
                orig_sig = orig_sigs[func_name]
                fixed_sig = fixed_sigs[func_name]

                if orig_sig["args"] != fixed_sig["args"]:
                    warnings.append(
                        f"[HIGH] 函数契约变更: {file_path}:{orig_sig['lineno']} "
                        f"函数 `{func_name}` 参数列表被修改 "
                        f"(原始: {orig_sig['args']}, 修复后: {fixed_sig['args']})"
                    )
                elif orig_sig["defaults_count"] != fixed_sig["defaults_count"]:
                    warnings.append(
                        f"[HIGH] 函数契约变更: {file_path}:{orig_sig['lineno']} "
                        f"函数 `{func_name}` 默认参数数量被修改 "
                        f"(原始: {orig_sig['defaults_count']}, "
                        f"修复后: {fixed_sig['defaults_count']})"
                    )
        return warnings

    def _auto_compare_codes(
        self, original_codes: Dict[str, str], code_changes: Dict[str, str],
        modified_files: List[str]
    ) -> Dict[str, Any]:
        """自动对比原始代码和修复后代码"""
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

                diff_lines = list(difflib.unified_diff(
                    original.splitlines(), fixed.splitlines(), n=0
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
        error_locations: List[ErrorLocation],
        fix_description: str,
        modified_files: List[str],
        code_changes: Dict[str, str],
        diff_content: str,
        repo_path: str,
        original_codes: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """审查代码变更"""
        try:
            logger.info("运行审查Agent")

            # 静态安全检查
            static_result = self._static_security_check(
                code_changes, diff_content, original_codes=original_codes
            )

            # CRITICAL → 立即终止
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
                    "rejection_reason": "",
                }

            # HIGH → 拦截但不终止
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
                    "rejection_reason": "",
                }

            if static_result["risk_warnings"]:
                logger.info(f"发现风险警告（不阻止流程）: {len(static_result['risk_warnings'])} 条")

            # 风险对比日志
            removed_count = sum(len(v) for v in static_result.get("removed_risks", {}).values())
            kept_count = sum(len(v) for v in static_result.get("kept_risks", {}).values())
            new_count = sum(len(v) for v in static_result.get("new_risks", {}).values())
            if removed_count > 0:
                logger.info(f"安全改进: 移除了 {removed_count} 个风险点")
            if kept_count > 0:
                logger.info(f"残留风险: {kept_count} 个风险点（原始代码已有，不阻断）")
            if new_count > 0:
                logger.info(f"新风险: {new_count} 个风险点")

            set_tool_context({"repo_path": repo_path})

            # 格式化错误信息
            error_info = []
            for error in error_locations:
                if error.file_path and error.line_number:
                    error_info.append(
                        f"{error.file_path}:{error.line_number} "
                        f"{error.error_type}: {error.error_message}"
                    )
                else:
                    error_info.append(f"{error.error_type}: {error.error_message}")
            error_info_str = "\n".join(error_info) if error_info else "无明确错误位置"

            # original_codes 必须由调用方传入，不存在时直接报错（不再回退到磁盘）
            if original_codes is None:
                logger.warning("original_codes 未传入，使用空字典")
                original_codes = {}

            # 自动化代码对比
            auto_result = self._auto_compare_codes(original_codes, code_changes, modified_files)
            logger.info(f"自动代码对比: 完全相同={auto_result['all_identical']}, "
                        f"已修改={auto_result['changed_files_count']}/{len(modified_files)}")

            for detail in auto_result["details"]:
                if detail["changed"]:
                    logger.info(f"  ✓ {detail['file_path']}: +{detail.get('added_lines',0)}"
                                f"/-{detail.get('removed_lines',0)}")
                else:
                    logger.warning(f"  ✗ {detail['file_path']}: 未修改")

            if auto_result["all_identical"]:
                logger.warning("自动对比发现所有文件代码完全相同，拒绝通过审查")
                return {
                    "review_passed": False,
                    "review_comments": "修复后的代码与原始代码完全相同，没有做任何有效修改。",
                    "change_lines": static_result["change_lines"],
                    "risk_warnings": static_result["risk_warnings"] + ["所有文件代码完全相同，未做任何有效修改"],
                    "rejection_reason": "",
                }

            # 构建代码对比
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
                if not auto_result["all_identical"]:
                    for detail in auto_result["details"]:
                        if detail["file_path"] == file_path and detail["changed"]:
                            code_comparison_sections.append(
                                f"\n> 差异：{detail.get('added_lines', 0)} 行增加, "
                                f"{detail.get('removed_lines', 0)} 行删除"
                            )
            code_comparison_section = "\n".join(code_comparison_sections)

            # 构建静态警告
            static_warnings_section = ""
            if static_result["risk_warnings"]:
                removed = static_result.get("removed_risks", {})
                kept = static_result.get("kept_risks", {})
                new_r = static_result.get("new_risks", {})
                removed_all = sum(len(v) for v in removed.values())
                kept_all = sum(len(v) for v in kept.values())
                new_all = sum(len(v) for v in new_r.values())

                summary_parts = []
                if removed_all > 0:
                    summary_parts.append(f"\n✅ 已移除的风险点 ({removed_all} 个)：")
                    for level in ["critical", "high", "medium", "low"]:
                        for r in removed[level]:
                            summary_parts.append(f"  - {r}")
                if kept_all > 0:
                    summary_parts.append(f"\n⚠️ 残留的原始风险 ({kept_all} 个，不阻断)：")
                    for level in ["critical", "high", "medium", "low"]:
                        for r in kept[level]:
                            summary_parts.append(f"  - {r}")
                if new_all > 0:
                    summary_parts.append(f"\n❌ 新引入的风险 ({new_all} 个)：")
                    for level in ["critical", "high", "medium", "low"]:
                        for r in new_r[level]:
                            summary_parts.append(f"  - {r}")

                risk_comparison = "\n".join(summary_parts)
                static_warnings_section = f"""
## 静态检查：风险变化对比
{risk_comparison}

注意：已移除的风险视为安全改进；残留的原始风险不影响通过；新引入的风险将根据等级决定是否阻止。
"""

            user_input = REVIEW_AGENT_USER_PROMPT.format(
                error_info_str=error_info_str,
                code_comparison_section=code_comparison_section,
                fix_description=fix_description,
                static_warnings_section=static_warnings_section
            )

            messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=user_input)
            ]
            result = await self.llm.ainvoke(messages)

            response_content = result.content
            logger.info(f"审查Agent原始响应: {response_content[:500]}")

            import json as _json

            json_match = re.search(
                r"```json\s*(.*?)\s*```", response_content, re.DOTALL
            )
            if json_match:
                json_content = json_match.group(1)
            else:
                json_content = response_content.strip()

            review_result = _json.loads(json_content)

            # 合并静态检查结果
            review_result["change_lines"] = static_result["change_lines"]
            if "risk_warnings" not in review_result:
                review_result["risk_warnings"] = []
            review_result["risk_warnings"].extend(static_result["risk_warnings"])

            # 保留 rejection_reason（LLM 输出中的新字段）
            if "rejection_reason" not in review_result:
                review_result["rejection_reason"] = ""

            llm_passed = bool(review_result.get("review_passed", False))

            if not llm_passed and not auto_result["all_identical"]:
                logger.warning(
                    f"LLM审查未通过，但自动对比确认代码已修改 "
                    f"({auto_result['changed_files_count']}/{len(modified_files)} 文件已修改)"
                )

            review_result["review_passed"] = llm_passed
            review_result["risk_level"] = static_result["risk_level"]
            review_result["has_critical_risks"] = static_result["has_critical_risks"]
            review_result["has_high_risks"] = static_result["has_high_risks"]
            logger.info(f"审查完成. 通过: {review_result['review_passed']}, "
                        f"风险等级: {static_result['risk_level']}, "
                        f"rejection_reason: {review_result.get('rejection_reason', 'N/A')}")
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
                "rejection_reason": "",
            }
