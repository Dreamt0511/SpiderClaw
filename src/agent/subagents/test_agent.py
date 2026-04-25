"""测试Agent实现 - LangChain标准版本"""
from typing import Dict, Any, List
import logging
import re
from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

from src.agent.prompts.test_agent_prompts import TEST_AGENT_SYSTEM_PROMPT, TEST_AGENT_USER_PROMPT
from src.agent.tools import all_tools, set_tool_context, run_tests as run_tests_tool

logger = logging.getLogger(__name__)


class TestAgent:
    """测试Agent，使用LangChain标准工具调用模式"""

    def __init__(
        self,
        repo_path: str,
        llm_model: str = "gpt-4o",
        temperature: float = 0.1,
        openai_api_key: str = None,
        openai_base_url: str = "https://api.openai.com/v1",
        test_command: str = "pytest"
    ):
        """
        初始化测试Agent

        Args:
            repo_path: 本地仓库路径
            llm_model: LLM模型名称
            temperature: 温度参数
            openai_api_key: OpenAI API密钥
            openai_base_url: OpenAI API基础URL
            test_command: 测试命令
        """
        self.repo_path = repo_path
        self.test_command = test_command

        # 初始化LLM
        self.llm = ChatOpenAI(
            model=llm_model,
            temperature=temperature,
            api_key=openai_api_key,
            base_url=openai_base_url
        )

    def _parse_failed_tests(self, test_output: str) -> List[str]:
        """
        解析测试输出中的失败用例

        Args:
            test_output: 测试输出内容

        Returns:
            List[str]: 失败的测试用例名称列表
        """
        failed_tests = []

        # 匹配pytest失败格式
        failed_pattern = re.compile(r'^FAILED ([^\s:]+::[^\s]+)', re.MULTILINE)
        matches = failed_pattern.findall(test_output)
        failed_tests.extend(matches)

        # 匹配简短摘要格式
        summary_pattern = re.compile(r'=+ short test summary info =+\n(.*?)\n=+', re.DOTALL)
        summary_match = summary_pattern.search(test_output)
        if summary_match:
            summary_content = summary_match.group(1)
            summary_failed = re.findall(r'FAILED\s+([^\s]+)', summary_content)
            failed_tests.extend(summary_failed)

        # 去重
        return list(set(failed_tests))

    async def verify_fix(
        self,
        error_locations: List[Dict],
        fix_description: str,
        diff_content: str
    ) -> Dict[str, Any]:
        """
        运行测试并验证修复有效性

        Args:
            error_locations: 原始错误位置列表
            fix_description: 修复描述
            diff_content: 修复的diff内容

        Returns:
            Dict: 测试结果
        """
        try:
            logger.info("运行测试Agent")

            # 设置工具上下文
            set_tool_context({
                "repo_path": self.repo_path
            })

            # 运行测试
            test_output = run_tests_tool.invoke({"test_command": self.test_command})

            # 解析测试结果
            test_passed = "Exit code: 0" in test_output
            failed_tests = self._parse_failed_tests(test_output)

            # 如果测试直接通过，无需调用LLM分析
            if test_passed and not failed_tests:
                return {
                    "test_passed": True,
                    "test_output": test_output,
                    "failed_tests": [],
                    "verification_summary": "所有测试通过，修复有效"
                }

            # 否则调用LLM分析测试结果
            user_input = TEST_AGENT_USER_PROMPT.format(
                fix_description=fix_description,
                diff_content=diff_content,
                test_output=test_output,
                error_locations=error_locations
            )

            # 直接调用LLM分析测试结果
            from langchain_core.messages import SystemMessage, HumanMessage
            messages = [
                SystemMessage(content=TEST_AGENT_SYSTEM_PROMPT),
                HumanMessage(content=user_input)
            ]
            result = await self.llm.ainvoke(messages)

            # 解析结果
            response_content = result.content
            logger.info(f"测试Agent原始响应: {response_content[:500]}")

            # 尝试提取JSON
            import json
            import re

            json_match = re.search(r'```json\s*(.*?)\s*```', response_content, re.DOTALL)
            if json_match:
                json_content = json_match.group(1)
            else:
                json_content = response_content.strip()

            test_result = json.loads(json_content)

            # 确保test_passed是布尔值
            test_result["test_passed"] = bool(test_result.get("test_passed", False))
            # 补充原始测试输出
            test_result["test_output"] = test_output

            logger.info(f"测试完成. 通过: {test_result['test_passed']}")
            return test_result

        except Exception as e:
            logger.error(f"测试Agent执行失败: {e}", exc_info=True)
            return {
                "test_passed": False,
                "test_output": f"测试执行失败: {str(e)}",
                "failed_tests": [],
                "verification_summary": f"测试过程出错: {str(e)}"
            }
