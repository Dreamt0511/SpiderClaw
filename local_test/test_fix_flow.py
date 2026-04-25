#!/usr/bin/env python3
"""本地修复流程测试脚本"""
import os
import sys
import subprocess
import tempfile
import json
import yaml
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.orchestrator import RepairOrchestrator
from src.bus.schemas import GitHubEvent
from src.agent.tools.langchain_tools import set_tool_context, clone_repository

# 读取配置文件
config_path = Path(__file__).parent.parent / "config" / "agent-config.yaml"
with open(config_path, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

def run_test_file(file_path: str) -> str:
    """运行测试文件，捕获错误输出"""
    try:
        result = subprocess.run(
            [sys.executable, file_path],
            capture_output=True,
            text=True,
            timeout=10
        )
        # 合并stdout和stderr
        output = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        return output
    except subprocess.TimeoutExpired:
        return "Error: 运行超时"
    except Exception as e:
        return f"Error: 运行失败: {str(e)}"

def create_mock_github_event(repo_path: str, branch: str, ci_logs: str) -> GitHubEvent:
    """创建模拟的GitHub事件"""
    return GitHubEvent(
        event_id=f"test_{os.urandom(4).hex()}",
        event_type="workflow_run",
        action="completed",
        source="local_test",
        repository="test/test_repo",
        signature_valid=True,
        payload={},
        clone_url=f"file://{repo_path}",
        branch=branch,
        conclusion="failure",
        logs_url=""  # 本地测试不需要下载日志
    )

async def test_fix_flow(test_file: str, description: str):
    """测试修复流程"""
    print(f"\n{'='*60}")
    print(f"测试: {description}")
    print(f"文件: {test_file}")
    print('='*60)

    # 1. 创建临时测试仓库
    with tempfile.TemporaryDirectory(prefix="spiderclaw_test_") as temp_dir:
        print(f"临时仓库目录: {temp_dir}")

        # 初始化git仓库
        subprocess.run(["git", "init"], cwd=temp_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=temp_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=temp_dir, capture_output=True)

        # 复制测试文件到临时仓库
        test_file_name = os.path.basename(test_file)
        dest_file = os.path.join(temp_dir, test_file_name)
        with open(test_file, 'r', encoding='utf-8') as src, open(dest_file, 'w', encoding='utf-8') as dst:
            dst.write(src.read())

        # 提交文件
        subprocess.run(["git", "add", test_file_name], cwd=temp_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add test file"], cwd=temp_dir, capture_output=True)
        # 创建main分支
        subprocess.run(["git", "branch", "-M", "main"], cwd=temp_dir, capture_output=True)

        # 2. 运行测试文件，获取错误日志
        print("\n1. 运行测试文件，获取错误日志...")
        ci_logs = run_test_file(dest_file)
        print(f"错误日志预览:\n{ci_logs[:500]}...")

        # 3. 创建模拟事件
        event = create_mock_github_event(temp_dir, "main", ci_logs)

        # 4. 初始化编排器
        print("\n2. 初始化修复编排器...")
        orchestrator = RepairOrchestrator(
            github_token=config["github"]["token"],
            openai_api_key=config["openai"]["api_key"],
            openai_base_url=config["openai"]["base_url"],
            llm_model=config["openai"]["model_name"]
        )

        # 5. 运行完整修复流程
        print("\n3. 运行完整修复流程（修复-审查-测试）...")
        from src.agent.subagents.fix_agent import FixAgent
        from src.agent.subagents.review_agent import ReviewAgent
        from src.agent.subagents.test_agent import TestAgent
        from src.agent.tools.langchain_tools import parse_python_errors, write_file, get_diff

        # 解析错误
        error_locations = parse_python_errors.invoke({"log_content": ci_logs})
        print(f"解析到错误数量: {len(error_locations)}")
        for i, err in enumerate(error_locations):
            print(f"  错误{i+1}: {err.get('file_path', 'unknown')}:{err.get('line_number', 0)} {err.get('error_type', 'UnknownError')}: {err.get('error_message', '')[:100]}")

        # 设置工具上下文
        set_tool_context({
            "repo_path": temp_dir,
            "github_token": config["github"]["token"]
        })

        # ========== 第一步：修复Agent生成修复 ==========
        print("\n4. 调用修复Agent生成修复...")
        try:
            # 创建修复Agent
            fix_agent = FixAgent(
                repo_path=temp_dir,
                llm_model=config["openai"]["model_name"],
                openai_api_key=config["openai"]["api_key"],
                openai_base_url=config["openai"]["base_url"]
            )

            # 生成修复
            fix_result = await fix_agent.generate_fix(
                ci_logs=ci_logs,
                error_locations=error_locations
            )

            print(f"\n修复结果:")
            print(f"  修复描述: {fix_result.get('fix_description', '无')}")
            print(f"  修改文件: {fix_result.get('modified_files', [])}")

            if not fix_result.get("code_changes"):
                print("[ERROR] 没有生成修复代码")
                return

            # 先读取所有原始文件内容，生成diff，然后进行审查（此时文件还是原始内容）
            import difflib
            diff_content = ""
            original_contents = {}

            # 先读取所有原始文件内容
            for file_path in fix_result["code_changes"].keys():
                original_file_path = os.path.join(temp_dir, file_path)
                with open(original_file_path, 'r', encoding='utf-8') as f:
                    original_contents[file_path] = f.read()

            # 生成diff
            for file_path, new_content in fix_result["code_changes"].items():
                original_content = original_contents[file_path]
                diff = difflib.unified_diff(
                    original_content.splitlines(),
                    new_content.splitlines(),
                    fromfile=file_path,
                    tofile=file_path,
                    lineterm=''
                )
                diff_content += '\n'.join(diff) + '\n'

            # 显示修复内容
            for file_path, content in fix_result["code_changes"].items():
                print(f"\n  {file_path} 的修复内容:")
                print("-" * 40)
                print(content[:500] + ("..." if len(content) > 500 else ""))
                print("-" * 40)

            print(f"\nDiff内容:\n{diff_content[:500]}...")

            # ========== 第二步：审查Agent审查修复 ==========
            print("\n5. 调用审查Agent审查修复...")
            review_agent = ReviewAgent(
                llm_model=config["openai"]["model_name"],
                openai_api_key=config["openai"]["api_key"],
                openai_base_url=config["openai"]["base_url"],
                max_change_lines=20
            )

            review_result = await review_agent.review_changes(
                error_locations=error_locations,
                fix_description=fix_result["fix_description"],
                modified_files=fix_result["modified_files"],
                code_changes=fix_result["code_changes"],
                diff_content=diff_content,
                repo_path=temp_dir
            )

            # 审查通过后再应用修复到本地文件
            print(f"审查结果: {'通过' if review_result.get('review_passed', False) else '不通过'}")
            print(f"审查意见: {review_result.get('review_comments', '无')}")
            if review_result.get("risk_warnings"):
                print(f"风险警告: {review_result.get('risk_warnings')}")

            if not review_result.get("review_passed", False):
                print("[ERROR] 修复未通过审查")
                return

            # 审查通过，应用修复到本地文件
            print("\n审查通过，应用修复到本地文件...")
            for file_path, content in fix_result["code_changes"].items():
                # 写入修复后的文件
                full_file_path = os.path.join(temp_dir, file_path)
                write_result = write_file.invoke({
                    "file_path": file_path,
                    "content": content
                })
                if write_result != "Success":
                    print(f"[ERROR] 写入文件失败: {write_result}")
                    return

            print(f"审查结果: {'通过' if review_result.get('review_passed', False) else '不通过'}")
            print(f"审查意见: {review_result.get('review_comments', '无')}")
            if review_result.get("risk_warnings"):
                print(f"风险警告: {review_result.get('risk_warnings')}")

            if not review_result.get("review_passed", False):
                print("[ERROR] 修复未通过审查")
                return

            # ========== 第三步：测试Agent验证修复 ==========
            print("\n6. 调用测试Agent验证修复...")
            test_agent = TestAgent(
                repo_path=temp_dir,
                llm_model=config["openai"]["model_name"],
                openai_api_key=config["openai"]["api_key"],
                openai_base_url=config["openai"]["base_url"],
                test_command="python"
            )

            test_result = await test_agent.verify_fix(
                error_locations=error_locations,
                fix_description=fix_result["fix_description"],
                diff_content=diff_content
            )

            print(f"测试结果: {'通过' if test_result.get('test_passed', False) else '不通过'}")
            print(f"测试输出: {test_result.get('verification_summary', '无')}")
            if test_result.get("failed_tests"):
                print(f"失败测试: {test_result.get('failed_tests')}")

            if not test_result.get("test_passed", False):
                print("[ERROR] 修复未通过测试")
                return

            # ========== 最终验证 ==========
            print("\n7. 最终验证修复结果...")
            # 运行修复后的文件
            repaired_file = os.path.join(temp_dir, list(fix_result["code_changes"].keys())[0])
            result = subprocess.run(
                [sys.executable, repaired_file],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                print("[OK] 完整流程测试通过！修复成功")
                print(f"   运行输出: {result.stdout.strip()}")
            else:
                print("[ERROR] 修复失败！代码仍然有错误")
                print(f"   错误信息: {result.stderr.strip()}")

        except Exception as e:
            print(f"[ERROR] 流程执行出错: {str(e)}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    import asyncio

    # 测试语法错误2：引号不闭合
    # asyncio.run(test_fix_flow(
    #     "local_test/test_syntax_error_2.py",
    #     "语法错误 - 字符串引号不闭合"
    # ))

    # 测试语法错误3：缩进错误
    # asyncio.run(test_fix_flow(
    #     "local_test/test_syntax_error_3.py",
    #     "语法错误 - 缩进错误"
    # ))

    # 测试运行时错误1：索引越界
    # asyncio.run(test_fix_flow(
    #     "local_test/test_runtime_error_1.py",
    #     "运行时错误 - 列表索引越界"
    # ))

    # 测试运行时错误2：除以零
    asyncio.run(test_fix_flow(
        "local_test/test_runtime_error_2.py",
        "运行时错误 - 除以零"
    ))
