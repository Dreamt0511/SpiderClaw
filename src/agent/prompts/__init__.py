"""Agent提示词模板"""
from .fix_agent import FIX_AGENT_SYSTEM_PROMPT, FIX_AGENT_USER_PROMPT
from .review_agent_prompts import REVIEW_AGENT_SYSTEM_PROMPT, REVIEW_AGENT_USER_PROMPT
from .test_agent_prompts import TEST_AGENT_SYSTEM_PROMPT, TEST_AGENT_USER_PROMPT

__all__ = [
    "FIX_AGENT_SYSTEM_PROMPT",
    "FIX_AGENT_USER_PROMPT",
    "REVIEW_AGENT_SYSTEM_PROMPT",
    "REVIEW_AGENT_USER_PROMPT",
    "TEST_AGENT_SYSTEM_PROMPT",
    "TEST_AGENT_USER_PROMPT"
]

#__all__ 不是约定俗成的名字，而是 Python 语法的强制要求，只有这个名字才能触发导入控制机制。