#!/usr/bin/env python3
"""Agent工作流测试脚本"""
import sys
import asyncio
import os
import tempfile
import shutil
from pathlib import Path
from git import Repo

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))

from src.config.settings import get_settings
from src.agent.tools.langchain_tools import parse_python_errors
from src.agent.subagents.fix_agent import FixAgent
from src.agent.subagents.review_agent import ReviewAgent
from src.agent.subagents.test_agent import TestAgent

# 从配置文件加载配置
settings = get_settings(config_path="config/agent-config.yaml")
OPENAI_API_KEY = settings.openai.api_key
OPENAI_BASE_URL = settings.openai.base_url
LLM_MODEL = settings.openai.model_name

print(f"加载配置成功:")
print(f"  模型: {LLM_MODEL}")
print(f"  API地址: {OPENAI_BASE_URL}")
print(f"  API密钥长度: {len(OPENAI_API_KEY)}")

async def test_empty_context():
    """测试空上下文场景（问题1验证）"""
    print("=" * 60)
    print("测试场景1：空上下文拦截")
    print("=" * 60)

    # 创建临时仓库
    with tempfile.TemporaryDirectory() as temp_dir:
        # 初始化git仓库
        repo = Repo.init(temp_dir)
        # 复制测试文件
        shutil.copy("test_cases/simple_bug/demo_bug.py", temp_dir)
        shutil.copy("test_cases/simple_bug/test_demo_bug.py", temp_dir)
        # 初始提交
        repo.index.add(["demo_bug.py", "test_demo_bug.py"])
        repo.index.commit("Initial commit")

        # 测试：空CI日志
        print("\n测试1：空CI日志")
        try:
            fix_agent = FixAgent(
                repo_path=temp_dir,
                llm_model=LLM_MODEL,
                openai_api_key=OPENAI_API_KEY,
                openai_base_url=OPENAI_BASE_URL
            )
            result = await fix_agent.generate_fix(
                ci_logs="",
                error_locations=[{"file_path": "demo_bug.py", "line_number": 2, "error_type": "ZeroDivisionError", "error_message": "division by zero"}]
            )
            print(f"修复结果: {result}")
        except Exception as e:
            print(f"预期错误（空CI日志应该被拦截）: {e}")

        # 测试：空错误信息
        print("\n测试2：空错误信息")
        try:
            with open("test_cases/simple_bug/ci_logs.txt", "r", encoding="utf-8") as f:
                ci_logs = f.read()

            fix_agent = FixAgent(
                repo_path=temp_dir,
                llm_model=LLM_MODEL,
                openai_api_key=OPENAI_API_KEY,
                openai_base_url=OPENAI_BASE_URL
            )
            result = await fix_agent.generate_fix(
                ci_logs=ci_logs,
                error_locations=[]
            )
            print(f"修复结果: {result}")
        except Exception as e:
            print(f"预期错误（空错误信息应该被拦截）: {e}")

    print("\n✅ 空上下文测试完成")

async def test_full_repair_flow():
    """测试完整修复流程"""
    print("\n" + "=" * 60)
    print("测试场景2：完整修复流程")
    print("=" * 60)

    # 创建临时仓库
    with tempfile.TemporaryDirectory() as temp_dir:
        # 初始化git仓库
        repo = Repo.init(temp_dir)
        # 复制测试文件
        shutil.copy("test_cases/simple_bug/demo_bug.py", temp_dir)
        shutil.copy("test_cases/simple_bug/test_demo_bug.py", temp_dir)
        # 初始提交
        repo.index.add(["demo_bug.py", "test_demo_bug.py"])
        repo.index.commit("Initial commit")

        # 1. 解析错误
        print("\n1. 解析CI日志中的错误...")
        with open("test_cases/simple_bug/ci_logs.txt", "r", encoding="utf-8") as f:
            ci_logs = f.read()

        error_locations = parse_python_errors.invoke({"log_content": ci_logs})
        print(f"解析到错误: {len(error_locations)} 个")
        for err in error_locations:
            print(f"  - {err['file_path']}:{err['line_number']} {err['error_type']}: {err['error_message']}")

        # 2. 生成修复
        print("\n2. 调用修复Agent生成修复...")
        fix_agent = FixAgent(
            repo_path=temp_dir,
            llm_model=LLM_MODEL,
            openai_api_key=OPENAI_API_KEY,
            openai_base_url=OPENAI_BASE_URL
        )
        fix_result = await fix_agent.generate_fix(
            ci_logs=ci_logs,
            error_locations=error_locations
        )
        print(f"修复描述: {fix_result.get('fix_description')}")
        print(f"修改文件: {fix_result.get('modified_files', [])}")
        print(f"代码变更: {list(fix_result.get('code_changes', {}).keys())}")

        if not fix_result.get("code_changes"):
            print("❌ 修复失败，没有生成代码变更")
            return

        # 应用修复
        for file_path, content in fix_result["code_changes"].items():
            full_path = os.path.join(temp_dir, file_path)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"已应用修复到: {file_path}")

        # 3. 审查修复
        print("\n3. 调用审查Agent审查修复...")
        review_agent = ReviewAgent(
            llm_model=LLM_MODEL,
            openai_api_key=OPENAI_API_KEY,
            openai_base_url=OPENAI_BASE_URL,
            max_change_lines=20
        )

        # 构造diff
        diff_content = repo.git.diff()
        print(f"变更行数: {len(diff_content.split('\\n'))} 行")

        review_result = await review_agent.review_changes(
            error_locations=error_locations,
            fix_description=fix_result["fix_description"],
            modified_files=fix_result["modified_files"],
            code_changes=fix_result["code_changes"],
            diff_content=diff_content,
            repo_path=temp_dir
        )
        print(f"审查通过: {review_result['review_passed']}")
        print(f"审查意见: {review_result.get('review_comments')}")
        print(f"风险警告: {review_result.get('risk_warnings', [])}")

        if not review_result["review_passed"]:
            print("❌ 审查不通过")
            return

        # 4. 测试修复
        print("\n4. 调用测试Agent验证修复...")
        test_agent = TestAgent(
            repo_path=temp_dir,
            llm_model=LLM_MODEL,
            openai_api_key=OPENAI_API_KEY,
            openai_base_url=OPENAI_BASE_URL,
            test_command="pytest test_demo_bug.py -v"
        )
        test_result = await test_agent.verify_fix(
            error_locations=error_locations,
            fix_description=fix_result["fix_description"],
            diff_content=diff_content
        )
        print(f"测试通过: {test_result['test_passed']}")
        print(f"测试输出: {test_result.get('test_output', '')[:500]}...")
        print(f"失败测试: {test_result.get('failed_tests', [])}")

        if test_result["test_passed"]:
            print("✅ 完整流程测试通过！")
        else:
            print("❌ 测试不通过")

async def test_review_feedback_retry():
    """测试审查不通过时的反馈重试"""
    print("\n" + "=" * 60)
    print("测试场景3：审查不通过反馈重试")
    print("=" * 60)

    # 创建临时仓库
    with tempfile.TemporaryDirectory() as temp_dir:
        # 初始化git仓库
        repo = Repo.init(temp_dir)
        # 复制测试文件
        shutil.copy("test_cases/simple_bug/demo_bug.py", temp_dir)
        shutil.copy("test_cases/simple_bug/test_demo_bug.py", temp_dir)
        # 初始提交
        repo.index.add(["demo_bug.py", "test_demo_bug.py"])
        repo.index.commit("Initial commit")

        # 解析错误
        with open("test_cases/simple_bug/ci_logs.txt", "r", encoding="utf-8") as f:
            ci_logs = f.read()
        error_locations = parse_python_errors.invoke({"log_content": ci_logs})

        # 模拟一个有问题的修复（比如只处理b==0，但返回0而不是None，不符合测试期望）
        bad_fix = {
            "demo_bug.py": """def divide(a, b):
    if b == 0:
        return 0
    return a / b

def main():
    result = divide(10, 0)
    print(f"Result: {result}")

if __name__ == "__main__":
    main()
"""
        }

        # 应用错误修复
        for file_path, content in bad_fix.items():
            full_path = os.path.join(temp_dir, file_path)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

        diff_content = repo.git.diff()

        # 审查错误的修复
        review_agent = ReviewAgent(
            llm_model=LLM_MODEL,
            openai_api_key=OPENAI_API_KEY,
            openai_base_url=OPENAI_BASE_URL,
            max_change_lines=20
        )

        review_result = await review_agent.review_changes(
            error_locations=error_locations,
            fix_description="修复除以零错误，返回0",
            modified_files=["demo_bug.py"],
            code_changes=bad_fix,
            diff_content=diff_content,
            repo_path=temp_dir
        )

        print(f"审查结果: 通过={review_result['review_passed']}")
        print(f"审查意见: {review_result.get('review_comments')}")

        if not review_result["review_passed"]:
            # 使用审查反馈重试修复
            print("\n使用审查反馈重试修复...")
            fix_agent = FixAgent(
                repo_path=temp_dir,
                llm_model=LLM_MODEL,
                openai_api_key=OPENAI_API_KEY,
                openai_base_url=OPENAI_BASE_URL
            )
            retry_result = await fix_agent.generate_fix(
                ci_logs=ci_logs,
                error_locations=error_locations,
                review_feedback=review_result["review_comments"],
                risk_warnings=review_result["risk_warnings"]
            )
            print(f"重试修复描述: {retry_result.get('fix_description')}")
            print("✅ 审查反馈重试测试完成")

async def test_test_feedback_retry():
    """测试测试不通过时的反馈重试"""
    print("\n" + "=" * 60)
    print("测试场景4：测试不通过反馈重试")
    print("=" * 60)

    # 创建临时仓库
    with tempfile.TemporaryDirectory() as temp_dir:
        # 初始化git仓库
        repo = Repo.init(temp_dir)
        # 复制测试文件
        shutil.copy("test_cases/simple_bug/demo_bug.py", temp_dir)
        shutil.copy("test_cases/simple_bug/test_demo_bug.py", temp_dir)
        # 初始提交
        repo.index.add(["demo_bug.py", "test_demo_bug.py"])
        repo.index.commit("Initial commit")

        # 解析错误
        with open("test_cases/simple_bug/ci_logs.txt", "r", encoding="utf-8") as f:
            ci_logs = f.read()
        error_locations = parse_python_errors.invoke({"log_content": ci_logs})

        # 模拟一个不完整的修复
        bad_fix = {
            "demo_bug.py": """def divide(a, b):
    return a / b

def main():
    result = divide(10, 0)
    print(f"Result: {result}")

if __name__ == "__main__":
    main()
"""
        }

        # 应用错误修复（等于没改）
        for file_path, content in bad_fix.items():
            full_path = os.path.join(temp_dir, file_path)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

        # 运行测试得到失败结果
        test_agent = TestAgent(
            repo_path=temp_dir,
            llm_model=LLM_MODEL,
            openai_api_key=OPENAI_API_KEY,
            openai_base_url=OPENAI_BASE_URL,
            test_command="pytest test_demo_bug.py -v"
        )
        test_result = await test_agent.verify_fix(
            error_locations=error_locations,
            fix_description="尝试修复除以零错误",
            diff_content=""
        )

        print(f"测试结果: 通过={test_result['test_passed']}")
        print(f"失败测试: {test_result.get('failed_tests', [])}")

        if not test_result["test_passed"]:
            # 使用测试反馈重试修复
            print("\n使用测试反馈重试修复...")
            fix_agent = FixAgent(
                repo_path=temp_dir,
                llm_model=LLM_MODEL,
                openai_api_key=OPENAI_API_KEY,
                openai_base_url=OPENAI_BASE_URL
            )
            retry_result = await fix_agent.generate_fix(
                ci_logs=ci_logs,
                error_locations=error_locations,
                test_output=test_result["test_output"],
                failed_tests=test_result["failed_tests"]
            )
            print(f"重试修复描述: {retry_result.get('fix_description')}")
            print("✅ 测试反馈重试测试完成")

if __name__ == "__main__":
    if not OPENAI_API_KEY:
        print("请在config/agent-config.yaml中配置openai.api_key")
        sys.exit(1)

    asyncio.run(test_empty_context())
    asyncio.run(test_full_repair_flow())
    asyncio.run(test_review_feedback_retry())
    asyncio.run(test_test_feedback_retry())
    print("\n🎉 所有测试场景执行完成！")
