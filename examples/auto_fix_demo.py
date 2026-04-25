"""自动修复功能演示脚本"""
import asyncio
import tempfile
import os
from git import Repo
from src.bus.schemas import GitHubEvent
from src.agent.orchestrator import RepairOrchestrator


async def demo_auto_fix():
    """演示自动修复流程"""
    print("🚀 SpiderClaw 自动修复功能演示")
    print("=" * 60)

    # 1. 创建模拟的GitHub事件
    print("\n1. 创建模拟的CI失败事件")

    # 创建临时测试仓库
    with tempfile.TemporaryDirectory() as tmpdir:
        # 初始化Git仓库
        repo = Repo.init(tmpdir)
        repo.config_writer().set_value("user", "name", "Test User").release()
        repo.config_writer().set_value("user", "email", "test@example.com").release()

        # 创建一个有bug的Python文件
        buggy_code = """
def calculate_average(numbers):
    total = sum(numbers)
    count = len(numbers)
    return total / count

# 测试用例
numbers = []
result = calculate_average(numbers)
print(f"Average: {result}")
"""
        code_path = os.path.join(tmpdir, "stats.py")
        with open(code_path, "w") as f:
            f.write(buggy_code)

        # 提交初始代码
        repo.index.add([code_path])
        repo.index.commit("Initial commit with bug")

        # 创建模拟的CI日志（包含除零错误）
        ci_logs = """
Traceback (most recent call last):
  File "stats.py", line 9, in <module>
    result = calculate_average(numbers)
  File "stats.py", line 4, in calculate_average
    return total / count
ZeroDivisionError: division by zero
"""

        # 创建GitHub事件
        event = GitHubEvent(
            event_id="test_event_123",
            event_type="workflow_run",
            action="completed",
            source="github_webhook",
            repository="test/repo",
            signature_valid=True,
            clone_url=f"file://{tmpdir}",  # 使用本地文件路径作为克隆地址
            branch="main",
            conclusion="failure",
            logs_url="",  # 本地演示不需要真实日志URL
            payload={}
        )

        print(f"   事件ID: {event.event_id}")
        print(f"   仓库: {event.repository}")
        print(f"   错误类型: ZeroDivisionError")

        # 2. 初始化编排器
        print("\n2. 初始化修复编排器")
        orchestrator = RepairOrchestrator(
            github_token="dummy_token",  # 本地演示不需要真实Token
            openai_api_key="your_api_key_here",  # 替换为真实的API Key
            openai_base_url="https://api.openai.com/v1",
            llm_model="gpt-4o",
            max_retries=2,
            max_change_lines=20
        )

        # 3. 运行修复流程
        print("\n3. 运行自动修复流程...")
        print("   这需要调用LLM，请确保网络连接正常...")

        # Mock LLM响应（演示用，实际使用时移除这段）
        from unittest.mock import patch, AsyncMock, Mock

        mock_fix_response = Mock()
        mock_fix_response.content = """
{
    "fix_description": "修复空列表时的除零错误，添加空列表检查",
    "modified_files": ["stats.py"],
    "code_changes": {
        "stats.py": "def calculate_average(numbers):\\n    total = sum(numbers)\\n    count = len(numbers)\\n    if count == 0:\\n        return 0.0\\n    return total / count\\n\\n# 测试用例\\nnumbers = []\\nresult = calculate_average(numbers)\\nprint(f\\"Average: {result}\\")"
    }
}
"""

        mock_review_response = Mock()
        mock_review_response.content = """
{
    "review_passed": true,
    "review_comments": "修复正确，添加了空列表检查，没有安全问题",
    "change_lines": 3,
    "risk_warnings": []
}
"""

        mock_test_response = Mock()
        mock_test_response.content = """
{
    "test_passed": true,
    "test_output": "测试通过，修复有效",
    "failed_tests": [],
    "verification_summary": "修复成功解决了除零错误，当输入空列表时返回0.0"
}
"""

        with patch.object(orchestrator.ci_tools, 'download_github_logs', return_value=None):
            # Mock修复Agent
            with patch('src.agent.orchestrator.FixAgent') as mock_fix_agent_class:
                mock_fix_agent = AsyncMock()
                mock_fix_agent.generate_fix.return_value = {
                    "fix_description": "修复空列表时的除零错误，添加空列表检查",
                    "modified_files": ["stats.py"],
                    "code_changes": {
                        "stats.py": "def calculate_average(numbers):\n    total = sum(numbers)\n    count = len(numbers)\n    if count == 0:\n        return 0.0\n    return total / count\n\n# 测试用例\nnumbers = []\nresult = calculate_average(numbers)\nprint(f\"Average: {result}\")"
                    }
                }
                mock_fix_agent_class.return_value = mock_fix_agent

                # Mock审查Agent
                with patch('src.agent.orchestrator.ReviewAgent') as mock_review_agent_class:
                    mock_review_agent = AsyncMock()
                    mock_review_agent.review_changes.return_value = {
                        "review_passed": True,
                        "review_comments": "修复正确，添加了空列表检查，没有安全问题",
                        "change_lines": 3,
                        "risk_warnings": []
                    }
                    mock_review_agent_class.return_value = mock_review_agent

                    # Mock测试Agent
                    with patch('src.agent.orchestrator.TestAgent') as mock_test_agent_class:
                        mock_test_agent = AsyncMock()
                        mock_test_agent.run_tests.return_value = {
                            "test_passed": True,
                            "test_output": "测试通过",
                            "failed_tests": []
                        }
                        mock_test_agent.verify_fix.return_value = {
                            "test_passed": True,
                            "test_output": "测试通过",
                            "failed_tests": [],
                            "verification_summary": "修复成功"
                        }
                        mock_test_agent_class.return_value = mock_test_agent

                        # Mock PR创建
                        with patch.object(orchestrator.git_tools, 'create_pull_request') as mock_create_pr:
                            mock_create_pr.return_value = Mock(
                                html_url="https://github.com/test/repo/pull/1",
                                number=1
                            )

                            # 运行修复流程
                            result = await orchestrator.run(event)

        # 4. 显示结果
        print("\n4. 修复结果:")
        print(f"   修复成功: {'✅ 是' if result['success'] else '❌ 否'}")
        if result.get("pr_url"):
            print(f"   PR链接: {result['pr_url']}")
        print(f"   修复描述: {result.get('fix_description', '无')}")
        print(f"   修改文件: {', '.join(result.get('modified_files', []))}")
        print(f"   变更行数: {result.get('change_lines', 0)} 行")

        if result.get("diff_content"):
            print("\n   修复Diff:")
            print("-" * 60)
            print(result["diff_content"])
            print("-" * 60)

        if not result["success"]:
            print(f"   错误信息: {result.get('error_message', '未知错误')}")

    print("\n🎉 演示完成！")
    print("\n要在真实环境中使用:")
    print("1. 配置 config/agent-config.yaml 中的API密钥")
    print("2. 启动Webhook服务: spiderclaw webhook start")
    print("3. 在GitHub仓库中配置Webhook指向你的服务")
    print("4. 当CI失败时，系统会自动修复并创建PR！")


if __name__ == "__main__":
    asyncio.run(demo_auto_fix())
