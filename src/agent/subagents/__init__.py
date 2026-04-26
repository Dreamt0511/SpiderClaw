"""子Agent实现 - LangChain标准版本"""
from .fix_agent import FixAgent
from .review_agent import ReviewAgent
from .test_agent import TestAgent

__all__ = ["FixAgent", "ReviewAgent", "TestAgent"]

"""
统一的导入接口
外部代码可以通过简洁的方式导入：
from agent.subagents import FixAgent, ReviewAgent, TestAgent

而不是：
from agent.subagents.fix_agent import FixAgent
from agent.subagents.review_agent import ReviewAgent
from agent.subagents.test_agent import TestAgent
"""