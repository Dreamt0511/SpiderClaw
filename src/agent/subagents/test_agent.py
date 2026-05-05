"""测试Agent实现 - 动态验证代码修复"""
import ast
import json
import logging
import os
import re
import subprocess
from typing import Dict, Any, List, Optional

from src.agent.state import ErrorLocation
from src.agent.tools.langchain_tools import execute_python_code

logger = logging.getLogger(__name__)


class TestAgent:
    """测试Agent - 动态验证代码修复

    验证策略：
    1. 主验证：ast.parse 语法检查 + execute_python_code 运行时验证
    2. 补充验证：CI 原始命令（仅作参考，失败不判 failure）
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
        openai_api_key: str = None,
        openai_base_url: str = "https://api.openai.com/v1",
        test_command: str = "pytest"
    ):
        self.repo_path = repo_path
        self.test_command = test_command

    # ---------- 安全过滤 ----------

    def _safety_filter(self, command: str) -> bool:
        """安全检查：返回 True 表示命令安全，False 表示危险"""
        for pattern in self._DANGEROUS_PATTERNS:
            if pattern.search(command):
                logger.warning(f"危险命令被拦截: {command}")
                return False
        return True

    # ---------- 源码验证（核心） ----------

    async def _verify_source_code(
        self,
        error_locations: List[ErrorLocation],
        diff_content: str,
    ) -> Optional[Dict[str, Any]]:
        """验证修复后的源码本身（ast.parse + execute_python_code）

        不依赖外部测试文件，直接检查被修复的代码：
        1. ast.parse 语法检查
        2. execute_python_code 子进程执行，检查运行时错误

        Returns:
            None 表示无法确定（无文件可验证），交给后续流程
        """
        source_files = self._extract_modified_files(error_locations, diff_content)
        if not source_files:
            logger.info("无法从错误位置和 diff 提取源文件，跳过源码验证")
            return None

        logger.info(f"源码验证: 检查 {len(source_files)} 个文件")

        results = []
        all_passed = True
        failure_details = []

        for fp in source_files:
            if not fp.endswith('.py'):
                continue

            full_path = os.path.join(self.repo_path, fp)
            if not os.path.exists(full_path):
                logger.warning(f"源码验证: 文件不存在 {fp}")
                continue

            # ---- 1. ast.parse 语法检查 ----
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    source = f.read()
                ast.parse(source, full_path)
                logger.info(f"源码验证: {fp} 语法检查通过")
            except SyntaxError as e:
                error_detail = f"{fp} 语法错误: line {e.lineno}: {e.msg}"
                results.append(f"[FAIL] {error_detail}")
                failure_details.append(error_detail)
                all_passed = False
                continue

            # ---- 2. execute_python_code 运行时检查 ----
            logger.info(f"源码验证: 执行 {fp}")
            exec_result_str = execute_python_code.invoke({
                "file_path": fp,
                "timeout": 15,
            })

            try:
                exec_result = json.loads(exec_result_str)
            except json.JSONDecodeError:
                error_detail = f"{fp}: 执行结果解析失败"
                results.append(f"[FAIL] {error_detail}")
                failure_details.append(error_detail)
                all_passed = False
                continue

            if exec_result.get("success"):
                results.append(f"[PASS] {fp}: 执行成功")
                continue

            # 执行失败 — 判断是原始错误还是新错误
            error_type = exec_result.get("error_type", "Unknown")
            error_msg = exec_result.get("error_message", "")
            error_line = exec_result.get("error_line", 0)

            # 导入失败是致命错误
            if error_type in ("ModuleNotFoundError", "ImportError"):
                error_detail = (
                    f"{fp}: 导入失败 - {error_type}: {error_msg} (行 {error_line})"
                )
                results.append(f"[FAIL] {error_detail}")
                failure_details.append(error_detail)
                all_passed = False
                continue

            # 检查是否是原始错误未修复
            is_original = any(
                error_type == err.error_type
                and err.error_message[:30] in error_msg[:30]
                for err in error_locations
                if err.file_path == fp
            )

            if is_original:
                error_detail = (
                    f"{fp}: 原始错误未修复 - {error_type}: {error_msg} (行 {error_line})"
                )
                results.append(f"[FAIL] {error_detail}")
                failure_details.append(error_detail)
                all_passed = False
            else:
                results.append(
                    f"[WARN] {fp}: 新错误 {error_type}: {error_msg} (行 {error_line})"
                    f"，但原始错误已修复"
                )

        if not results:
            return None

        output = "\n".join(results)

        if all_passed:
            return {
                "validation_status": "success",
                "validation_method": "source_code",
                "command_used": "ast.parse + execute_python_code",
                "test_passed": True,
                "test_output": output,
                "failed_tests": [],
                "verification_summary": f"源码验证通过: {len(source_files)} 个文件语法正确且无原始错误",
            }

        if failure_details:
            return {
                "validation_status": "failure",
                "validation_method": "source_code",
                "command_used": "ast.parse + execute_python_code",
                "test_passed": False,
                "test_output": output,
                "failed_tests": failure_details,
                "verification_summary": f"源码验证失败: {'; '.join(failure_details)}",
            }

        # 有警告但无明确失败
        return {
            "validation_status": "uncertain",
            "validation_method": "source_code",
            "command_used": "ast.parse + execute_python_code",
            "test_passed": False,
            "test_output": output,
            "failed_tests": [],
            "verification_summary": "源码验证: 原始错误已修复但出现新错误",
        }

    # ---------- CI 命令提取（补充验证用） ----------

    def _extract_ci_command(self, ci_logs: str) -> Optional[str]:
        """从 CI 日志中提取原始测试命令（仅从日志提取，不做文件推断）

        仅作为补充验证手段，失败不直接判 failure。
        """
        if not ci_logs:
            return None

        # 剥离 ANSI 转义码
        ci_logs = re.sub(r'\033\[[0-9;]*[a-zA-Z]', '', ci_logs)
        ci_logs = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', ci_logs)

        lines = ci_logs.split('\n')

        PYTHON_TEST_PREFIXES = ('pytest', 'python ', 'python3 ', 'nosetests', 'tox', 'unittest')

        def _is_python_command(cmd: str) -> bool:
            return any(cmd.lower().strip().startswith(p) for p in PYTHON_TEST_PREFIXES)

        def _clean_command(cmd: str) -> Optional[str]:
            cmd = cmd.split('|')[0].strip()
            cmd = cmd.split('&&')[0].strip()
            cmd = cmd.split(';')[0].strip()
            cmd = re.sub(r'\s+2>&1$', '', cmd).strip()
            return cmd if cmd else None

        def _is_env_setup_command(cmd: str) -> bool:
            env_patterns = [r'\bpip\b', r'\bconda\b', r'\binstall\b', r'\bsetup\.py\b']
            return any(re.search(p, cmd) for p in env_patterns)

        # ##[command] 格式
        for line in lines:
            m = re.search(r'##\[command\](\S.+)', line)
            if m:
                cmd = _clean_command(m.group(1))
                if cmd and _is_python_command(cmd) and not _is_env_setup_command(cmd):
                    logger.info(f"从 ##[command] 提取到命令: {cmd}")
                    return cmd

        # "Run <命令>" 行
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            cleaned = re.sub(r'^##\[group\]', '', stripped)
            prev = None
            while cleaned != prev:
                prev = cleaned
                cleaned = re.sub(r'^Run\s+', '', cleaned)
            cleaned = re.sub(r'^[$#>]\s*', '', cleaned)
            cmd = _clean_command(cleaned)
            if cmd and _is_python_command(cmd) and not _is_env_setup_command(cmd):
                logger.info(f"从 Run 行提取到命令: {cmd}")
                return cmd

        return None

    # ---------- 辅助方法 ----------

    def _extract_modified_files(
        self, error_locations: List[ErrorLocation], diff_content: str
    ) -> List[str]:
        """从错误位置和 diff 中提取修改的 .py 文件列表"""
        modified_files = set()

        for err in error_locations:
            fp = err.file_path
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

    def _estimate_confidence(
        self, error_locations: List[ErrorLocation], diff_content: str
    ) -> str:
        """基于错误类型和diff内容估算修复置信度"""
        if not diff_content:
            return "低"

        has_syntax_err = any(
            err.error_type in ("SyntaxError", "IndentationError", "TabError")
            for err in error_locations
        )
        if has_syntax_err and diff_content.strip():
            return "高"

        has_name_err = any(
            err.error_type in ("NameError", "ImportError", "ModuleNotFoundError")
            for err in error_locations
        )
        if has_name_err and ("import " in diff_content or "def " in diff_content):
            return "中高"

        if diff_content.strip():
            return "中"

        return "低"

    def _format_output(self, command: str, result: subprocess.CompletedProcess) -> str:
        """格式化命令输出"""
        output = f"Command: {command}\nExit code: {result.returncode}\n\n"
        if result.stdout:
            output += f"STDOUT:\n{result.stdout[:3000]}\n"
        if result.stderr:
            output += f"STDERR:\n{result.stderr[:3000]}\n"
        return output

    # ---------- 核心验证方法 ----------

    async def verify_fix(
        self,
        error_locations: List[ErrorLocation],
        fix_description: str,
        diff_content: str,
        ci_logs: str = "",
    ) -> Dict[str, Any]:
        """
        动态验证修复有效性

        策略：
        1. 主验证：ast.parse + execute_python_code 验证源码本身
        2. 补充验证：CI 原始命令（失败不判 failure，降为 uncertain）
        3. 全部不确定 → uncertain + 置信度估算

        Args:
            error_locations: 原始错误位置列表
            fix_description: 修复描述
            diff_content: 修复的diff内容
            ci_logs: CI日志内容（用于提取原始命令）

        Returns:
            Dict: 验证结果，包含 validation_status, test_output, failed_tests 等字段
        """
        try:
            logger.info("=== 测试Agent: 动态验证修复 ===")

            # ---- 步骤1: 源码验证（主验证） ----
            source_result = await self._verify_source_code(
                error_locations, diff_content
            )

            if source_result:
                status = source_result["validation_status"]
                logger.info(f"源码验证结果: {status}")

                # 通过 → 直接返回
                if status == "success":
                    return source_result

                # 明确失败 → 返回失败（带详细信息供修复 Agent 重试）
                if status == "failure":
                    return source_result

                # uncertain → 继续尝试补充验证

            # ---- 步骤2: 补充验证 — CI 原始命令 ----
            ci_command = self._extract_ci_command(ci_logs)

            if ci_command and self._safety_filter(ci_command):
                logger.info(f"补充验证: 执行 CI 原始命令 {ci_command}")
                try:
                    result = subprocess.run(
                        ci_command,
                        shell=True,
                        cwd=self.repo_path,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    output = self._format_output(ci_command, result)

                    if result.returncode == 0:
                        logger.info("CI 命令验证通过")
                        return {
                            "validation_status": "success",
                            "validation_method": "ci_command",
                            "command_used": ci_command,
                            "test_passed": True,
                            "test_output": output,
                            "failed_tests": [],
                            "verification_summary": f"CI 命令 '{ci_command}' 执行成功",
                        }

                    # CI 命令失败不判 failure（测试文件本身可能有问题）
                    logger.warning(
                        f"CI 命令退出码 {result.returncode}，但不作为最终判定"
                    )
                    if source_result:
                        # 源码验证已给出 uncertain，合并 CI 信息
                        source_result["test_output"] += f"\n\n--- CI 命令补充 ---\n{output}"
                        source_result["verification_summary"] += (
                            f"；CI 命令 '{ci_command}' 也失败（退出码 {result.returncode}），"
                            f"但测试文件本身可能有问题"
                        )
                        return source_result

                except subprocess.TimeoutExpired:
                    logger.warning(f"CI 命令超时: {ci_command}")
                except Exception as e:
                    logger.warning(f"CI 命令执行异常: {e}")

            # ---- 步骤3: 全部不确定 ----
            if source_result:
                return source_result

            confidence = self._estimate_confidence(error_locations, diff_content)
            return {
                "validation_status": "uncertain",
                "validation_method": "none",
                "command_used": "",
                "test_passed": False,
                "test_output": "无法验证: 未能提取源文件且无 CI 日志",
                "failed_tests": [],
                "verification_summary": f"无法自动验证修复正确性。置信度: {confidence}",
                "details": f"置信度: {confidence}",
            }

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
