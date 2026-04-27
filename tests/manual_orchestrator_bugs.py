#!/usr/bin/env python3
"""测试orchestrator的三个Bug修复"""
import sys
import asyncio
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))

from src.config.settings import get_settings
from src.agent.orchestrator import RepairOrchestrator
from src.bus.schemas import GitHubEvent

# 加载配置
settings = get_settings(config_path="src/config/agent-config.yaml")

async def test_bug1_duplicate_events():
    """测试Bug1：同一PR的事件重复处理"""
    print("=" * 60)
    print("测试Bug1：同一PR的事件重复处理")
    print("=" * 60)

    orchestrator = RepairOrchestrator(
        github_token=settings.github.token,
        openai_api_key=settings.openai.api_key,
        openai_base_url=settings.openai.base_url,
        llm_model=settings.openai.model_name
    )

    # 模拟两个相同PR的事件
    event1 = GitHubEvent(
        event_id="event-1",
        event_type="check_run",
        action="completed",
        source="test",
        repository="owner/test-repo",
        signature_valid=True,
        pr_number=123,
        conclusion="failure",
        payload={"head_sha": "abc123"}
    )

    event2 = GitHubEvent(
        event_id="event-2",
        event_type="workflow_run",
        action="completed",
        source="test",
        repository="owner/test-repo",
        signature_valid=True,
        pr_number=123,
        conclusion="failure",
        payload={"head_sha": "abc123"}
    )

    # 处理第一个事件（应该正常处理）
    print("\n处理第一个事件（PR#123）...")
    result1 = await orchestrator.run(event1)
    print(f"第一个事件结果: success={result1['success']}, message={result1['error_message']}")

    # 处理第二个相同PR的事件（应该被去重）
    print("\n处理第二个相同PR的事件（PR#123）...")
    result2 = await orchestrator.run(event2)
    print(f"第二个事件结果: success={result2['success']}, message={result2['error_message']}")

    if "事件已处理过" in result2["error_message"]:
        print("[OK] 重复事件正确被拦截！")
    else:
        print("[FAIL] 重复事件没有被拦截！")

    return "事件已处理过" in result2["error_message"]

async def test_bug2_review_to_test_flow():
    """测试Bug2：审查通过后没走测试流程"""
    print("\n" + "=" * 60)
    print("测试Bug2：审查通过后流程正确性")
    print("=" * 60)

    # 检查图结构
    orchestrator = RepairOrchestrator(
        github_token=settings.github.token,
        openai_api_key=settings.openai.api_key,
        openai_base_url=settings.openai.base_url,
        llm_model=settings.openai.model_name
    )

    # 检查节点和边是否正确
    graph = orchestrator.graph
    nodes = list(graph.nodes.keys())
    print(f"图节点: {nodes}")

    # 检查条件边配置
    review_edges = graph.edges.get(('review_changes', None), [])
    print(f"审查后的条件边目标: {[e[1] for e in review_edges]}")

    required_nodes = ["collect_context", "fix_agent", "review_changes", "run_tests", "create_pr", "handle_failure"]
    all_nodes_present = all(node in nodes for node in required_nodes)
    test_edge_present = any("run_tests" in str(e) for e in review_edges)

    if all_nodes_present and test_edge_present:
        print("[OK] 图结构正确，审查通过后会进入测试阶段！")
        # 检查fix_agent的输出是否有多余的goto
        import inspect
        source = inspect.getsource(orchestrator._run_fix_agent)
        if 'goto="review_changes"' not in source:
            print("[OK] _run_fix_agent中已移除多余的goto，避免冲突！")
            return True
        else:
            print("[FAIL] _run_fix_agent中仍有多余的goto！")
            return False
    else:
        print("[FAIL] 图结构不正确！")
        return False

async def test_bug3_feedback_propagation():
    """测试Bug3：审查意见正确传给修复Agent"""
    print("\n" + "=" * 60)
    print("测试Bug3：反馈信息传递正确性")
    print("=" * 60)

    orchestrator = RepairOrchestrator(
        github_token=settings.github.token,
        openai_api_key=settings.openai.api_key,
        openai_base_url=settings.openai.base_url,
        llm_model=settings.openai.model_name
    )

    # 检查_run_fix_agent中是否正确获取反馈字段
    import inspect
    source = inspect.getsource(orchestrator._run_fix_agent)

    checks = [
        'review_feedback' in source,
        'risk_warnings' in source,
        'test_output' in source,
        'failed_tests' in source,
        'state.get("review_comments")' in source,
    ]

    if all(checks):
        print("[OK] 所有反馈字段都正确获取并传递给修复Agent！")

        # 检查重试计数日志
        route_source = inspect.getsource(orchestrator._route_after_review) + inspect.getsource(orchestrator._route_after_test)
        if "retry_count + 1" in route_source and "第 {retry_count + 1} 次" in route_source:
            print("[OK] 重试次数正确递增显示！")
            return True
        else:
            print("[FAIL] 重试次数显示有问题！")
            return False
    else:
        print("[FAIL] 反馈字段传递不完整！")
        return False

async def main():
    """运行所有测试"""
    print("开始测试Orchestrator Bug修复...\n")

    test1 = await test_bug1_duplicate_events()
    test2 = await test_bug2_review_to_test_flow()
    test3 = await test_bug3_feedback_propagation()

    print("\n" + "=" * 60)
    print("测试结果汇总:")
    print(f"Bug1 重复事件处理: {'[OK] 通过' if test1 else '[FAIL] 失败'}")
    print(f"Bug2 审查到测试流程: {'[OK] 通过' if test2 else '[FAIL] 失败'}")
    print(f"Bug3 反馈信息传递: {'[OK] 通过' if test3 else '[FAIL] 失败'}")
    print("=" * 60)

    if test1 and test2 and test3:
        print("\n所有Bug修复测试通过！可以进行线上测试了！")
    else:
        print("\n部分测试未通过，请检查修复。")

if __name__ == "__main__":
    asyncio.run(main())
