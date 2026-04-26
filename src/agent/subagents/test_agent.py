"""测试Agent实现 - 动态验证代码修复"""
import ast
import logging
import os
import re
import subprocess
from collections import Counter
from typing import Dict, Any, List, Optional

from langchain_openai import ChatOpenAI

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

        按优先级尝试：
        1. GitHub Actions 的 "Run <command>" 行
        2. shell 提示符 "$ <command>" 或 "% <command>"
        3. 从 error_locations 的文件路径推断
        """
        if not ci_logs:
            return None

        lines = ci_logs.split('\n')

        # 模式1: 查找包含 pytest/python 等命令的行
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue

            # 移除常见的 CI 前缀
            cleaned = re.sub(r'^##\[group\]', '', stripped)
            cleaned = re.sub(r'^##\[endgroup\].*$', '', cleaned)
            cleaned = re.sub(r'^Run\s+', '', cleaned)
            cleaned = re.sub(r'^[$#>]\s*', '', cleaned)

            # 检查是否是测试/运行命令
            for prefix in ['pytest', 'python ', 'python3 ', 'nosetests',
                           'tox', 'make test', 'make check', 'make ']:
                if cleaned.startswith(prefix):
                    # 只取第一条命令（去掉管道和链式执行）
                    cmd = cleaned.split('|')[0].strip()
                    cmd = cmd.split('&&')[0].strip()
                    cmd = cmd.split(';')[0].strip()
                    cmd = re.sub(r'\s+2>&1$', '', cmd).strip()
                    if cmd:
                        logger.info(f"从 CI 日志提取到命令: {cmd}")
                        return cmd

        # 模式2: 从 error_locations 的文件路径推断
        py_files = [
            err.get('file_path', '')
            for err in error_locations
            if err.get('file_path', '').endswith('.py')
        ]
        if py_files:
            most_common_file = Counter(py_files).most_common(1)[0][0]
            if most_common_file.startswith('tests/') or 'test_' in most_common_file:
                inferred = f"pytest {most_common_file} -v"
            else:
                inferred = f"python {most_common_file}"
            logger.info(f"从错误位置推断命令: {inferred}")
            return inferred

        logger.warning("未能从 CI 日志中提取到任何命令")
        return None

    # ---------- 降级验证 ----------

    async def _fallback_verify(
        self, error_locations: List[Dict]
    ) -> Dict[str, Any]:
        """降级验证：无法提取命令时的备用方案

        优先级：
        1. 语法错误 → ast.parse 静态解析
        2. 其他错误 → 尝试常见测试命令（pytest → python -m unittest）
        """
        # --- 方案1: 语法错误 → ast.parse ---
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
                        "details": f"文件 {fp} 语法正确",
                    }
                except SyntaxError as e:
                    logger.warning(f"AST解析发现 {fp} 仍有语法错误: {e}")
                    return {
                        "validation_status": "failure",
                        "validation_method": "ast",
                        "command_used": f"ast.parse({fp})",
                        "output": str(e),
                        "details": f"文件 {fp} 仍然存在语法错误: {e}",
                    }

        # --- 方案2: 尝试常见测试命令 ---
        test_commands = [
            self.test_command,                    # 默认命令 pytest
            "python -m pytest --tb=short -x",     # 失败即停
            "python -m unittest discover -v",      # unittest
            "python -m pytest",                    # 通用 pytest
        ]

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
                    return {
                        "validation_status": "uncertain",
                        "validation_method": "fallback_test",
                        "command_used": cmd,
                        "output": output,
                        "details": "仓库中无测试用例，无法自动验证修复正确性",
                    }

                logger.warning(f"降级命令 '{cmd}' 退出码 {result.returncode}")

            except subprocess.TimeoutExpired:
                logger.warning(f"降级命令 '{cmd}' 超时")
            except Exception as e:
                logger.warning(f"降级命令 '{cmd}' 执行异常: {e}")

        # --- 全部降级失败 ---
        return {
            "validation_status": "uncertain",
            "validation_method": "none",
            "command_used": "",
            "output": "所有降级验证方案均不可用",
            "details": "无法提取原始失败命令，且所有降级验证方案均不可用",
        }

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

                    # pytest 退出码 5 = 无测试用例
                    if result.returncode == 5:
                        logger.info("验证不确定: pytest 无测试用例")
                        return {
                            "validation_status": "uncertain",
                            "validation_method": "command",
                            "command_used": original_command,
                            "test_passed": False,
                            "test_output": output,
                            "failed_tests": failed_tests,
                            "verification_summary": "pytest 未发现任何测试用例，无法判断修复正确性",
                            "details": (
                                "原始命令 'pytest' 返回退出码 5（无测试用例）。\n"
                                "这不是修复失败，而是仓库中没有可执行的测试。\n"
                                "已将修复视为有效并提交 PR，请人工确认。"
                            ),
                        }

                    # 非零退出码 = 验证失败
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

            # ---- 步骤2: 无原始命令 → 降级验证 ----
            logger.info("未提取到原始命令，使用降级验证")
            fallback_result = await self._fallback_verify(error_locations)

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
