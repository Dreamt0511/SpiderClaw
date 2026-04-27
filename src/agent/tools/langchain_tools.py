"""LangChain标准工具定义"""
import os
import tempfile
import subprocess
import re
from typing import Optional, List, Dict, Any
from langchain_core.tools import tool
from git import Repo, GitCommandError
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
import zipfile
import logging

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# 上下文变量，用于在工具之间异步安全地共享上下文
import contextvars
_tool_context_var: contextvars.ContextVar = contextvars.ContextVar("tool_context", default={})


def set_tool_context(context: Dict[str, Any]) -> None:
    """设置工具上下文，在工具执行前调用"""
    current = _tool_context_var.get()
    current.update(context)
    _tool_context_var.set(current)


def get_tool_context() -> Dict[str, Any]:
    """获取工具上下文"""
    return _tool_context_var.get()


@tool
def read_file(file_path: str) -> str:
    """
    读取指定文件的内容。

    只能读取当前工作目录下的文件，不允许访问系统其他目录。

    Args:
        file_path: 要读取的文件路径（相对于当前仓库根目录）

    Returns:
        str: 文件内容，如果文件不存在或读取失败返回错误信息
    """
    repo_path = get_tool_context().get("repo_path", "")
    if not repo_path:
        return "Error: 仓库路径未设置，请先克隆仓库"

    # 防止路径穿越
    full_path = os.path.abspath(os.path.join(repo_path, file_path))
    if not full_path.startswith(os.path.abspath(repo_path)):
        return f"Error: 路径 '{file_path}' 超出仓库范围，禁止访问"

    if not os.path.exists(full_path) or not os.path.isfile(full_path):
        return f"Error: 文件 '{file_path}' 不存在"

    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(full_path, 'r', encoding='gbk') as f:
                return f.read()
        except Exception as e:
            return f"Error: 读取文件失败: {str(e)}"
    except Exception as e:
        return f"Error: 读取文件失败: {str(e)}"


@tool
def write_file(file_path: str, content: str) -> str:
    """
    写入内容到指定文件。
    权限: ORCHESTRATOR_ONLY

    只能写入当前工作目录下的文件，不允许访问系统其他目录。
    如果文件已存在会被覆盖。

    Args:
        file_path: 要写入的文件路径（相对于当前仓库根目录）
        content: 要写入的文件内容

    Returns:
        str: 操作结果，成功返回"Success"，失败返回错误信息
    """
    repo_path = get_tool_context().get("repo_path", "")
    if not repo_path:
        return "Error: 仓库路径未设置，请先克隆仓库"

    # 防止路径穿越
    full_path = os.path.abspath(os.path.join(repo_path, file_path))
    if not full_path.startswith(os.path.abspath(repo_path)):
        return f"Error: 路径 '{file_path}' 超出仓库范围，禁止访问"

    # 确保目录存在
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    try:
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return "Success"
    except Exception as e:
        return f"Error: 写入文件失败: {str(e)}"


@tool
def search_files(pattern: str, file_type: Optional[str] = None) -> List[str]:
    """
    搜索仓库中匹配的文件。

    Args:
        pattern: 搜索模式，支持glob语法，例如"*.py"、"src/**/*.ts"
        file_type: 可选，文件类型过滤，例如"py"、"js"

    Returns:
        List[str]: 匹配的文件路径列表（相对于仓库根目录）
    """
    import glob

    repo_path = get_tool_context().get("repo_path", "")
    if not repo_path:
        return []

    if file_type:
        pattern = f"**/*.{file_type}"

    search_pattern = os.path.join(repo_path, pattern)
    matching_files = glob.glob(search_pattern, recursive=True)

    # 转换为相对路径
    relative_paths = [
        os.path.relpath(file_path, repo_path)
        for file_path in matching_files
        if os.path.isfile(file_path)
    ]

    return relative_paths


@tool
def search_code(keyword: str, file_type: str = "py") -> List[Dict]:
    """
    在代码中搜索关键词。

    Args:
        keyword: 要搜索的关键词
        file_type: 文件类型，默认为py

    Returns:
        List[Dict]: 搜索结果，每个元素包含file_path、line_number、line_content
    """
    results = []
    files = search_files.invoke({"pattern": f"**/*.{file_type}"})

    for file_path in files:
        try:
            content = read_file.invoke({"file_path": file_path})
            if content.startswith("Error:"):
                continue
            lines = content.split('\n')
            for line_num, line in enumerate(lines, 1):
                if keyword in line:
                    results.append({
                        "file_path": file_path,
                        "line_number": line_num,
                        "line_content": line.strip()
                    })
        except Exception as e:
            logger.warning(f"搜索文件 {file_path} 失败: {e}")
            continue

    return results


@tool
def clone_repository(clone_url: str, branch: str = "main") -> str:
    """
    克隆Git仓库到临时目录。

    Args:
        clone_url: 仓库的克隆URL，可以是HTTPS或SSH地址
        branch: 要克隆的分支名称，默认为main

    Returns:
        str: 本地仓库路径，如果克隆失败返回错误信息
    """
    try:
        # 创建临时目录
        temp_dir = tempfile.mkdtemp(prefix="spiderclaw_repo_")
        logger.info(f"克隆仓库 {clone_url} (分支: {branch}) 到 {temp_dir}")

        repo = Repo.clone_from(
            clone_url,
            temp_dir,
            branch=branch,
            depth=1  # 浅克隆，加快速度
        )

        # 保存到上下文
        ctx = get_tool_context()
        ctx["repo_path"] = temp_dir
        ctx["repo"] = repo
        _tool_context_var.set(ctx)

        return temp_dir
    except GitCommandError as e:
        logger.error(f"克隆仓库失败: {e}")
        return f"Error: 克隆仓库失败: {str(e)}"


@tool
def create_branch(branch_name: str) -> str:
    """
    在当前仓库创建并切换到新分支。

    Args:
        branch_name: 新分支的名称

    Returns:
        str: 操作结果，成功返回"Success"，失败返回错误信息
    """
    repo = get_tool_context().get("repo")
    if not repo:
        return "Error: 仓库未初始化，请先克隆仓库"

    try:
        # 检查分支是否已存在
        if branch_name in [h.name for h in repo.heads]:
            logger.warning(f"分支 {branch_name} 已存在，直接切换")
            repo.heads[branch_name].checkout()
        else:
            logger.info(f"创建新分支: {branch_name}")
            new_branch = repo.create_head(branch_name)
            new_branch.checkout()
        return "Success"
    except GitCommandError as e:
        return f"Error: 创建分支失败: {str(e)}"


@tool
def commit_changes(message: str, files: Optional[List[str]] = None,
                   author_name: str = "SpiderClaw AutoFix",
                   author_email: str = "spiderclaw@example.com") -> str:
    """
    提交仓库变更（仅暂存指定的文件）。

    Args:
        message: 提交信息
        files: 要提交的文件列表（相对于仓库根目录）。如为None则提交所有变更。
        author_name: 提交者名称，默认为"SpiderClaw AutoFix"
        author_email: 提交者邮箱，默认为"spiderclaw@example.com"

    Returns:
        str: 操作结果，成功返回"Success"，失败返回错误信息
    """
    repo = get_tool_context().get("repo")
    if not repo:
        return "Error: 仓库未初始化，请先克隆仓库"

    try:
        # 添加需要提交的文件（如未指定则添加所有）
        if files:
            for file_path in files:
                repo.git.add(file_path)
        else:
            repo.git.add(A=True)

        # 检查是否有变更需要提交
        if repo.is_dirty(untracked_files=True):
            logger.info(f"提交变更: {message}")
            repo.config_writer().set_value("user", "name", author_name).release()
            repo.config_writer().set_value("user", "email", author_email).release()
            repo.index.commit(message)
            return "Success"
        else:
            return "Warning: 没有需要提交的变更"
    except GitCommandError as e:
        return f"Error: 提交变更失败: {str(e)}"


@tool
def get_diff(base_branch: str = "main") -> str:
    """
    获取当前分支与基准分支的diff内容。

    Args:
        base_branch: 基准分支名称，默认为main

    Returns:
        str: diff内容，如果获取失败返回错误信息
    """
    repo = get_tool_context().get("repo")
    if not repo:
        return "Error: 仓库未初始化，请先克隆仓库"

    try:
        # 确保基准分支存在
        if base_branch not in [h.name for h in repo.heads]:
            # 如果本地没有基准分支，从远程获取
            repo.git.fetch("origin", base_branch)

        diff = repo.git.diff(f"{base_branch}...HEAD")
        return diff
    except GitCommandError as e:
        return f"Error: 获取diff失败: {str(e)}"


@tool
def download_ci_logs(logs_url: str) -> str:
    """
    下载GitHub Actions CI日志。

    Args:
        logs_url: CI日志的下载URL，支持以下格式：
        - https://github.com/{owner}/{repo}/runs/{job_id}... (网页URL)
        - https://api.github.com/repos/{owner}/{repo}/actions/jobs/{job_id}/logs (API URL)
        - https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/logs (workflow zip URL)

    Returns:
        str: 日志内容，如果下载失败返回错误信息
    """
    github_token = get_tool_context().get("github_token", "")

    try:
        logger.info(f"下载CI日志: {logs_url}")

        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)

        # 配置SSL验证
        session.verify = os.getenv("SSL_VERIFY", "true").lower() != "false"

        if github_token:
            session.headers.update({
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json"
            })

        # 处理不同格式的URL，转换为正确的job日志API URL
        import re

        # 匹配 GitHub 网页格式：https://github.com/{owner}/{repo}/runs/{job_id}...
        web_pattern = r'https?://github\.com/([^/]+)/([^/]+)/runs/(\d+)'
        web_match = re.match(web_pattern, logs_url)
        if web_match:
            owner, repo, job_id = web_match.groups()
            logs_url = f"https://api.github.com/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
            logger.info(f"转换为API URL: {logs_url}")

        response = session.get(logs_url, stream=True, timeout=30)
        response.raise_for_status()

        # 创建临时目录保存日志
        temp_dir = tempfile.mkdtemp(prefix="spiderclaw_logs_")

        # 如果是zip文件，先解压
        if "zip" in response.headers.get("content-type", "") or logs_url.endswith(".zip"):
            zip_path = os.path.join(temp_dir, "logs.zip")
            with open(zip_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            # 解压zip文件
            extract_dir = os.path.join(temp_dir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_dir)

            # 合并所有日志文件
            log_content = []
            for root, _, files in os.walk(extract_dir):
                for file in files:
                    if file.endswith(".txt") or file.endswith(".log"):
                        file_path = os.path.join(root, file)
                        try:
                            with open(file_path, "r", encoding="utf-8") as f:
                                log_content.append(f"=== {file} ===\n")
                                log_content.append(f.read())
                        except UnicodeDecodeError:
                            try:
                                with open(file_path, "r", encoding="gbk") as f:
                                    log_content.append(f"=== {file} ===\n")
                                    log_content.append(f.read())
                            except Exception as e:
                                logger.warning(f"读取日志文件 {file_path} 失败: {e}")
                                continue

            return "\n".join(log_content)
        else:
            # 直接返回文本日志
            return response.text

    except Exception as e:
        logger.error(f"下载CI日志失败: {e}")
        return f"Error: 下载CI日志失败: {str(e)}"


@tool
def parse_python_errors(log_content: str) -> List[Dict]:
    """
    解析Python Traceback错误信息。

    Args:
        log_content: CI日志内容

    Returns:
        List[Dict]: 错误列表，每个元素包含file_path、line_number、error_type、error_message、traceback等字段
    """
    errors = []

    # 预处理：移除每行开头的时间戳前缀（如 2026-04-24T16:24:21.8873818Z ）
    import re
    timestamp_pattern = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s+'
    original_lines = log_content.split('\n')
    processed_lines = []
    for line in original_lines:
        # 移除时间戳前缀
        processed_line = re.sub(timestamp_pattern, '', line)
        processed_lines.append(processed_line)
    processed_content = '\n'.join(processed_lines)

    # 匹配Traceback块
    traceback_pattern = re.compile(
        r'Traceback \(most recent call last\):\n'
        r'(?:  File "([^"]+)", line (\d+)[^\n]*\n.*?\n)+?'
        r'([^\n:]+): (.*?)\n',
        re.DOTALL | re.MULTILINE
    )

    # 匹配没有Traceback头部的语法错误（如IndentationError、SyntaxError等）
    syntax_error_pattern = re.compile(
        r'^File "([^"]+)", line (\d+)[^\n]*\n'
        r'(?:.*?\n)*?'
        r'([A-Z][a-zA-Z0-9]*Error): (.*?)$',
        re.MULTILINE
    )

    # 匹配简单错误行，支持多种前缀格式
    simple_error_pattern = re.compile(
        r'^(?:E\s+|ERROR:\s+|\[ERROR\]\s+|ERROR\s+\|\s+)?([A-Z][a-zA-Z0-9]*Error):?\s*(.*)$',
        re.MULTILINE
    )

    # 匹配pytest失败总结行
    pytest_failure_pattern = re.compile(
        r'^FAILED\s+.*?::.*?\s+-\s+([A-Z][a-zA-Z0-9]*Error):?\s*(.*)$',
        re.MULTILINE
    )

    # 匹配pytest失败详情格式（短测试摘要之后的详细traceback段）
    pytest_detail_pattern = re.compile(
        r'_{10,}\s+FAILED.*?_{10,}\n.*?([A-Z][a-zA-Z0-9]*Error): (.*?)\n',
        re.DOTALL
    )

    # 匹配Flask错误页面片段（如 ValueError: ... / werkzeug.exceptions...）
    flask_error_pattern = re.compile(
        r'(?:werkzeug\.|flask\.).*?([A-Z][a-zA-Z0-9]*(?:Error|Exception)): (.*?)$',
        re.MULTILINE
    )

    # 匹配Django错误页面片段
    django_error_pattern = re.compile(
        r'(?:django\.core\.|django\.db\.).*?([A-Z][a-zA-Z0-9]*(?:Error|Exception)): (.*?)$',
        re.MULTILINE
    )

    # 匹配ERROR: 开头的非标准错误行
    error_prefix_pattern = re.compile(
        r'^ERROR:\s*(.*)$',
        re.MULTILINE
    )

    # 匹配裸 Error: 后跟描述的格式（非标准但常见）
    bare_error_pattern = re.compile(
        r'^Error:\s*(.*?)$',
        re.MULTILINE
    )

    # 匹配 "ERROR: ..." 格式（CI 常见）
    error_generic = re.compile(
        r'^ERROR:\s*(.*?)(?:\s*$)',
        re.MULTILINE
    )

    # 匹配 pytest 失败详情
    pytest_detail = re.compile(
        r'E\s+([A-Z][a-zA-Z0-9]*Error):\s*(.*?)$',
        re.MULTILINE
    )

    # 匹配 traceback 的简写形式
    traceback_short = re.compile(
        r'([A-Z][a-zA-Z0-9]*Error):\s*(.*?)\n\s*at\s+(\S+)',
        re.MULTILINE
    )

    # 提取所有Traceback
    for match in traceback_pattern.finditer(processed_content):
        full_traceback = match.group(0)
        error_type = match.group(3)
        error_message = match.group(4)

        # 提取最准确的错误位置（Traceback的最后一个文件）
        last_file_match = re.findall(r'File "([^"]+)", line (\d+)', full_traceback)[-1]
        file_path, line_number = last_file_match[0], int(last_file_match[1])

        # 过滤系统路径，只保留用户项目文件
        is_system_file = (
            file_path.startswith("/")
            and not file_path.startswith("/github/workspace/")
            and not file_path.startswith("./")
            and not os.path.isabs(file_path) is False
        ) or "python" in file_path.lower() and "lib" in file_path.lower()

        if not is_system_file:
            errors.append({
                "type": "traceback",
                "file_path": file_path,
                "line_number": line_number,
                "error_type": error_type,
                "error_message": error_message,
                "traceback": full_traceback
            })

    # 提取没有Traceback头部的语法错误
    for match in syntax_error_pattern.finditer(processed_content):
        full_traceback = match.group(0)
        file_path = match.group(1)
        line_number = int(match.group(2))
        error_type = match.group(3)
        error_message = match.group(4)

        # 避免重复添加
        error_key = (file_path, line_number, error_type, error_message)
        existing_keys = {(e["file_path"], e["line_number"], e["error_type"], e["error_message"]) for e in errors}
        if error_key not in existing_keys:
            # 过滤系统路径，只保留用户项目文件
            is_system_file = (
                file_path.startswith("/")
                and not file_path.startswith("/github/workspace/")
                and not file_path.startswith("./")
                and not os.path.isabs(file_path) is False
            ) or "python" in file_path.lower() and "lib" in file_path.lower()

            if not is_system_file:
                errors.append({
                    "type": "syntax_error",
                    "file_path": file_path,
                    "line_number": line_number,
                    "error_type": error_type,
                    "error_message": error_message,
                    "traceback": full_traceback
                })

    # 提取简单错误（避免重复）
    existing_errors = {(e["error_type"], e["error_message"]) for e in errors}
    for match in simple_error_pattern.finditer(processed_content):
        error_type = match.group(1)
        error_message = match.group(2)
        file_path = ""
        line_number = 0

        # 专门处理语法错误（SyntaxError、IndentationError、TabError等）：尝试从上下文中提取文件路径
        if error_type in ["SyntaxError", "IndentationError", "TabError"]:
            # 获取当前匹配行的位置
            match_pos = processed_content.find(match.group(0))
            if match_pos != -1:
                # 向前查找1000个字符，寻找文件路径模式
                context_start = max(0, match_pos - 1000)
                context = processed_content[context_start:match_pos]
                # 匹配 File "xxx.py", line xx 模式
                file_match = re.search(r'File "([^"]+\.py)", line (\d+)', context)
                if file_match and file_match.group(1) != "<string>":
                    file_path = file_match.group(1)
                    line_number = int(file_match.group(2))
                # 匹配CI日志中的语法错误标记行：❌ 语法错误: xxx.py
                else:
                    ci_syntax_match = re.search(r'语法错误:\s*([a-zA-Z0-9_\-/\\.]+\.py)', context)
                    if ci_syntax_match:
                        file_path = ci_syntax_match.group(1)
                        # 尝试从错误信息中提取行号
                        line_match = re.search(r'line (\d+)', match.group(0))
                        if line_match:
                            line_number = int(line_match.group(1))
                # 匹配直接的文件名行，如 "File "/path/to/file.py""
                if not file_path:
                    simple_file_match = re.search(r'^.*?([a-zA-Z0-9_\-/\\]+\.py)', context, re.MULTILINE)
                    if simple_file_match:
                        file_path = simple_file_match.group(1)

        if (error_type, error_message) not in existing_errors:
            existing_errors.add((error_type, error_message))
            # 过滤系统路径，只保留用户项目文件
            is_system_file = (
                file_path.startswith("/")
                and not file_path.startswith("/github/workspace/")
                and not file_path.startswith("./")
                and not os.path.isabs(file_path) is False
            ) or "python" in file_path.lower() and "lib" in file_path.lower()

            if not is_system_file:
                errors.append({
                    "type": "simple",
                    "file_path": file_path,
                    "line_number": line_number,
                    "error_type": error_type,
                    "error_message": error_message,
                    "traceback": f"{error_type}: {error_message}"
                })

    # 提取pytest失败总结行
    for match in pytest_failure_pattern.finditer(processed_content):
        error_type = match.group(1)
        error_message = match.group(2)

        if (error_type, error_message) not in existing_errors:
            existing_errors.add((error_type, error_message))
            errors.append({
                "type": "pytest",
                "file_path": "",
                "line_number": 0,
                "error_type": error_type,
                "error_message": error_message,
                "traceback": f"{error_type}: {error_message}"
            })

    # 提取ERROR: 开头的错误行
    for match in error_prefix_pattern.finditer(processed_content):
        error_message = match.group(1).strip()
        error_type = "UnknownError"

        # 尝试从错误信息中提取具体的错误类型
        import re
        error_type_match = re.match(r'([A-Z][a-zA-Z0-9]*Error):', error_message)
        if error_type_match:
            error_type = error_type_match.group(1)
            # 移除错误类型前缀
            error_message = error_message[len(error_type) + 1:].strip()

        if (error_type, error_message) not in existing_errors:
            existing_errors.add((error_type, error_message))
            errors.append({
                "type": "simple",
                "file_path": "",
                "line_number": 0,
                "error_type": error_type,
                "error_message": error_message,
                "traceback": f"{error_type}: {error_message}"
            })

    # 提取pytest失败详情（短测试摘要后的详细traceback段）
    for match in pytest_detail_pattern.finditer(processed_content):
        error_type = match.group(1)
        error_message = match.group(2).strip()

        # 从匹配上下文中提取文件路径
        context_start = max(0, match.start() - 500)
        context = processed_content[context_start:match.start()]
        file_path = ""
        line_number = 0
        file_match = re.search(r'File "([^"]+\.py)", line (\d+)', context)
        if file_match and file_match.group(1) != "<string>":
            file_path = file_match.group(1)
            line_number = int(file_match.group(2))

        if (error_type, error_message) not in existing_errors:
            existing_errors.add((error_type, error_message))
            errors.append({
                "type": "pytest_detail",
                "file_path": file_path,
                "line_number": line_number,
                "error_type": error_type,
                "error_message": error_message,
                "traceback": f"{error_type}: {error_message}"
            })

    # 提取Flask框架错误
    for match in flask_error_pattern.finditer(processed_content):
        error_type = match.group(1)
        error_message = match.group(2).strip()
        if (error_type, error_message) not in existing_errors:
            existing_errors.add((error_type, error_message))
            errors.append({
                "type": "framework",
                "file_path": "",
                "line_number": 0,
                "error_type": error_type,
                "error_message": error_message,
                "traceback": f"{error_type}: {error_message}"
            })

    # 提取Django框架错误
    for match in django_error_pattern.finditer(processed_content):
        error_type = match.group(1)
        error_message = match.group(2).strip()
        if (error_type, error_message) not in existing_errors:
            existing_errors.add((error_type, error_message))
            errors.append({
                "type": "framework",
                "file_path": "",
                "line_number": 0,
                "error_type": error_type,
                "error_message": error_message,
                "traceback": f"{error_type}: {error_message}"
            })

    # 提取裸 Error: 后跟描述的格式
    for match in bare_error_pattern.finditer(processed_content):
        error_message = match.group(1).strip()
        if not error_message:
            continue

        error_type = "UnknownError"
        # 尝试提取具体的错误类型
        error_type_match = re.match(r'([A-Z][a-zA-Z0-9]*(?:Error|Exception|Warning)):?\s*', error_message)
        if error_type_match:
            error_type = error_type_match.group(1)
            error_message = error_message[error_type_match.end():].strip()

        # 提取变量名或缺失模块名等关键线索
        key_clue = ""
        # NameError线索: name 'xxx' is not defined
        name_match = re.search(r"name '(\w+)'", error_message)
        if name_match:
            key_clue = f" (变量: {name_match.group(1)})"
        # ImportError线索: No module named 'xxx'
        module_match = re.search(r"No module named '?(\w+)'?", error_message)
        if module_match:
            key_clue = f" (缺失模块: {module_match.group(1)})"

        if (error_type, error_message) not in existing_errors:
            existing_errors.add((error_type, error_message))
            errors.append({
                "type": "bare_error",
                "file_path": "",
                "line_number": 0,
                "error_type": error_type,
                "error_message": error_message + key_clue,
                "traceback": f"{error_type}: {error_message}"
            })

    # 兜底解析：如果没有找到任何错误，扫描包含错误关键词的行
    if not errors:
        # 错误关键词
        error_keywords = ["Error", "ERROR", "FAILED", "Traceback"]
        added_contexts = set()  # 避免重复添加相同的上下文

        for i, line in enumerate(processed_lines):
            # 检查是否包含错误关键词
            if any(keyword in line for keyword in error_keywords):
                # 获取前后各3行，注意边界
                start = max(0, i - 3)
                end = min(len(processed_lines), i + 4)  # +4因为切片是左闭右开
                context_lines = processed_lines[start:end]
                context_str = '\n'.join(context_lines)

                # 避免重复添加相同的上下文
                if context_str not in added_contexts:
                    added_contexts.add(context_str)
                    # 提取错误信息（取当前行作为错误信息）
                    error_message = line.strip()
                    errors.append({
                        "type": "unknown",
                        "file_path": "",
                        "line_number": 0,
                        "error_type": "UnknownError",
                        "error_message": error_message,
                        "traceback": context_str
                    })

    return errors


@tool
def run_tests(test_command: str = "pytest") -> str:
    """
    在仓库目录下运行测试命令。

    Args:
        test_command: 测试命令，默认为"pytest"

    Returns:
        str: 测试输出内容，如果运行失败返回错误信息
    """
    repo_path = get_tool_context().get("repo_path", "")
    if not repo_path:
        return "Error: 仓库路径未设置，请先克隆仓库"

    try:
        logger.info(f"运行测试命令: {test_command}")

        result = subprocess.run(
            test_command,
            shell=True,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=300  # 5分钟超时
        )

        output = f"Exit code: {result.returncode}\n\n"
        if result.stdout:
            output += f"STDOUT:\n{result.stdout}\n"
        if result.stderr:
            output += f"STDERR:\n{result.stderr}\n"

        return output
    except subprocess.TimeoutExpired:
        return "Error: 测试运行超时（超过5分钟）"
    except Exception as e:
        return f"Error: 运行测试失败: {str(e)}"


@tool
def push_branch(branch_name: str, remote_name: str = "origin") -> str:
    """
    推送本地分支到远程仓库。
    权限: ORCHESTRATOR_ONLY

    Args:
        branch_name: 要推送的分支名称
        remote_name: 远程仓库名称，默认为"origin"

    Returns:
        str: 操作结果，成功返回"Success"，失败返回错误信息
    """
    repo_path = get_tool_context().get("repo_path", "")
    if not repo_path:
        return "Error: 仓库路径未设置，请先克隆仓库"

    try:
        from git import Repo, GitCommandError
        repo = Repo(repo_path)
        logger.info(f"推送分支 {branch_name} 到 {remote_name}")
        origin = repo.remote(name=remote_name)
        origin.push(refspec=f"HEAD:refs/heads/{branch_name}")
        return "Success"
    except GitCommandError as e:
        return f"Error: 推送分支失败: {str(e)}"
    except Exception as e:
        return f"Error: 推送分支失败: {str(e)}"


@tool
def create_pull_request(
    repo_full_name: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str
) -> str:
    """
    在GitHub上创建Pull Request。
    权限: ORCHESTRATOR_ONLY

    Args:
        repo_full_name: 仓库全名（owner/repo）
        head_branch: 源分支名称
        base_branch: 目标分支名称
        title: PR标题
        body: PR描述内容

    Returns:
        str: 创建成功返回PR URL，失败返回错误信息
    """
    github_token = get_tool_context().get("github_token", "")
    if not github_token:
        return "Error: GitHub Token未设置"

    try:
        from github import Github, GithubException
        logger.info(f"创建PR: {repo_full_name} {head_branch} → {base_branch}")

        # 遵循SSL_VERIFY环境变量（与download_ci_logs保持一致）
        verify_ssl = os.getenv("SSL_VERIFY", "true").lower() != "false"
        g = Github(github_token, verify=verify_ssl)
        repo = g.get_repo(repo_full_name)

        pr = repo.create_pull(
            title=title,
            body=body,
            head=head_branch,
            base=base_branch
        )

        return pr.html_url
    except GithubException as e:
        return f"Error: 创建PR失败: {str(e)}"
    except Exception as e:
        return f"Error: 创建PR失败: {str(e)}"


@tool
def execute_python_code(file_path: str, timeout: int = 10) -> str:
    """
    在隔离子进程中执行指定的 Python 文件，捕获异常并返回结果。
    用于验证修复后的代码是否能正常运行。

    Args:
        file_path: 要执行的 Python 文件路径（相对于仓库根目录）
        timeout: 执行超时时间（秒），默认 10 秒

    Returns:
        str: JSON格式的执行结果 {success, exit_code, stdout, stderr, error_type, error_message, error_line}
    """
    import json

    repo_path = get_tool_context().get("repo_path", "")
    if not repo_path:
        return json.dumps({"success": False, "error_message": "仓库路径未设置"}, ensure_ascii=False)

    full_path = os.path.abspath(os.path.join(repo_path, file_path))
    if not full_path.startswith(os.path.abspath(repo_path)):
        return json.dumps({"success": False, "error_message": f"路径超出仓库范围: {file_path}"}, ensure_ascii=False)

    if not os.path.exists(full_path):
        return json.dumps({"success": False, "error_message": f"文件不存在: {file_path}"}, ensure_ascii=False)

    try:
        result = subprocess.run(
            ["python", full_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=repo_path,
            encoding='utf-8',
            errors='replace',
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        error_type = ""
        error_message = ""
        error_line = 0

        stderr = result.stderr
        if result.returncode != 0 and stderr:
            # 匹配 Traceback 中的异常信息（全文搜索）
            tb_match = re.search(
                r"(\w+Error):\s*(.+)",
                stderr,
            )
            if tb_match:
                error_type = tb_match.group(1)
                error_message = tb_match.group(2).strip()

            # 匹配失败行的行号
            line_match = re.search(r"line (\d+)", stderr)
            if line_match:
                error_line = int(line_match.group(1))

        return json.dumps({
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout[:5000] if result.stdout else "",
            "stderr": stderr[:5000] if stderr else "",
            "error_type": error_type,
            "error_message": error_message,
            "error_line": error_line,
        }, ensure_ascii=False)

    except subprocess.TimeoutExpired:
        return json.dumps({
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"执行超时（{timeout}秒）",
            "error_type": "TimeoutError",
            "error_message": f"代码执行超过 {timeout} 秒",
            "error_line": 0,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_line": 0,
        }, ensure_ascii=False)


# 导出所有工具
all_tools = [
    read_file,
    write_file,
    search_files,
    search_code,
    clone_repository,
    create_branch,
    commit_changes,
    get_diff,
    download_ci_logs,
    parse_python_errors,
    run_tests,
    push_branch,
    create_pull_request,
    execute_python_code,
]
