"""修复Agent实现 - LangChain标准版本"""

from typing import Dict, Any, List
import json
import logging
from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

from src.agent.prompts.fix_agent import FIX_AGENT_SYSTEM_PROMPT, FIX_AGENT_USER_PROMPT
from src.agent.tools.langchain_tools import all_tools, set_tool_context

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
    ):
        """
        初始化修复Agent

        Args:
            repo_path: 本地仓库路径
            llm_model: LLM模型名称
            temperature: 温度参数
            openai_api_key: OpenAI API密钥
            openai_base_url: OpenAI API基础URL
            github_token: GitHub访问令牌
        """
        self.repo_path = repo_path
        self.github_token = github_token

        # 初始化LLM
        self.llm = ChatOpenAI(
            model=llm_model,
            temperature=temperature,
            api_key=openai_api_key,
            base_url=openai_base_url,
        )

        # 过滤出修复Agent需要的工具（只读权限，禁止写入）
        # search_files仅用于SyntaxError无文件路径时的定位，不会被滥用
        self.tools = [
            tool for tool in all_tools if tool.name in ["read_file", "search_files"]
        ]

        # 创建Agent（使用最新create_agent参数规范）
        self.agent = create_agent(
            model=self.llm, tools=self.tools, system_prompt=FIX_AGENT_SYSTEM_PROMPT
        )

    @staticmethod
    def _parse_json_safely(json_str: str) -> Dict[str, Any] | None:
        """健壮的JSON解析，尝试多种策略处理LLM常见的JSON格式问题"""
        import re as _re

        # 清理：移除BOM、空字符
        json_str = json_str.strip().lstrip("﻿")

        # 策略1: 标准解析
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        # 策略2: 宽松模式（允许字符串中的未转义控制字符）
        try:
            return json.loads(json_str, strict=False)
        except json.JSONDecodeError:
            pass

        # 策略3: 移除尾随逗号后重试
        try:
            cleaned = _re.sub(r",\s*([}\]])", r"\1", json_str)
            return json.loads(cleaned, strict=False)
        except json.JSONDecodeError:
            pass

        # 策略4: 处理 code_changes 值中可能的未转义双引号
        # 在 code_changes 的字符串值中，找到并转义未转义的 " 字符
        try:
            # 查找 code_changes 对象中所有字符串值
            def _escape_code_content(m):
                prefix = m.group(1)  # "filename.py": "
                content = m.group(2)  # the raw content
                # 转义内容中的未转义双引号（但不转义已转义的 \"）
                escaped = content.replace('\\"', "\x00")  # 暂存已转义的
                escaped = escaped.replace('"', '\\"')  # 转义所有
                escaped = escaped.replace("\x00", '\\"')  # 恢复原来的
                return prefix + escaped + '"'

            # 匹配 "key": "value" 模式，其中 value 跨越多行
            pattern = _re.compile(
                r'("(?:code_changes|fix_description)"\s*:\s*")(.*?)(?<!\\")"(?=\s*[,}\]])',
                _re.DOTALL,
            )
            # 这只修复简单的 case，复杂 case 需要递归
            repaired = json_str
            for _ in range(3):  # 多次迭代处理嵌套
                new_repaired = pattern.sub(_escape_code_content, repaired)
                if new_repaired == repaired:
                    break
                repaired = new_repaired
            return json.loads(repaired, strict=False)
        except (json.JSONDecodeError, Exception):
            pass

        # 策略5: 手动提取必需字段（最后手段）
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

    async def generate_fix(
        self,
        ci_logs: str,
        error_locations: List[Dict],
        review_feedback: str = "",
        risk_warnings: List[str] = None,
        test_output: str = "",
        failed_tests: List[str] = None,
        retry_count: int = 0,
    ) -> Dict[str, Any]:
        """
        生成修复代码

        Args:
            ci_logs: CI失败日志内容
            error_locations: 错误位置列表
            review_feedback: 审查反馈（重试时提供）
            risk_warnings: 风险警告列表（重试时提供）
            test_output: 测试输出（重试时提供）
            failed_tests: 失败的测试用例列表（重试时提供）
            retry_count: 重试次数（默认0，最多重试3次）

        Returns:
            Dict: 修复结果，包含fix_description、modified_files、code_changes
        """
        try:
            logger.info(f"使用LangChain Agent生成修复代码，重试次数: {retry_count}")

            # 重试次数上限检测，避免死循环
            if retry_count >= 3:
                logger.error("重试次数达到上限，放弃修复")
                return {
                    "fix_description": "修复失败：重试次数已达3次上限，无法生成有效修复",
                    "modified_files": [],
                    "code_changes": {},
                    "error": "重试次数达到上限",
                }

            # 收集所有有明确文件路径的错误文件（去重），不限于语法错误
            target_files = []
            for err in error_locations:
                file_path = err.get("file_path")
                error_type = err.get("error_type", "")
                if file_path and file_path != "<string>" and file_path not in target_files:
                    target_files.append(file_path)

            # 设置工具上下文
            set_tool_context(
                {"repo_path": self.repo_path, "github_token": self.github_token}
            )

            # 构建动态部分

            #审查反馈（需要修改）
            review_feedback_section = (f"""{review_feedback}""" if review_feedback else "")

            # 风险警告（需要修复）
            risk_warnings_section = (f"""{"\n- ".join(risk_warnings)}""" if risk_warnings and len(risk_warnings) > 0 else "")

            # 测试输出
            test_output_section = (f"""```{test_output}```""" if test_output else "")

            # 失败的测试用例
            failed_tests_section = (f"""- {"\n- ".join(failed_tests)}""" if failed_tests and len(failed_tests) > 0 else "")

            # 根因错误识别与优先处理
            root_cause_errors = [
                err for err in error_locations if err.get("is_root_cause")
            ]
            root_cause_section = ""
            if root_cause_errors:
                root_cause_lines = []
                for err in root_cause_errors:
                    fp = err.get("file_path", "未知文件")
                    et = err.get("error_type", "UnknownError")
                    em = err.get("error_message", "")
                    chain_info = ""
                    if err.get("chain_consequence"):
                        chain_info = f"\n      → 导致: {err['chain_consequence'][:100]}"
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

            # 使用模板构建用户输入
            import json

            error_locations_json = json.dumps(
                error_locations, ensure_ascii=False, indent=2
            )

            # 简化日志输出，避免编码问题
            logger.info(f"找到错误数量: {len(error_locations)}")
            logger.info(f"目标修复文件列表: {target_files}")

            # 构建目标文件指令（动态部分）—— 对所有有文件路径的错误类型生效
            force_instruction_content = ""
            if target_files:
                if len(target_files) == 1:
                    file_path = target_files[0]
                    force_instruction_content = f"""1. **唯一修复目标文件：{file_path}**
   - 你 **只能** 修复这个文件，绝对不允许修改或返回其他任何文件
   - 在你的JSON响应中，`modified_files`数组 **必须** 只包含["{file_path}"]
   - 在你的JSON响应中，`code_changes`对象的key **必须** 是"{file_path}"
"""
                else:
                    files_str = "、".join(target_files)
                    files_json = '", "'.join(target_files)
                    force_instruction_content = f"""1. **修复目标文件列表：{files_str}**
   - 你 **只能** 修复列表中的这些文件，绝对不允许修改或返回其他任何文件
   - 在你的JSON响应中，`modified_files`数组 **必须** 只包含["{files_json}"]
   - 在你的JSON响应中，`code_changes`对象的key **必须** 是上述列表中的文件路径
"""
                logger.info(f"构建目标文件指令: {force_instruction_content[:300]}...")
            else:
                force_instruction_content = ""
                logger.info("没有找到带文件路径的错误，不添加目标文件指令")

            user_input = FIX_AGENT_USER_PROMPT.format(
                force_instruction_content=force_instruction_content,
                ci_logs=ci_logs,
                error_locations=error_locations_json,
                repo_path=self.repo_path,
                root_cause_section=root_cause_section,
                review_feedback_section=review_feedback_section,
                risk_warnings_section=risk_warnings_section,
                test_output_section=test_output_section,
                failed_tests_section=failed_tests_section,
            )

            logger.info(f"用户提示词构建完成，长度: {len(user_input)}")
            logger.info(f"提示词前500字符: {user_input[:500]}...")

            # 使用Agent调用，支持工具自动调用，最多允许3轮工具调用
            logger.info("开始调用Agent生成修复...")
            config = {"recursion_limit": 10}  # 限制最大调用次数，防止无限循环
            result = await self.agent.ainvoke({"input": user_input}, config=config)
            logger.info("Agent调用完成")

            # 解析结果
            response_content = result["messages"][-1].content
            logger.info(f"修复Agent原始响应长度: {len(response_content)}")
            logger.info(f"修复Agent原始响应完整内容: {response_content}")

            # 检查是否有工具调用
            if "tool_calls" in result["messages"][-1].additional_kwargs:
                tool_calls = result["messages"][-1].additional_kwargs["tool_calls"]
                logger.info(f"Agent调用了工具: {tool_calls}")
                # 这里不需要手动处理工具调用，LangChain的create_agent会自动处理

            # 尝试提取JSON
            import json
            import re

            json_content = ""

            # 首先尝试匹配markdown格式的JSON
            json_match = re.search(
                r"```json\s*(.*?)\s*```", response_content, re.DOTALL
            )
            if json_match:
                json_content = json_match.group(1)
                logger.info(f"从markdown中提取JSON: {json_content}")
            else:
                # 尝试直接匹配JSON对象
                json_match = re.search(r"\{.*\}", response_content, re.DOTALL)
                if json_match:
                    json_content = json_match.group(0)
                    logger.info(f"从响应中直接提取JSON: {json_content}")
                else:
                    # 如果没有找到JSON，尝试清理响应内容
                    # 移除所有非JSON内容，只保留从第一个{到最后一个}的部分
                    start_idx = response_content.find("{")
                    end_idx = response_content.rfind("}")
                    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                        json_content = response_content[start_idx : end_idx + 1]
                        logger.info(f"清理后提取的JSON: {json_content}")
                    else:
                        json_content = response_content.strip()
                        logger.warning(f"无法提取JSON，响应内容为: {response_content}")

            # 尝试解析JSON（带自动修复）
            fix_result = self._parse_json_safely(json_content)
            if fix_result is None:
                logger.error(f"JSON解析失败，内容: {json_content}")
                raise json.JSONDecodeError("所有修复策略均失败", json_content, 0)
            logger.info(f"JSON解析成功: {fix_result}")

            # 验证结果格式
            required_fields = ["fix_description", "modified_files", "code_changes"]
            for field in required_fields:
                if field not in fix_result:
                    raise ValueError(f"修复结果缺少必要字段: {field}")

            # 统一文件路径格式，使用正斜杠
            def normalize_path(path):
                return path.replace("\\", "/").lstrip("./")

            # 规范化modified_files中的路径
            fix_result["modified_files"] = [
                normalize_path(f) for f in fix_result["modified_files"]
            ]

            # 规范化code_changes中的路径
            normalized_code_changes = {}
            for path, content in fix_result["code_changes"].items():
                normalized_code_changes[normalize_path(path)] = content
            fix_result["code_changes"] = normalized_code_changes

            # 修复结果验证（适用于所有错误类型）
            if target_files:
                # 检查返回的文件路径是否符合要求
                expected_files = [f.lstrip("./").replace("\\", "/") for f in target_files]
                expected_files = list(set(expected_files))

                # 检查返回的文件是否在预期列表中
                returned_files = [
                    f.lstrip("./").replace("\\", "/")
                    for f in fix_result.get("code_changes", {}).keys()
                ]
                invalid_files = [
                    f for f in returned_files if f not in expected_files
                ]

                if invalid_files:
                    logger.error(
                        f"修复Agent返回了不允许的文件: {invalid_files}，允许修复的文件: {expected_files}"
                    )
                    # 不在此处重试，由Orchestrator的review→retry循环处理
                    return {
                        "fix_description": f"修复Agent返回了不允许的文件: {invalid_files}",
                        "modified_files": fix_result.get("modified_files", []),
                        "code_changes": fix_result.get("code_changes", {}),
                        "error": f"返回了不在允许列表中的文件: {invalid_files}",
                    }

            if (
                not fix_result.get("code_changes")
                or len(fix_result["code_changes"]) == 0
            ):
                logger.error("修复Agent返回了空的code_changes")
                # 不在此处重试，由Orchestrator的review→retry循环处理
                return {
                    "fix_description": "修复Agent未能生成有效的代码变更",
                    "modified_files": fix_result.get("modified_files", []),
                    "code_changes": fix_result.get("code_changes", {}),
                    "error": "code_changes为空",
                }

            # 验证修复后的代码没有语法错误
            for file_path, code_content in fix_result["code_changes"].items():
                try:
                    import ast
                    ast.parse(code_content)
                    logger.info(f"文件 {file_path} 语法检查通过")
                except SyntaxError as e:
                    logger.error(f"修复后的代码仍然有语法错误: {e}")
                    # 不在此处重试，由Orchestrator的review→retry循环处理
                    return {
                        "fix_description": f"修复后的代码仍有语法错误: {str(e)}",
                        "modified_files": fix_result.get("modified_files", []),
                        "code_changes": fix_result.get("code_changes", {}),
                        "error": f"生成的代码有语法错误: {str(e)}",
                    }

            logger.info(f"修复生成成功: {fix_result['fix_description']}")
            return fix_result

        except Exception as e:
            logger.error(f"生成修复失败: {e}", exc_info=True)
            return {
                "fix_description": f"生成修复失败: {str(e)}",
                "modified_files": [],
                "code_changes": {},
                "error": str(e),
            }
