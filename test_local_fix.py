#!/usr/bin/env python3
"""本地测试修复流程，不需要Webhook"""
import asyncio
import os
import sys
import tempfile
import datetime
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))

from src.agent.orchestrator import RepairOrchestrator
from src.config.settings import Settings
from src.bus.schemas import GitHubEvent


async def test_local_syntax_error_fix():
    """测试本地语法错误修复流程"""
    print("=== 本地语法错误修复测试 ===")

    # 加载配置
    settings = Settings()

    # 创建临时测试目录
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # 创建有语法错误的测试文件
        test_file = tmpdir_path / "test_syntax_errors.py"
        test_content = """def process_data(items):
    result = []
    for item in items  # 缺少冒号
        if item > 0:
            result.append(item * 2)
        else  # 缺少冒号
            result.append(item)
    return result

def another_function(x)  # 缺少冒号
    return x * 2
"""
        test_file.write_text(test_content, encoding="utf-8")
        print(f"创建测试文件: {test_file}")
        print("错误内容：")
        print(test_content)

        # 初始化Git仓库
        os.chdir(tmpdir_path)
        os.system("git init > /dev/null 2>&1")
        os.system("git config user.name 'Test User' > /dev/null 2>&1")
        os.system("git config user.email 'test@example.com' > /dev/null 2>&1")
        os.system("git add . > /dev/null 2>&1")
        os.system("git commit -m 'initial commit' > /dev/null 2>&1")
        os.system("git checkout -b my_test_branch > /dev/null 2>&1")

        # 模拟CI错误日志
        ci_logs = """
File "test_syntax_errors.py", line 3
    for item in items  # 缺少冒号
                      ^
SyntaxError: expected ':'

File "test_syntax_errors.py", line 6
    else  # 缺少冒号
        ^
SyntaxError: expected ':'

File "test_syntax_errors.py", line 9
def another_function(x)  # 缺少冒号
                        ^
SyntaxError: expected ':'
"""

        # 创建模拟事件
        event = GitHubEvent(
            event_id="test-event-123",
            event_type="workflow_run",
            action="completed",
            timestamp=datetime.datetime.now(),
            source="local_test",
            repository="test/repo",
            signature_valid=True,
            clone_url=f"file://{tmpdir_path}",
            branch="my_test_branch",
            conclusion="failure",
            logs_url="",
            payload={}
        )

        # 创建Orchestrator
        orchestrator = RepairOrchestrator(
            github_token=settings.github.token,
            openai_api_key=settings.openai.api_key,
            openai_base_url=settings.openai.base_url,
            llm_model=settings.openai.model_name,
            max_retries=settings.agent.max_retries,
            max_change_lines=settings.agent.max_change_lines
        )

        # 运行修复流程
        print("\n启动修复流程...")
        result = await orchestrator.run_repair(event, ci_logs=ci_logs)

        print("\n=== 修复结果 ===")
        print(f"成功: {result.get('success', False)}")
        print(f"错误信息: {result.get('error_message', '')}")

        if result.get('success'):
            print(f"修复描述: {result.get('fix_description', '')}")
            print(f"修改的文件: {result.get('modified_files', [])}")

            # 查看修复后的文件内容
            print("\n修复后的文件内容:")
            for file_path in result.get('modified_files', []):
                full_path = tmpdir_path / file_path
                if full_path.exists():
                    print(f"\n--- {file_path} ---")
                    print(full_path.read_text(encoding="utf-8"))


async def test_local_runtime_error_fix():
    """测试本地运行时错误修复流程"""
    print("\n\n=== 本地运行时错误修复测试 ===")

    # 加载配置
    settings = Settings()

    # 创建临时测试目录
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # 复制用户提供的运行时错误文件
        source_file = Path("D:/U 盘/SpiderClaw/local_test/test_runtime_error_2.py")
        test_content = source_file.read_text(encoding="utf-8")
        test_file = tmpdir_path / "test_runtime_error.py"
        test_file.write_text(test_content, encoding="utf-8")
        print(f"创建测试文件: {test_file}")
        print("错误内容：")
        print(test_content)

        # 初始化Git仓库
        os.chdir(tmpdir_path)
        os.system("git init > /dev/null 2>&1")
        os.system("git config user.name 'Test User' > /dev/null 2>&1")
        os.system("git config user.email 'test@example.com' > /dev/null 2>&1")
        os.system("git add . > /dev/null 2>&1")
        os.system("git commit -m 'initial commit' > /dev/null 2>&1")
        os.system("git checkout -b my_test_branch > /dev/null 2>&1")

        # 运行代码获取真实的错误日志
        print("\n运行代码获取错误日志...")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(test_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        ci_logs = stderr.decode('utf-8', errors='replace')
        print("错误日志:")
        print(ci_logs)

        # 创建模拟事件
        event = GitHubEvent(
            event_id="test-event-123",
            event_type="workflow_run",
            action="completed",
            timestamp=datetime.datetime.now(),
            source="local_test",
            repository="test/repo",
            signature_valid=True,
            clone_url=f"file://{tmpdir_path}",
            branch="my_test_branch",
            conclusion="failure",
            logs_url="",
            payload={}
        )

        # 创建Orchestrator
        orchestrator = RepairOrchestrator(
            github_token=settings.github.token,
            openai_api_key=settings.openai.api_key,
            openai_base_url=settings.openai.base_url,
            llm_model=settings.openai.model_name,
            max_retries=settings.agent.max_retries,
            max_change_lines=settings.agent.max_change_lines
        )

        # 运行修复流程
        print("\n启动修复流程...")
        result = await orchestrator.run_repair(event, ci_logs=ci_logs)

        print("\n=== 修复结果 ===")
        print(f"成功: {result.get('success', False)}")
        print(f"错误信息: {result.get('error_message', '')}")

        if result.get('success'):
            print(f"修复描述: {result.get('fix_description', '')}")
            print(f"修改的文件: {result.get('modified_files', [])}")

            # 查看修复后的文件内容
            print("\n修复后的文件内容:")
            for file_path in result.get('modified_files', []):
                full_path = tmpdir_path / file_path
                if full_path.exists():
                    print(f"\n--- {file_path} ---")
                    print(full_path.read_text(encoding="utf-8"))

            # 运行修复后的代码验证
            print("\n运行修复后的代码验证...")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(test_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            print(f"退出码: {proc.returncode}")
            if stdout:
                print("输出:")
                print(stdout.decode('utf-8', errors='replace'))
            if stderr:
                print("错误:")
                print(stderr.decode('utf-8', errors='replace'))


if __name__ == "__main__":
    # 运行语法错误测试
    asyncio.run(test_local_syntax_error_fix())

    # 运行运行时错误测试
    # asyncio.run(test_local_runtime_error_fix())
