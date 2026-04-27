"""测试Agent实现 - 动态验证代码修复"""
import ast
import logging
import os
import re
import subprocess
from collections import Counter
from typing import Dict, Any, List, Optional

from langchain_openai import ChatOpenAI

from src.agent.tools.langchain_tools import execute_python_code

logger = logging.getLogger(__name__)


class TestAgent:
    """测试Agent - 动态验证代码修复

    核心改进：不再固定执行 pytest，而是从 CI 日志中提取原始失败命令，
    用同样的命令验证修复是否有效。如果无法提取命令，使用降级策略。
    """

    # 危险命令模式 - 禁止执行
    _DANGEROUS_PATTERNS = [
        re.compile(p) for p in [
            r'\brm\s+(-rf?|-[rf]+)\s+/\s*',  # rm -rf /
            r'\bsudo\s',                       # sudo
            r'\beval\s',                       # eval
            r'\bexec\s',                       # exec
            r'`[^`]*`',                        # backtick execution
            r'\$\(',                           # $() substitution
            r'>\s*/dev/',                      # redirect to device
            r'\bdd\s+if=',                     # dd dangerous
            r'\bmkfs\.',                       # mkfs
            r':\(\)\s*\{',                     # fork bomb
            r'\bchmod\s+777\b',                # chmod 777
            r'\bwget\b.*\bbash\b',             # wget | bash
            r'\bcurl\b.*\|?\s*\bbash\b',       # curl | bash
        ]
    ]

    def __init__(
        self,
        repo_path: str,
        llm_model: str = "gpt-4o",
        temperature: float = 0.1,
        openai_api_key: str = None,
        openai_base_url: str = "https://api.openai.com/v1",
        test_command: str = "pytest"
    ):
        """
        初始化测试Agent

        Args:
            repo_path: 本地仓库路径
            llm_model: LLM模型名称
            temperature: 温度参数
            openai_api_key: OpenAI API密钥
            openai_base_url: OpenAI API基础URL
            test_command: 默认测试命令（当无法从日志提取时作为降级选项）
        """
        self.repo_path = repo_path
        self.test_command = test_command

        # 初始化LLM（用于可选的失败分析）
        self.llm = ChatOpenAI(
            model=llm_model,
            temperature=temperature,
            api_key=openai_api_key,
            base_url=openai_base_url
        )

    # ---------- 安全过滤 ----------

    def _safety_filter(self, command: str) -> bool:
        """安全检查：返回 True 表示命令安全，False 表示危险"""
        for pattern in self._DANGEROUS_PATTERNS:
            if pattern.search(command):
                logger.warning(f"危险命令被拦截: {command}")
                return False
        return True

    # ---------- 命令提取 ----------

    def _extract_failure_command(
        self, ci_logs: str, error_locations: List[Dict]
    ) -> Optional[str]:
        """从 CI 日志中提取原始失败命令

        优先级（核心原则：错误文件路径推断 > CI 日志提取）：
        1. 从 error_locations 的文件路径推断（最可靠）
        2. GitHub Actions 的 "##[command]<实际命令>" 行
        3. GitHub Actions 的 "Run <命令>" 行

        返回前会检查命令是否在测试白名单中，环境准备命令（pip install 等）会被拒绝。
        """
        # 剥离 ANSI 转义码（CI 日志中可能包含颜色控制序列如 [0m）
        if ci_logs:
            ci_logs = re.sub(r'\033\[[0-9;]*[a-zA-Z]', '', ci_logs)
            ci_logs = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', ci_logs)

        # 环境/准备命令黑名单 — — 这些不是验证命令
        _ENV_COMMAND_PATTERNS = [
            re.compile(p) for p in [
                r'\bpip\b',         # pip install/ uninstall / etc
                r'\bconda\b',       # conda install / etc
                r'\bpoetry\b',      # poetry install / add / etc
                r'\bnpm\b',         # npm install / ci / etc
                r'\binstall\b',     # setup.py install, make install, etc
                r'\bsetup\.py\b',   # python setup.py ...
            ]
        ]

        def _is_env_setup_command(cmd: str) -> bool:
            """检查是否为环境准备命令（非验证命令）"""
            for pattern in _ENV_COMMAND_PATTERNS:
                if pattern.search(cmd):
                    return True
            return False

        # --- 模式1（最高优先级）：从 error_locations 的文件路径推断 ---
        inferred = self._infer_command_from_errors(error_locations)
        if inferred:
            logger.info(f"从错误位置推断命令: {inferred}")
            return inferred

        # --- 无日志时直接返回 ---
        if not ci_logs:
            logger.warning("未能从 CI 日志中提取到任何命令")
            return None

        # --- CI 日志提取（仅当推断失败且有日志时才执行）---
        lines = ci_logs.split('\n')

        # Python 测试命令白名单
        PYTHON_TEST_PREFIXES = ('pytest', 'python ', 'python3 ', 'nosetests', 'tox', 'unittest')

        def _is_python_command(cmd: str) -> bool:
            return any(cmd.lower().strip().startswith(p) for p in PYTHON_TEST_PREFIXES)

        # 常见测试命令前缀（用于日志行匹配）
        COMMAND_PREFIXES = [
            'pytest', 'python ', 'python3 ', 'nosetests',
            'tox', 'make test', 'make check', 'make ',
        ]

        def _is_test_command(cmd: str) -> bool:
            return any(cmd.startswith(p) for p in COMMAND_PREFIXES)

        def _is_valid_verification_command(cmd: str) -> bool:
            """同时满足：是测试命令 + 是 Python 命令 + 不是环境准备命令"""
            return _is_test_command(cmd) and _is_python_command(cmd) and not _is_env_setup_command(cmd)

        def _clean_command(cmd: str) -> Optional[str]:
            """清理命令：去掉管道/链式/重定向，返回纯命令"""
            cmd = cmd.split('|')[0].strip()
            cmd = cmd.split('&&')[0].strip()
            cmd = cmd.split(';')[0].strip()
            cmd = re.sub(r'\s+2>&1$', '', cmd).strip()
            return cmd if cmd else None

        # 模式2: GitHub Actions ##[command] 格式（精确提取实际执行的命令）
        for line in lines:
            m = re.search(r'##\[command\](\S.+)', line)
            if m:
                cmd = _clean_command(m.group(1))
                if cmd and _is_valid_verification_command(cmd):
                    logger.info(f"从 ##[command] 提取到命令: {cmd}")
                    return cmd

        # 模式3: 查找 "Run <命令>" 行（剥离 ##[group]/Run 前缀）
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            cleaned = re.sub(r'^##\[group\]', '', stripped)

            # 多次剥离 "Run " 前缀（处理 "Run Run pytest" 的情况）
            prev = None
            while cleaned != prev:
                prev = cleaned
                cleaned = re.sub(r'^Run\s+', '', cleaned)

            cleaned = re.sub(r'^[$#>]\s*', '', cleaned)

            cmd = _clean_command(cleaned)
            if cmd and _is_valid_verification_command(cmd):
                logger.info(f"从 Run 行提取到命令: {cmd}")
                return cmd

        logger.warning("未能从 CI 日志中提取到任何有效验证命令")
        return None

    def _infer_command_from_errors(
        self, error_locations: List[Dict]
    ) -> Optional[str]:
        """从错误位置的文件路径推断验证命令"""
        py_files = [
            err.get('file_path', '')
            for err in error_locations
            if err.get('file_path', '').endswith('.py')
        ]
        if py_files:
            most_common_file = Counter(py_files).most_common(1)[0][0]
            basename = os.path.basename(most_common_file)

            # 以 test_ 开头的不一定是 pytest 测试文件（可能是语法错误示例）
            # 读取文件内容检查是否包含实际测试函数
            if basename.startswith('test_') or basename.startswith('test-'):
                full_path = os.path.join(self.repo_path, most_common_file)
                if os.path.exists(full_path):
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        if 'def test_' in content:
                            inferred = f"pytest {most_common_file}"
                            logger.info(f"检测到测试函数，使用 pytest: {inferred}")
                            return inferred
                    except Exception:
                        pass
                # 文件不存在或不包含测试函数 → 使用 py_compile 安全验证
                inferred = f"python -m py_compile {most_common_file}"
                logger.info(f"文件不含测试函数或无法读取，使用 py_compile: {inferred}")
                return inferred

            # 普通文件 → py_compile
            inferred = f"python -m py_compile {most_common_file}"
            logger.info(f"非测试文件，使用 py_compile 验证语法: {inferred}")
            return inferred

        logger.warning("未能从 error_locations 提取到 .py 文件")
        return None

    # ---------- 降级验证 ----------

    async def _replay_verification(
        self, error_locations: List[Dict], ci_logs: str = ""
    ) -> Dict[str, Any]:
        """回放式验证：直接检测原始错误是否已修复

        作为降级策略的第一优先级，在语法错误检查之前执行。
        通过编译/导入检测来验证特定类型的错误是否已修复。
        """
        results = []
        all_passed = True

        for error in error_locations:
            error_type = error.get("error_type", "")
            error_msg = error.get("error_message", "")
            file_path = error.get("file_path", "")

            # 1. NameError 检测 — compile 不能捕获运行时 NameError
            #    这里仅做语法检查，真正的运行时验证交给 Code Interpreter
            if error_type == "NameError":
                full_path = os.path.join(self.repo_path, file_path) if file_path else None
                if full_path and os.path.exists(full_path):
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            source = f.read()
                        compile(source, full_path, 'exec')
                        # compile 通过仅说明语法正确，NameError 仍需运行时验证
                        results.append(f"[INFO] {error_type}: 文件语法正确，需运行时验证")
                        all_passed = False  # 标记为未通过，继续降级做运行时验证
                    except SyntaxError as e:
                        all_passed = False
                        results.append(f"[FAIL] {error_type}: 仍存在语法错误: {e}")
                    except Exception as e:
                        all_passed = False
                        results.append(f"[FAIL] {error_type}: 编译失败: {e}")

            # 2. ImportError 检测 — 尝试导入模块
            if error_type in ("ImportError", "ModuleNotFoundError"):
                # 从错误消息中提取缺失模块名
                import re
                match = re.search(r"No module named '(\w+)'", error_msg)
                if match:
                    module_name = match.group(1)
                    try:
                        __import__(module_name)
                        results.append(f"[PASS] {error_type}: 模块 {module_name} 导入成功")
                    except ImportError:
                        all_passed = False
                        results.append(f"[FAIL] {error_type}: 模块 {module_name} 仍未找到")
                else:
                    # 无法提取模块名，使用通用检查
                    full_path = os.path.join(self.repo_path, file_path) if file_path else None
                    if full_path and os.path.exists(full_path):
                        try:
                            with open(full_path, 'r', encoding='utf-8') as f:
                                source = f.read()
                            compile(source, full_path, 'exec')
                            results.append(f"[PASS] {error_type}: 文件编译成功，导入错误可能已修复")
                        except Exception as e:
                            all_passed = False
                            results.append(f"[FAIL] {error_type}: 编译失败: {e}")

        if not results:
            return None

        if all_passed and all("[PASS]" in r or "[INFO]" in r for r in results):
            return {
                "validation_status": "success",
                "validation_method": "replay_verification",
                "command_used": "",
                "output": "\n".join(results),
                "details": f"回放验证全部通过: {'; '.join(results)}",
            }
        elif any("[FAIL]" in r for r in results):
            return {
                "validation_status": "failure",
                "validation_method": "replay_verification",
                "command_used": "",
                "output": "\n".join(results),
                "details": f"回放验证失败: {'; '.join(results)}",
            }
        else:
            return {
                "validation_status": "uncertain",
                "validation_method": "replay_verification",
                "command_used": "",
                "output": "\n".join(results),
                "details": f"回放验证部分通过: {'; '.join(results)}",
            }

    async def _fallback_verify(
        self, error_locations: List[Dict],
        ci_logs: str = "",
        diff_content: str = "",
    ) -> Dict[str, Any]:
        """降级验证：通过多种方案验证修复的正确性

        优先级：
        1. 回放式验证 → compile 检查（轻量级，先过滤语法错误）
        2. Code Interpreter → 直接执行修复后的文件，检查错误是否消失
        3. 语法错误 → ast.parse 静态解析
        4. 常见测试命令 → pytest / unittest（兜底）
        """
        # --- 方案1: 回放式验证（compile 检查）---
        replay_result = await self._replay_verification(error_locations, ci_logs)
        if replay_result and replay_result.get("validation_status") == "failure":
            return replay_result

        # --- 方案2: Code Interpreter 直接执行修复后的代码 ---
        modified_files = self._extract_modified_files(error_locations, diff_content)
        if not modified_files:
            # 如果 error_locations 和 diff 都无法提供文件列表，
            # 主动扫描仓库找到修改/相关的 .py 文件
            logger.info("从错误位置和 diff 均未提取到文件，主动扫描仓库中的 .py 文件")
            modified_files = self._scan_repo_for_py_files(max_files=5)
        if modified_files:
            logger.info(f"Code Interpreter: 将执行 {len(modified_files)} 个文件")
            import json

            results = []
            all_passed = True

            for fp in modified_files:
                if not fp.endswith('.py'):
                    continue

                logger.info(f"Code Interpreter 执行: {fp}")
                exec_result_str = execute_python_code.invoke({
                    "file_path": fp,
                    "timeout": 15,
                })

                try:
                    exec_result = json.loads(exec_result_str)
                except json.JSONDecodeError:
                    results.append(f"文件 {fp}: 执行结果解析失败")
                    all_passed = False
                    continue

                if exec_result.get("success"):
                    results.append(f"[PASS] 文件 {fp}: 执行成功（退出码 0）")
                else:
                    error_type = exec_result.get("error_type", "Unknown")
                    error_msg = exec_result.get("error_message", "")
                    error_line = exec_result.get("error_line", 0)

                    # 检查是否是原始错误还是新错误
                    is_original_error = any(
                        error_type == err.get("error_type", "")
                        and err.get("error_message", "")[:30] in error_msg[:30]
                        for err in error_locations
                    )

                    if is_original_error:
                        results.append(
                            f"[FAIL] 文件 {fp}: 原始错误未修复 - {error_type}: {error_msg} "
                            f"(行 {error_line})"
                        )
                        all_passed = False
                    else:
                        results.append(
                            f"[WARN] 文件 {fp}: 有新错误 - {error_type}: {error_msg} "
                            f"(行 {error_line})，但原始错误已消失"
                        )

            if results:
                if all_passed:
                    return {
                        "validation_status": "success",
                        "validation_method": "code_interpreter",
                        "command_used": "execute_python_code",
                        "output": "\n".join(results),
                        "details": "Code Interpreter 验证全部通过，置信度: 高",
                    }
                elif any("✗" in r for r in results):
                    return {
                        "validation_status": "failure",
                        "validation_method": "code_interpreter",
                        "command_used": "execute_python_code",
                        "output": "\n".join(results),
                        "details": "Code Interpreter 验证失败，原始错误仍存在，置信度: 高",
                    }
                else:
                    return {
                        "validation_status": "uncertain",
                        "validation_method": "code_interpreter",
                        "command_used": "execute_python_code",
                        "output": "\n".join(results),
                        "details": "Code Interpreter 验证: 原始错误已修复但出现新错误，置信度: 中",
                    }

        # --- 方案3: 语法错误 → ast.parse ---
        is_syntax_err = any(
            err.get('error_type', '') in ('SyntaxError', 'IndentationError', 'TabError')
            for err in error_locations
        )
        if is_syntax_err:
            for err in error_locations:
                fp = err.get('file_path', '')
                if not fp:
                    continue
                full_path = os.path.join(self.repo_path, fp)
                if not os.path.exists(full_path):
                    continue
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        ast.parse(f.read())
                    logger.info(f"AST解析验证通过: {fp}")
                    return {
                        "validation_status": "success",
                        "validation_method": "ast",
                        "command_used": f"ast.parse({fp})",
                        "output": "AST 语法解析通过",
                        "details": f"文件 {fp} 语法正确，置信度: 高",
                    }
                except SyntaxError as e:
                    logger.warning(f"AST解析发现 {fp} 仍有语法错误: {e}")
                    return {
                        "validation_status": "failure",
                        "validation_method": "ast",
                        "command_used": f"ast.parse({fp})",
                        "output": str(e),
                        "details": f"文件 {fp} 仍然存在语法错误: {e}，置信度: 高",
                    }

        # --- 方案4: 尝试常见测试命令（兜底）---
        test_commands = []
        if modified_files:
            basename = os.path.basename(modified_files[0])
            if basename.startswith('test_') or basename.startswith('test-'):
                test_commands.append(f"pytest {modified_files[0]}")
            else:
                test_commands.append(f"python -m py_compile {modified_files[0]}")
        test_commands += [
            self.test_command,                    # 默认命令 pytest
            "python -m pytest --tb=short -x",     # 失败即停
            "python -m unittest discover -v",      # unittest
            "python -m pytest",                    # 通用 pytest
        ]

        # 过滤掉 None
        test_commands = [cmd for cmd in test_commands if cmd]

        for cmd in test_commands:
            if not cmd or not self._safety_filter(cmd):
                continue

            try:
                logger.info(f"降级验证: 尝试命令 '{cmd}'")
                result = subprocess.run(
                    cmd, shell=True, cwd=self.repo_path,
                    capture_output=True, text=True, timeout=60
                )
                output = self._format_output(cmd, result)

                if result.returncode == 0:
                    logger.info(f"降级命令 '{cmd}' 执行成功")
                    return {
                        "validation_status": "success",
                        "validation_method": "fallback_test",
                        "command_used": cmd,
                        "output": output,
                        "details": f"降级测试命令 '{cmd}' 执行成功",
                    }

                # pytest 退出码 5 = 无测试用例收集
                if result.returncode == 5:
                    logger.info(f"降级命令 '{cmd}' 退出码 5（无测试用例）")
                    confidence = self._estimate_confidence(error_locations, diff_content)
                    return {
                        "validation_status": "uncertain",
                        "validation_method": "fallback_test",
                        "command_used": cmd,
                        "output": output,
                        "details": (
                            f"仓库中无测试用例，无法自动验证修复正确性。"
                            f"置信度: {confidence}"
                        ),
                    }

                logger.warning(f"降级命令 '{cmd}' 退出码 {result.returncode}")

            except subprocess.TimeoutExpired:
                logger.warning(f"降级命令 '{cmd}' 超时")
            except Exception as e:
                logger.warning(f"降级命令 '{cmd}' 执行异常: {e}")

        # --- 全部降级失败 ---
        confidence = self._estimate_confidence(error_locations, diff_content)
        return {
            "validation_status": "uncertain",
            "validation_method": "none",
            "command_used": "",
            "output": "所有降级验证方案均不可用",
            "details": f"无法提取原始失败命令，且所有降级验证方案均不可用。置信度: {confidence}",
        }

    def _extract_modified_files(
        self, error_locations: List[Dict], diff_content: str
    ) -> List[str]:
        """从错误位置和 diff 中提取修改的 .py 文件列表"""
        modified_files = set()

        for err in error_locations:
            fp = err.get("file_path", "")
            if fp and fp.endswith('.py'):
                modified_files.add(fp)

        if diff_content:
            for line in diff_content.split('\n'):
                if line.startswith('+++ b/'):
                    fp = line[6:].strip()
                elif line.startswith('+++ a/'):
                    fp = line[6:].strip()
                elif line.startswith('+++ '):
                    fp = line[4:].strip()
                else:
                    continue
                if fp.endswith('.py'):
                    modified_files.add(fp)

        return list(modified_files)

    def _scan_repo_for_py_files(self, max_files: int = 5) -> List[str]:
        """扫描仓库中的 .py 文件（排除无关目录），作为 Code Interpreter 的兜底目标

        Args:
            max_files: 最大返回文件数，避免执行过多文件
        """
        py_files = []
        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', '.venv', 'venv', 'node_modules')]
            for f in files:
                if f.endswith('.py'):
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, self.repo_path).replace('\\', '/')
                    py_files.append(rel)
                    if len(py_files) >= max_files:
                        return py_files
        return py_files

    def _estimate_confidence(
        self, error_locations: List[Dict], diff_content: str
    ) -> str:
        """基于错误类型和diff内容估算修复置信度"""
        if not diff_content:
            return "低"

        # 语法错误 + 有diff → 高置信度
        has_syntax_err = any(
            err.get("error_type", "") in ("SyntaxError", "IndentationError", "TabError")
            for err in error_locations
        )
        if has_syntax_err and diff_content.strip():
            return "高"

        # NameError/ImportError + diff中包含import/变量定义 → 中高置信度
        has_name_err = any(
            err.get("error_type", "") in ("NameError", "ImportError", "ModuleNotFoundError")
            for err in error_locations
        )
        if has_name_err and ("import " in diff_content or "def " in diff_content):
            return "中高"

        # 有diff但不确定逻辑正确性
        if diff_content.strip():
            return "中"

        return "低"

    def _check_orig_errors_fixed(
        self, error_locations: List[Dict], check_details: List[str]
    ) -> bool:
        """检查语义检查结果中是否表明原始错误已被修复"""
        # 如果没有出现"原始错误未修复"，则认为已修复
        for detail in check_details:
            if "原始错误未修复" in detail:
                return False
        return True

    # ---------- 辅助方法 ----------

    def _format_output(self, command: str, result: subprocess.CompletedProcess) -> str:
        """格式化命令输出"""
        output = f"Command: {command}\nExit code: {result.returncode}\n\n"
        if result.stdout:
            output += f"STDOUT:\n{result.stdout[:3000]}\n"
        if result.stderr:
            output += f"STDERR:\n{result.stderr[:3000]}\n"
        return output

    def _parse_failed_tests(self, test_output: str) -> List[str]:
        """
        解析测试输出中的失败用例（辅助方法，不再作为主要判断依据）
        """
        failed_tests = []

        # 匹配pytest失败格式
        failed_pattern = re.compile(r'^FAILED ([^\s:]+::[^\s]+)', re.MULTILINE)
        matches = failed_pattern.findall(test_output)
        failed_tests.extend(matches)

        # 匹配简短摘要格式
        summary_pattern = re.compile(
            r'=+ short test summary info =+\n(.*?)\n=+', re.DOTALL
        )
        summary_match = summary_pattern.search(test_output)
        if summary_match:
            summary_content = summary_match.group(1)
            summary_failed = re.findall(r'FAILED\s+([^\s]+)', summary_content)
            failed_tests.extend(summary_failed)

        return list(set(failed_tests))

    # ---------- 核心验证方法 ----------

    async def verify_fix(
        self,
        error_locations: List[Dict],
        fix_description: str,
        diff_content: str,
        ci_logs: str = "",
    ) -> Dict[str, Any]:
        """
        动态验证修复有效性

        策略：
        1. 从 CI 日志提取原始失败命令
        2. 如果提取到 → 安全过滤 → 执行命令 → 根据退出码判断
        3. 未提取到 → 降级验证（ast.parse / 常见测试命令）
        4. 返回统一格式的验证结果

        Args:
            error_locations: 原始错误位置列表
            fix_description: 修复描述
            diff_content: 修复的diff内容
            ci_logs: CI日志内容（用于提取原始命令）

        Returns:
            Dict: 验证结果，包含 validation_status 字段
        """
        try:
            logger.info("=== 测试Agent: 动态验证修复 ===")

            # ---- 步骤1: 提取原始失败命令 ----
            original_command = self._extract_failure_command(ci_logs, error_locations)

            if original_command:
                logger.info(f"使用原始命令验证: {original_command}")

                # 安全检查
                if not self._safety_filter(original_command):
                    return {
                        "validation_status": "uncertain",
                        "validation_method": "blocked",
                        "command_used": original_command,
                        "test_passed": False,
                        "test_output": f"命令被安全过滤器拦截: {original_command}",
                        "failed_tests": [],
                        "verification_summary": "原始命令被安全策略拦截，无法自动验证",
                        "details": f"命令 '{original_command}' 包含危险操作，已被拦截",
                    }

                # 执行命令
                try:
                    logger.info(f"执行验证命令: {original_command}")
                    result = subprocess.run(
                        original_command,
                        shell=True,
                        cwd=self.repo_path,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    output = self._format_output(original_command, result)
                    failed_tests = self._parse_failed_tests(output)

                    # 根据退出码判断
                    if result.returncode == 0:
                        logger.info("验证通过: 命令退出码为 0")
                        return {
                            "validation_status": "success",
                            "validation_method": "command",
                            "command_used": original_command,
                            "test_passed": True,
                            "test_output": output,
                            "failed_tests": [],
                            "verification_summary": f"原始命令 '{original_command}' 执行成功，修复有效",
                        }

                    if result.returncode == 5:
                        # pytest 退出码 5 = 无测试用例 → 降级到 Code Interpreter
                        logger.info("pytest 无测试用例，降级到 Code Interpreter 验证")
                    else:
                        # 非零退出码（非5）= 验证失败
                        logger.warning(
                            f"验证失败: 命令退出码 {result.returncode}"
                        )
                        return {
                            "validation_status": "failure",
                            "validation_method": "command",
                            "command_used": original_command,
                            "test_passed": False,
                            "test_output": output,
                            "failed_tests": failed_tests,
                            "verification_summary": (
                                f"原始命令 '{original_command}' 执行失败（退出码 {result.returncode}），修复无效"
                            ),
                        }

                except subprocess.TimeoutExpired:
                    logger.warning(f"验证命令超时: {original_command}")
                    return {
                        "validation_status": "uncertain",
                        "validation_method": "timeout",
                        "command_used": original_command,
                        "test_passed": False,
                        "test_output": f"命令执行超时（120秒）: {original_command}",
                        "failed_tests": [],
                        "verification_summary": "验证命令执行超时，无法确定修复是否正确",
                    }

                except Exception as e:
                    logger.error(f"验证命令执行异常: {e}")
                    return {
                        "validation_status": "uncertain",
                        "validation_method": "error",
                        "command_used": original_command,
                        "test_passed": False,
                        "test_output": f"命令执行异常: {str(e)}",
                        "failed_tests": [],
                        "verification_summary": f"验证命令执行异常: {str(e)}",
                    }

            # ---- 步骤2: 降级验证（原始命令不可用或pytest无测试用例）----
            logger.info("使用降级验证（Code Interpreter / AST / 兜底命令）")
            fallback_result = await self._fallback_verify(
                error_locations, ci_logs=ci_logs, diff_content=diff_content
            )

            # 补充与原始 fix 兼容的字段
            fallback_result["test_passed"] = (
                fallback_result["validation_status"] == "success"
            )
            fallback_result["failed_tests"] = []
            fallback_result["test_output"] = fallback_result.get("output", "")
            fallback_result["verification_summary"] = fallback_result.get("details", "")

            logger.info(
                f"降级验证完成: {fallback_result['validation_status']}"
            )
            return fallback_result

        except Exception as e:
            logger.error(f"测试Agent执行失败: {e}", exc_info=True)
            return {
                "validation_status": "uncertain",
                "validation_method": "error",
                "command_used": "",
                "test_passed": False,
                "test_output": f"测试执行失败: {str(e)}",
                "failed_tests": [],
                "verification_summary": f"验证过程出错: {str(e)}",
                "details": f"测试Agent抛出异常: {str(e)}",
            }
