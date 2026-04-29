"""Agent 工厂 — 统一创建和配置所有 Agent"""

import logging
from dataclasses import dataclass, field

from src.agent.subagents.fix_agent import FixAgent
from src.agent.subagents.review_agent import ReviewAgent
from src.agent.subagents.test_agent import TestAgent

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Agent 配置"""
    llm_model: str = "gpt-4o"
    fix_temperature: float = 0.1
    review_temperature: float = 0.0
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    github_token: str = ""
    max_change_lines: int = 50  # 从 20 提升到 50（try/except ImportError 包裹需要多行）
    test_command: str = "pytest"


class AgentFactory:
    """统一创建和配置所有 Agent"""

    def __init__(self, config: AgentConfig):
        self.config = config

    def create_fix_agent(self, repo_path: str, system_prompt_override: str = "") -> FixAgent:
        return FixAgent(
            repo_path=repo_path,
            llm_model=self.config.llm_model,
            temperature=self.config.fix_temperature,
            openai_api_key=self.config.openai_api_key,
            openai_base_url=self.config.openai_base_url,
            github_token=self.config.github_token,
            system_prompt_override=system_prompt_override,
            max_change_lines=self.config.max_change_lines,
        )

    def create_review_agent(self, repo_path: str = "") -> ReviewAgent:
        return ReviewAgent(
            llm_model=self.config.llm_model,
            temperature=self.config.review_temperature,
            openai_api_key=self.config.openai_api_key,
            openai_base_url=self.config.openai_base_url,
            max_change_lines=self.config.max_change_lines,
            repo_path=repo_path,
            github_token=self.config.github_token,
        )

    def create_test_agent(self, repo_path: str) -> TestAgent:
        return TestAgent(
            repo_path=repo_path,
            openai_api_key=self.config.openai_api_key,
            openai_base_url=self.config.openai_base_url,
            test_command=self.config.test_command,
        )
