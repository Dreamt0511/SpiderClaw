"""修复Agent实现 - LangChain标准版本"""
from typing import Dict, Any, List
import logging
from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

from src.agent.prompts.fix_agent import FIX_AGENT_SYSTEM_PROMPT
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
        github_token: str = None
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
            base_url=openai_base_url
        )

        # 过滤出修复Agent需要的工具（限制工具数量，避免盲目调用）
        # 只保留最必要的工具，禁止搜索类工具，避免盲目读取文件
        self.tools = [
            tool for tool in all_tools
            if tool.name in [
                "read_file",
                "write_file"
            ]
        ]

        # 创建Agent（使用最新create_agent参数规范）
        self.agent = create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=FIX_AGENT_SYSTEM_PROMPT
        )

    async def generate_fix(
        self,
        ci_logs: str,
        error_locations: List[Dict],
        review_feedback: str = "",
        risk_warnings: List[str] = None,
        test_output: str = "",
        failed_tests: List[str] = None
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

        Returns:
            Dict: 修复结果，包含fix_description、modified_files、code_changes
        """
        try:
            logger.info("使用LangChain Agent生成修复代码")

            # 预先分析错误类型，过滤不需要修复的场景
            # 环境/配置/依赖错误，不需要修改代码
            env_error_patterns = [
                "Could not open requirements file",
                "No such file or directory",
                "ModuleNotFoundError",
                "ImportError",
                "pip install",
                "requirements.txt",
                "dependency",
                "version conflict",
                "permission denied",
                "Certificate verify failed",
                "SSL error",
                "network error",
                "timeout",
                "Connection refused"
            ]

            is_env_error = False
            error_msg = ""
            for err in error_locations:
                error_msg = err.get("error_message", "").lower()
                error_type = err.get("error_type", "").lower()
                for pattern in env_error_patterns:
                    if pattern.lower() in error_msg or pattern.lower() in error_type or pattern.lower() in ci_logs.lower():
                        is_env_error = True
                        break
                if is_env_error:
                    break

            if is_env_error:
                logger.info("检测到环境/配置/依赖错误，不需要修改代码")
                return {
                    "fix_description": "检测到环境/配置/依赖错误，需要在CI环境或项目配置中修复，无需修改代码",
                    "modified_files": [],
                    "code_changes": {},
                    "is_env_error": True  # 标记为环境错误，不需要后续处理
                }

            # 设置工具上下文
            set_tool_context({
                "repo_path": self.repo_path,
                "github_token": self.github_token
            })

            # 构建输入
            prompt_sections = ["""
请分析以下CI错误信息并生成修复代码：

## 重要提示
请严格遵循工作流程：
1. 先分析错误根本原因
2. 只读取与错误直接相关的文件（每次最多读1-2个）
3. 生成最小化修复，不要做无关修改

## CI错误日志
```
{ci_logs}
```

## 解析到的错误信息
{error_locations}
""".format(ci_logs=ci_logs, error_locations=error_locations)]

            # 添加审查反馈（如果有）
            if review_feedback:
                prompt_sections.append(f"""
## 审查反馈（需要修改）
{review_feedback}
""")

            # 添加风险警告（如果有）
            if risk_warnings and len(risk_warnings) > 0:
                prompt_sections.append("""
## 风险警告（需要修复）
- {warnings}
""".format(warnings="\n- ".join(risk_warnings)))

            # 添加测试反馈（如果有）
            if test_output:
                prompt_sections.append(f"""
## 测试输出
```
{test_output}
```
""")

            # 添加失败的测试用例（如果有）
            if failed_tests and len(failed_tests) > 0:
                prompt_sections.append("""
## 失败的测试用例
- {failed_tests}
""".format(failed_tests="\n- ".join(failed_tests)))

            # 添加返回格式要求
            prompt_sections.append("""
请根据以上信息生成修复方案，严格按照要求的JSON格式返回。
""")

            # 合并所有部分
            user_input = "\n".join(prompt_sections)

            # 运行Agent
            result = await self.agent.ainvoke({
                "input": user_input
            })

            # 解析结果
            response_content = result["messages"][-1].content
            logger.info(f"修复Agent原始响应: {response_content[:500]}")

            # 尝试提取JSON
            import json
            import re

            json_match = re.search(r'```json\s*(.*?)\s*```', response_content, re.DOTALL)
            if json_match:
                json_content = json_match.group(1)
            else:
                json_content = response_content.strip()

            fix_result = json.loads(json_content)

            # 验证结果格式
            required_fields = ["fix_description", "modified_files", "code_changes"]
            for field in required_fields:
                if field not in fix_result:
                    raise ValueError(f"修复结果缺少必要字段: {field}")

            logger.info(f"修复生成成功: {fix_result['fix_description']}")
            return fix_result

        except Exception as e:
            logger.error(f"生成修复失败: {e}", exc_info=True)
            return {
                "fix_description": f"生成修复失败: {str(e)}",
                "modified_files": [],
                "code_changes": {},
                "error": str(e)
            }
