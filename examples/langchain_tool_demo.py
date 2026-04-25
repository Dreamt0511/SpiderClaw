"""LangChain工具使用示例"""
import asyncio
from src.agent.tools.langchain_tools import (
    set_tool_context,
    read_file,
    write_file,
    search_files,
    clone_repository,
    download_ci_logs,
    parse_python_errors,
    run_tests
)


def demo_basic_tools():
    """演示基础工具的使用"""
    print("=== 基础工具演示 ===")

    # 设置上下文（实际使用时会自动设置）
    set_tool_context({
        "repo_path": "/path/to/your/repo",
        "github_token": "your_github_token"
    })

    # 1. 搜索文件
    print("\n1. 搜索Python文件:")
    py_files = search_files.invoke({"pattern": "**/*.py"})
    print(f"找到 {len(py_files)} 个Python文件")
    for f in py_files[:5]:  # 只显示前5个
        print(f"  - {f}")

    # 2. 读取文件
    if py_files:
        print(f"\n2. 读取文件 {py_files[0]}:")
        content = read_file.invoke({"file_path": py_files[0]})
        if not content.startswith("Error:"):
            print(f"文件内容前100字符: {content[:100]}...")
        else:
            print(content)

    # 3. 写入文件
    print("\n3. 写入测试文件:")
    write_result = write_file.invoke({
        "file_path": "test_output.txt",
        "content": "这是测试内容\nHello World!"
    })
    print(f"写入结果: {write_result}")

    # 4. 运行测试
    print("\n4. 运行测试:")
    test_output = run_tests.invoke({"test_command": "pytest tests/ -v"})
    print(f"测试输出前200字符: {test_output[:200]}...")


def demo_ci_tools():
    """演示CI相关工具的使用"""
    print("\n=== CI工具演示 ===")

    # 1. 下载CI日志
    print("\n1. 下载CI日志:")
    logs_url = "https://github.com/your/repo/actions/runs/123456/logs"
    logs_content = download_ci_logs.invoke({"logs_url": logs_url})
    if not logs_content.startswith("Error:"):
        print(f"下载成功，日志大小: {len(logs_content)} 字符")
    else:
        print(logs_content)

    # 2. 解析Python错误
    print("\n2. 解析Python错误:")
    sample_log = """
Traceback (most recent call last):
  File "app.py", line 10, in <module>
    result = divide(10, 0)
  File "app.py", line 6, in divide
    return a / b
ZeroDivisionError: division by zero
"""
    errors = parse_python_errors.invoke({"log_content": sample_log})
    print(f"解析到 {len(errors)} 个错误:")
    for err in errors:
        print(f"  - {err['error_type']}: {err['error_message']}")
        print(f"    文件: {err['file_path']}:{err['line_number']}")


def demo_clone_repo():
    """演示仓库克隆工具"""
    print("\n=== 仓库克隆演示 ===")

    # 克隆公开仓库
    print("\n1. 克隆仓库:")
    clone_url = "https://github.com/octocat/Hello-World.git"
    repo_path = clone_repository.invoke({
        "clone_url": clone_url,
        "branch": "main"
    })
    if not repo_path.startswith("Error:"):
        print(f"仓库克隆到: {repo_path}")
    else:
        print(repo_path)


async def demo_agent_flow():
    """演示完整的Agent流程"""
    print("\n=== Agent修复流程演示 ===")

    from src.bus.schemas import GitHubEvent
    from src.agent.orchestrator import RepairOrchestrator

    # 创建模拟事件
    event = GitHubEvent(
        event_id="demo_event_123",
        event_type="workflow_run",
        action="completed",
        source="demo",
        repository="owner/repo",
        signature_valid=True,
        clone_url="https://github.com/owner/repo.git",
        branch="main",
        conclusion="failure",
        logs_url="https://github.com/owner/repo/actions/runs/123/logs",
        payload={}
    )

    # 初始化编排器
    orchestrator = RepairOrchestrator(
        github_token="your_github_token",
        openai_api_key="your_openai_api_key"
    )

    # 运行修复流程
    result = await orchestrator.run(event)

    print(f"\n修复结果:")
    print(f"  成功: {result['success']}")
    print(f"  修复描述: {result.get('fix_description', '无')}")
    print(f"  PR链接: {result.get('pr_url', '无')}")
    print(f"  错误信息: {result.get('error_message', '无')}")


if __name__ == "__main__":
    print("SpiderClaw LangChain工具演示")
    print("=" * 50)

    # 取消注释要运行的演示
    # demo_basic_tools()
    # demo_ci_tools()
    # demo_clone_repo()
    # asyncio.run(demo_agent_flow())

    print("\n演示完成！")
