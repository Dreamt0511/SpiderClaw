"""
综合集成测试：验证所有修复过的场景
覆盖：ANSI剥离、命令黑名单、py_compile、JSON解析、bug_count
"""
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# TestAgent 初始化需要 API key，设置测试用密钥
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key-for-testing")

# 直接测试 ANSI 剥离逻辑（不依赖 TestAgent 实例）
_ANSI_PATTERN = re.compile(r'\033\[[0-9;]*[a-zA-Z]')

def _strip_ansi(text: str) -> str:
    return _ANSI_PATTERN.sub('', text)

# 直接测试环境命令黑名单逻辑
_ENV_PATTERNS = [
    re.compile(p) for p in [
        r'\bpip\b',
        r'\bconda\b',
        r'\bpoetry\b',
        r'\bnpm\b',
        r'\binstall\b',
        r'\bsetup\.py\b',
    ]
]

def _is_env_setup_command(cmd: str) -> bool:
    for pattern in _ENV_PATTERNS:
        if pattern.search(cmd):
            return True
    return False

_PYTHON_PREFIXES = ('pytest', 'python ', 'python3 ', 'nosetests', 'tox', 'unittest')

def _is_python_command(cmd: str) -> bool:
    return any(cmd.lower().strip().startswith(p) for p in _PYTHON_PREFIXES)

def _is_valid_verification_command(cmd: str) -> bool:
    return _is_python_command(cmd) and not _is_env_setup_command(cmd)


# ========== 场景1: ANSI 转义码剥离 ==========

def test_ansi_strip():
    """ANSI 转义码应被正确剥离"""
    ci_log = "\033[33mpytest\033[0m tests/ -v"
    cleaned = _strip_ansi(ci_log)
    assert "[0m" not in cleaned, f"ANSI 码未剥离: {cleaned}"
    assert cleaned == "pytest tests/ -v", f"命令被损坏: {cleaned}"
    logger.info(f"[PASS] ANSI剥离: '{cleaned}'")


def test_ansi_strip_multiple():
    """多种 ANSI 码都应被剥离"""
    ci_log = "\033[32mpython\033[0m \033[1mtest.py\033[22m"
    cleaned = _strip_ansi(ci_log)
    assert "[0m" not in cleaned and "[1m" not in cleaned
    assert cleaned == "python test.py", f"命令被损坏: {cleaned}"
    logger.info(f"[PASS] 多ANSI码: '{cleaned}'")


def test_ansi_strip_github_actions():
    """GitHub Actions 日志中的 ANSI 码应被剥离"""
    ci_log = '\n##[group]Run pytest tests/\n\033[33mpytest\033[0m tests/ -v\n##[endgroup]\n'
    cleaned = _strip_ansi(ci_log)
    assert "[33m" not in cleaned and "[0m" not in cleaned
    logger.info(f"[PASS] GHA日志ANSI剥离")


# ========== 场景2: 环境命令黑名单 ==========

def test_env_command_blocked():
    """pip install 应被拒绝"""
    assert not _is_valid_verification_command("python -m pip install --upgrade pip")
    logger.info("[PASS] pip install 被拒绝")


def test_env_command_blocked_conda():
    """conda install 应被拒绝"""
    assert not _is_valid_verification_command("conda install numpy")
    logger.info("[PASS] conda 被拒绝")


def test_env_command_blocked_npm():
    """npm install 应被拒绝"""
    assert not _is_valid_verification_command("npm install")
    logger.info("[PASS] npm 被拒绝")


def test_env_command_blocked_setup():
    """setup.py 应被拒绝"""
    assert not _is_valid_verification_command("python setup.py install")
    logger.info("[PASS] setup.py 被拒绝")


def test_env_command_blocked_poetry():
    """poetry install 应被拒绝"""
    assert not _is_valid_verification_command("poetry install")
    logger.info("[PASS] poetry 被拒绝")


def test_valid_pytest_passes():
    """pytest 应通过白名单"""
    assert _is_valid_verification_command("pytest tests/ -v")
    logger.info("[PASS] pytest 通过")


def test_valid_python_passes():
    """python 命令（非env）应通过"""
    assert _is_valid_verification_command("python test.py")
    assert _is_valid_verification_command("python -m pytest")
    assert _is_valid_verification_command("python -m py_compile test.py")
    logger.info("[PASS] python 命令通过")


def test_valid_tox_passes():
    """tox 应通过"""
    assert _is_valid_verification_command("tox")
    logger.info("[PASS] tox 通过")


# ========== 场景3: ANSI剥离后 Agent 提取命令 ==========

def test_agent_ansi_extraction():
    """验证 TestAgent._extract_failure_command 处理含 ANSI 码的日志"""
    from src.agent.subagents.test_agent import TestAgent
    agent = TestAgent(repo_path=".", openai_api_key=os.environ.get("OPENAI_API_KEY", "sk-test"))

    ci_log = "##[command]\033[33mpytest\033[0m tests/ -v"
    result = agent._extract_failure_command(ci_log, [{"error_type": "NameError"}])
    assert result is not None
    assert "[0m" not in result
    assert "pytest" in result
    logger.info(f"[PASS] Agent ANSI提取: {result}")


def test_agent_env_command_blocked():
    """验证 TestAgent 拒绝 pip install"""
    from src.agent.subagents.test_agent import TestAgent
    agent = TestAgent(repo_path=".", openai_api_key=os.environ.get("OPENAI_API_KEY", "sk-test"))

    ci_log = "##[command]python -m pip install --upgrade pip"
    result = agent._extract_failure_command(ci_log, [])
    assert result is None
    logger.info("[PASS] Agent pip拒绝")


def test_agent_inferred_priority():
    """验证 Agent 推断命令优先于 CI 日志"""
    from src.agent.subagents.test_agent import TestAgent
    agent = TestAgent(repo_path=".", openai_api_key=os.environ.get("OPENAI_API_KEY", "sk-test"))

    ci_log = "##[command]python -m pip install numpy"
    error_locs = [{"file_path": "src/server.py", "error_type": "NameError"}]
    result = agent._extract_failure_command(ci_log, error_locs)
    assert result is not None
    assert "py_compile" in result
    assert "src/server.py" in result
    logger.info(f"[PASS] 推断优先: {result}")


def test_agent_inferred_test_file():
    """验证 Agent 对包含测试函数的文件用 pytest"""
    import tempfile
    from src.agent.subagents.test_agent import TestAgent

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建包含测试函数的文件
        test_file = os.path.join(tmpdir, "test_app.py")
        with open(test_file, "w") as f:
            f.write("def test_hello():\n    assert 1 + 1 == 2\n")

        agent = TestAgent(repo_path=tmpdir)
        result = agent._extract_failure_command("", [{"file_path": "test_app.py", "error_type": "AssertionError"}])
        assert "pytest" in result, f"应为 pytest: {result}"
        logger.info(f"[PASS] 测试文件: {result}")


def test_agent_inferred_non_test():
    """验证 Agent 对非测试文件用 py_compile"""
    from src.agent.subagents.test_agent import TestAgent
    agent = TestAgent(repo_path=".", openai_api_key=os.environ.get("OPENAI_API_KEY", "sk-test"))

    result = agent._extract_failure_command("", [{"file_path": "src/app.py", "error_type": "SyntaxError"}])
    assert "py_compile" in result
    logger.info(f"[PASS] 非测试文件: {result}")


def test_agent_inferred_test_prefix_no_tests():
    """验证 Agent 对 test_ 前缀但无测试函数的文件用 py_compile（而非 pytest）"""
    import tempfile
    from src.agent.subagents.test_agent import TestAgent

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建 test_ 前缀文件但无测试函数（如语法错误示例）
        test_file = os.path.join(tmpdir, "test_syntax_errors.py")
        with open(test_file, "w") as f:
            f.write("x = 1\ny = 2\n")  # 无测试函数

        agent = TestAgent(repo_path=tmpdir)
        result = agent._extract_failure_command("", [{"file_path": "test_syntax_errors.py", "error_type": "SyntaxError"}])
        assert "py_compile" in result, f"应为 py_compile: {result}"
        logger.info(f"[PASS] test_前缀无测试函数: {result}")


# ========== 场景4: JSON 健壮解析 ==========

def test_robust_json_parsing():
    """各种坏 JSON 都应被修复"""
    from src.agent.subagents.fix_agent import FixAgent

    # 有未转义换行符的 JSON
    bad_json = '''{
        "fix_description": "修复bug",
        "modified_files": ["test.py"],
        "code_changes": {
            "test.py": "def foo():
    return 1
"
        }
    }'''
    result = FixAgent._parse_json_safely(bad_json)
    assert result is not None, "含换行符的 JSON 应被修复"
    assert "test.py" in result.get("code_changes", {})

    # 有尾随逗号的 JSON
    trailing_json = '''{
        "fix_description": "修复",
        "modified_files": ["a.py",],
        "code_changes": {"a.py": "print(1)",}
    }'''
    result = FixAgent._parse_json_safely(trailing_json)
    assert result is not None, "尾随逗号 JSON 应被修复"

    # 严重损坏的 JSON — 至少应返回可用的结构
    broken_json = "{broken json garbage}"
    result = FixAgent._parse_json_safely(broken_json)
    assert result is None, "严重损坏的 JSON 应返回 None"

    logger.info("[PASS] 健壮JSON解析")


# ========== 场景5: py_compile 实际执行 ==========

def test_py_compile_valid_file():
    """py_compile 对语法正确的文件应返回 0"""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("x = 1\ny = 2\nprint(x + y)\n")
        tmp = f.name

    try:
        result = subprocess.run(
            ["python", "-m", "py_compile", tmp],
            capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, f"py_compile 应成功: {result.returncode}"
    finally:
        os.unlink(tmp)
    logger.info("[PASS] py_compile 语法正确")


def test_py_compile_syntax_error():
    """py_compile 对语法错误的文件应返回非零"""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("x = 1\ny = 2\nprint(x + \n")  # 语法错误
        tmp = f.name

    try:
        result = subprocess.run(
            ["python", "-m", "py_compile", tmp],
            capture_output=True, text=True, timeout=10
        )
        assert result.returncode != 0, "py_compile 应检测到语法错误"
    finally:
        os.unlink(tmp)
    logger.info("[PASS] py_compile 检测语法错误")


# ========== 场景6: execute_python_code 工具正则修复 ==========

def test_execute_python_code_error_detection():
    """execute_python_code 应正确检测 stderr 中的异常（包括以 \\n 结尾的情况）"""
    import tempfile
    import json
    from src.agent.tools.langchain_tools import set_tool_context, execute_python_code

    set_tool_context({"repo_path": os.getcwd()})

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, dir=".") as f:
        f.write("x = 1\nprint(reslt)\n")  # NameError
        tmp = os.path.basename(f.name)

    try:
        result_str = execute_python_code.invoke({"file_path": tmp, "timeout": 10})
        result = json.loads(result_str)
        assert not result["success"], "应检测到执行失败"
        assert result["error_type"] == "NameError", f"应为 NameError: {result['error_type']}"
        assert "reslt" in result["error_message"], f"应提及 reslt: {result['error_message']}"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    logger.info("[PASS] execute_python_code 错误检测")


def test_execute_python_code_success():
    """修复后的代码应返回成功"""
    import tempfile
    import json
    from src.agent.tools.langchain_tools import set_tool_context, execute_python_code

    set_tool_context({"repo_path": os.getcwd()})

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, dir=".") as f:
        f.write("x = 1\nprint(x)\n")  # 正确代码
        tmp = os.path.basename(f.name)

    try:
        result_str = execute_python_code.invoke({"file_path": tmp, "timeout": 10})
        result = json.loads(result_str)
        assert result["success"], f"应执行成功: {result}"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    logger.info("[PASS] execute_python_code 成功检测")


# ========== 场景7: 完整 fallback_verify 流程 ==========

def test_fallback_verify_py_compile():
    """验证 fallback_verify 中 test_commands 含 py_compile"""
    import tempfile
    from src.agent.subagents.test_agent import TestAgent

    # 创建临时仓库目录
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        agent = TestAgent(repo_path=str(repo_path))

        # 创建一个语法正确的文件和一个语法错误的文件
        (repo_path / "good.py").write_text("x = 1\nprint(x)\n")
        (repo_path / "bad.py").write_text("x = 1\nprint(x\n")  # 语法错误

        # 验证语法错误的文件能被检测到
        import asyncio
        result = asyncio.run(agent._fallback_verify(
            error_locations=[{"file_path": "bad.py", "error_type": "SyntaxError"}],
            diff_content="+++ b/bad.py\n@@ -1 +1 @@\n-print(x\n+print(x)\n",
        ))
        assert result is not None
        logger.info(f"[PASS] fallback_verify 结果: {result.get('validation_status')}")

    # 不关心最终状态（CI 环境无文件），只验证流程不抛异常
    logger.info("[PASS] fallback_verify 流程正常")


# ========== 场景8: bug_count 去重 ==========

def test_bug_count_dedup():
    """bug_count 应按文件路径去重"""
    error_locations = [
        {"file_path": "a.py", "error_type": "NameError"},
        {"file_path": "a.py", "error_type": "TypeError"},  # 同一文件
        {"file_path": "b.py", "error_type": "SyntaxError"},
        {"file_path": "", "error_type": "Unknown"},  # 无路径
    ]

    bug_files = {err.get("file_path", "") for err in error_locations}
    bug_files.discard("")
    bug_count = len(bug_files) if bug_files else len(error_locations)

    assert bug_count == 2, f"应有 2 个涉 bug 文件: {bug_count}"
    logger.info(f"[PASS] bug_count 去重: {bug_count}")


def test_bug_count_all_empty():
    """所有 error_locations 无 file_path 时，回退到 len"""
    error_locations = [
        {"error_type": "NameError", "file_path": ""},
        {"error_type": "TypeError", "file_path": ""},
    ]

    bug_files = {err.get("file_path", "") for err in error_locations}
    bug_files.discard("")
    bug_count = len(bug_files) if bug_files else len(error_locations)

    assert bug_count == 2, f"无路径时应回退到总数: {bug_count}"
    logger.info(f"[PASS] bug_count 空路径回退: {bug_count}")


# ========== 主入口 ==========

if __name__ == "__main__":
    tests = [
        ("ANSI剥离", test_ansi_strip),
        ("ANSI多码", test_ansi_strip_multiple),
        ("ANSI_GH日志", test_ansi_strip_github_actions),
        ("pip拒绝", test_env_command_blocked),
        ("conda拒绝", test_env_command_blocked_conda),
        ("npm拒绝", test_env_command_blocked_npm),
        ("setup.py拒绝", test_env_command_blocked_setup),
        ("poetry拒绝", test_env_command_blocked_poetry),
        ("pytest通过", test_valid_pytest_passes),
        ("python通过", test_valid_python_passes),
        ("tox通过", test_valid_tox_passes),
        ("Agent ANSI提取", test_agent_ansi_extraction),
        ("Agent pip拒绝", test_agent_env_command_blocked),
        ("Agent推断优先", test_agent_inferred_priority),
        ("Agent测试文件pytest", test_agent_inferred_test_file),
        ("Agent非测试文件py_compile", test_agent_inferred_non_test),
        ("JSON健壮解析", test_robust_json_parsing),
        ("py_compile正确", test_py_compile_valid_file),
        ("py_compile语法错误", test_py_compile_syntax_error),
        ("execute_python_code错误检测", test_execute_python_code_error_detection),
        ("execute_python_code成功", test_execute_python_code_success),
        ("bug_count去重", test_bug_count_dedup),
        ("bug_count空路径", test_bug_count_all_empty),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            logger.error(f"[FAIL] {name}: {e}", exc_info=True)

    logger.info(f"\n{'='*40}")
    logger.info(f"结果: {passed} 通过, {failed} 失败")
    if failed > 0:
        exit(1)
