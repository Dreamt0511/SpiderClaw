"""审查Agent实现 - LangChain标准版本"""

from typing import Dict, Any, List
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

    def _static_security_check(
        self, code_changes: Dict[str, str], diff_content: str
    ) -> Dict[str, Any]:
        """
        静态安全检查（先于LLM审查执行）

        Args:
            code_changes: 代码变更字典
            diff_content: diff内容

        Returns:
            Dict: 检查结果
        """
        risk_warnings = []
        dangerous_operations = []

        # 1. 检查变更行数（仅作为警告，不拦截）
        add_count = 0
        remove_count = 0
        for line in diff_content.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                add_count += 1
            elif line.startswith("-") and not line.startswith("---"):
                remove_count += 1
        change_lines = add_count + remove_count

        if change_lines > self.max_change_lines:
            risk_warnings.append(
                f"变更行数超过建议值: {change_lines} 行，建议不超过 {self.max_change_lines} 行"
            )

        # 2. 扫描危险模式（必须拦截）
        dangerous_patterns = [
            r"rm\s+-rf",
            r"shutil\.rmtree",
            r"os\.remove",
            r"os\.unlink",
            r"os\.rmdir",
            r"subprocess\.run",
            r"subprocess\.call",
            r"subprocess\.Popen",
            r"os\.system",
            r"eval\(",
            r"exec\(",
            r"DROP\s+TABLE",
            r"DELETE\s+FROM.*WHERE\s+1=1",
            r"api_key\s*=",
            r"token\s*=",
            r"password\s*=",
            r"secret\s*=",
        ]

        for file_path, content in code_changes.items():
            for pattern in dangerous_patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    dangerous_operations.append(f"文件 {file_path} 中包含危险操作: {pattern}")

        # 3. 检查是否修改了非Python文件（仅作为警告）
        for file_path in code_changes.keys():
            if not file_path.endswith(".py"):
                risk_warnings.append(f"修改了非Python文件: {file_path}")

        # 合并警告和危险操作
        all_warnings = dangerous_operations + risk_warnings

        return {
            "change_lines": change_lines,
            "risk_warnings": all_warnings,
            "has_dangerous_operations": len(dangerous_operations) > 0,
            "dangerous_operations": dangerous_operations,
        }

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

            # 先执行静态检查
            static_result = self._static_security_check(code_changes, diff_content)

            # 只有危险操作才直接拦截
            if static_result["has_dangerous_operations"]:
                logger.warning(f"发现危险操作: {static_result['dangerous_operations']}")
                return {
                    "review_passed": False,
                    "review_comments": "发现危险操作，审查未通过: "
                    + "; ".join(static_result["dangerous_operations"]),
                    "change_lines": static_result["change_lines"],
                    "risk_warnings": static_result["risk_warnings"],
                }

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
                static_warnings_section = f"""
## 静态检查警告
{chr(10).join(f"- {warning}" for warning in static_result["risk_warnings"])}

注意：以上警告仅供参考，是否通过审查请根据实际情况判断。
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
            logger.info(f"审查完成. 通过: {review_result['review_passed']}")
            return review_result

        except Exception as e:
            logger.error(f"审查Agent执行失败: {e}", exc_info=True)
            return {
                "review_passed": False,
                "review_comments": f"审查过程出错: {str(e)}",
                "change_lines": 0,
                "risk_warnings": [str(e)],
            }
