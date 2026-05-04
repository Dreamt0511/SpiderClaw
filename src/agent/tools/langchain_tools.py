"""LangChain标准工具定义"""
import os
import tempfile
import subprocess
import re
from typing import Optional, List, Dict, Any, Callable
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
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

    支持隐藏目录（如 .github/），解决 glob.glob 默认跳过 dot 目录的问题。

    Args:
        pattern: 搜索模式，支持glob语法，例如"*.py"、"src/**/*.ts"
        file_type: 可选，文件类型过滤，例如"py"、"js"

    Returns:
        List[str]: 匹配的文件路径列表（相对于仓库根目录）
    """
    import glob as glob_module

    repo_path = get_tool_context().get("repo_path", "")
    if not repo_path:
        return []

    ext = f".{file_type}" if file_type else None

    matching_files = []
    for root, dirs, files in os.walk(repo_path):
        # 跳过 .git 目录
        if ".git" in root.split(os.sep):
            continue
        for f in files:
            if ext and not f.endswith(ext):
                continue
            full_path = os.path.join(root, f)
            if ext:
                matching_files.append(full_path)
            else:
                # fnmatch 不支持 **/ 通配符，转换为普通通配符
                match_pattern = pattern
                if match_pattern.startswith("**/"):
                    match_pattern = match_pattern[3:]
                if glob_module.fnmatch.fnmatch(
                    os.path.relpath(full_path, repo_path), match_pattern
                ):
                    matching_files.append(full_path)

    # 转换为相对路径，统一使用 / 分隔符
    relative_paths = [
        os.path.relpath(file_path, repo_path).replace("\\", "/")
        for file_path in matching_files
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


def _is_system_path(file_path: str) -> bool:
    """判断文件路径是否为 Python 标准库或系统路径，而非项目文件。"""
    if not file_path:
        return False

    # 系统/Python 安装路径特征
    SYSTEM_INDICATORS = ("/lib/python", "\\lib\\python", "/site-packages/", "\\site-packages\\")

    lower = file_path.lower()

    # 包含 Python 标准库路径特征 → 系统文件
    if any(indicator in lower for indicator in SYSTEM_INDICATORS):
        return True

    return False


@tool
def parse_python_errors(log_content: str) -> List[Dict]:
    """
    解析Python Traceback错误信息，适配 SpiderClaw CI 多阶段多文件检查格式。

    支持:
    - 标准 Python Traceback / SyntaxError / 简单错误行
    - CI ::group::检查: <file> 文件组格式（运行时检查阶段）
    - CI 路径前缀（/github/workspace/ 等）自动剥离
    - 多文件同名错误独立捕获（不因去重丢失跨文件错误）

    Args:
        log_content: CI日志内容

    Returns:
        List[Dict]: 错误列表
    """
    import re

    # ===================== 1. 预处理 =====================
    timestamp_pattern = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s+'
    original_lines = log_content.split('\n')
    processed_lines = []
    for line in original_lines:
        processed_line = re.sub(timestamp_pattern, '', line)
        processed_lines.append(processed_line)
    processed_content = '\n'.join(processed_lines)

    # ANSI 码清理
    processed_content = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', processed_content)

    # ===================== 2. CI 路径标准化 =====================
    def _normalize_ci_path(fp: str) -> str:
        """将 CI 环境绝对路径转换为相对路径

        处理的 CI 路径格式:
        - /github/workspace/src/file.py        → src/file.py
        - /home/runner/work/repo/repo/./f.py   → f.py
        - /home/runner/work/repo/./f.py         → f.py
        """
        for prefix in ["/github/workspace/", "/home/runner/work/"]:
            if fp.startswith(prefix):
                stripped = fp[len(prefix):]
                # 处理 /home/runner/work/{repo}/{repo}/./path 格式
                # 找到 /./ 标记，取其后的部分即相对于仓库根目录的路径
                dot_slash = stripped.find('/./')
                if dot_slash != -1:
                    stripped = stripped[dot_slash + 3:]  # 跳过 "/./"
                # 去除开头的 ./
                if stripped.startswith('./'):
                    stripped = stripped[2:]
                return stripped
        # 处理 <string> 和纯相对路径
        if fp.startswith('./'):
            return fp[2:]
        return fp

    # ===================== 3. 核心模式匹配 =====================
    def _match_patterns(content: str, ci_stage: str = "") -> List[Dict]:
        """对给定内容运行所有错误匹配模式，返回错误列表"""
        local_errors: List[Dict] = []

        # ----- 3a. 编译正则 -----

        # 标准 Traceback
        traceback_pattern = re.compile(
            r'Traceback \(most recent call last\):\n'
            r'(?:  File "([^"]+)", line (\d+)[^\n]*\n.*?\n)+?'
            r'([^\n:]+): (.*?)\n',
            re.DOTALL | re.MULTILINE,
        )

        # 语法错误（无 Traceback 头部）
        syntax_error_pattern = re.compile(
            r'^File "([^"]+)", line (\d+)[^\n]*\n'
            r'(?:.*?\n)*?'
            r'([A-Z][a-zA-Z0-9]*Error): (.*?)$',
            re.MULTILINE,
        )

        # 简单错误行（支持 E / ERROR: 前缀）
        simple_error_pattern = re.compile(
            r'^(?:E\s+|ERROR:\s+|\[ERROR\]\s+|ERROR\s+\|\s+)?'
            r'([A-Z][a-zA-Z0-9]*Error):?\s*(.*)$',
            re.MULTILINE,
        )

        # Python 非交互模式 SyntaxError（如: SyntaxError: invalid char (file.py, line N)）
        syntax_inline_pattern = re.compile(
            r'^([A-Z][a-zA-Z0-9]*Error): (.*?)\(([^)]+\.py),\s*line\s+(\d+)\)\s*$',
            re.MULTILINE,
        )

        # pytest 失败总结行
        pytest_failure_pattern = re.compile(
            r'^FAILED\s+.*?::.*?\s+-\s+([A-Z][a-zA-Z0-9]*Error):?\s*(.*)$',
            re.MULTILINE,
        )

        # pytest 失败详情段
        pytest_detail_pattern = re.compile(
            r'_{10,}\s+FAILED.*?_{10,}\n.*?([A-Z][a-zA-Z0-9]*Error): (.*?)\n',
            re.DOTALL,
        )

        # Flask 框架错误
        flask_error_pattern = re.compile(
            r'(?:werkzeug\.|flask\.).*?'
            r'([A-Z][a-zA-Z0-9]*(?:Error|Exception)): (.*?)$',
            re.MULTILINE,
        )

        # Django 框架错误
        django_error_pattern = re.compile(
            r'(?:django\.core\.|django\.db\.).*?'
            r'([A-Z][a-zA-Z0-9]*(?:Error|Exception)): (.*?)$',
            re.MULTILINE,
        )

        # ERROR: 前缀行
        error_prefix_pattern = re.compile(r'^ERROR:\s*(.*)$', re.MULTILINE)

        # 裸 Error: 前缀行
        bare_error_pattern = re.compile(r'^Error:\s*(.*?)$', re.MULTILINE)

        # ----- 3b. Traceback 匹配 -----
        for match in traceback_pattern.finditer(content):
            full_tb = match.group(0)
            error_type = match.group(3)
            error_message = match.group(4)

            last_file = re.findall(r'File "([^"]+)", line (\d+)', full_tb)[-1]
            file_path = _normalize_ci_path(last_file[0])
            line_number = int(last_file[1])

            if not _is_system_path(file_path):
                local_errors.append({
                    "type": "traceback",
                    "file_path": file_path,
                    "line_number": line_number,
                    "error_type": error_type,
                    "error_message": error_message,
                    "traceback": full_tb,
                    "ci_stage": ci_stage,
                })

        # ----- 3c. 语法错误匹配（去重键包含 file_path） -----
        existing_keys = {
            (e["file_path"], e["line_number"], e["error_type"], e["error_message"])
            for e in local_errors
        }
        for match in syntax_error_pattern.finditer(content):
            full_tb = match.group(0)
            file_path = _normalize_ci_path(match.group(1))
            line_number = int(match.group(2))
            error_type = match.group(3)
            error_message = match.group(4)

            key = (file_path, line_number, error_type, error_message)
            if key not in existing_keys and not _is_system_path(file_path):
                existing_keys.add(key)
                local_errors.append({
                    "type": "syntax_error",
                    "file_path": file_path,
                    "line_number": line_number,
                    "error_type": error_type,
                    "error_message": error_message,
                    "traceback": full_tb,
                    "ci_stage": ci_stage,
                })

        # ----- 3d. 简单错误行匹配（去重键改为 file_path+error_type+line_number） -----
        existing_err_set = {
            (e.get("file_path", ""), e["error_type"], e.get("line_number", 0))
            for e in local_errors
        }

        def _ek(fp, et, ln):
            return (fp or "", et or "", ln or 0)

        # ----- 3c2. 运行时日志格式（[ERROR] logger.name: message） -----
        # 适配 biz-server 通过 collector 上报的结构化日志行
        runtime_log_pattern = re.compile(
            r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+'
            r'\[(ERROR|WARNING)\]\s+'
            r'([\w.]+):\s+(.*)$',
            re.MULTILINE,
        )
        for match in runtime_log_pattern.finditer(content):
            level = match.group(1)
            logger_name = match.group(2)
            message = match.group(3)

            # logger 名 → 文件路径：尝试多种路径转换策略
            logger_parts = logger_name.split('.')
            # 策略1: 直接转换 (app.user_service -> app/user_service.py)
            direct_path = logger_name.replace('.', '/') + '.py'
            # 策略2: 去掉第一段 (app.user_service -> user_service.py)
            if len(logger_parts) > 1:
                short_path = '/'.join(logger_parts[1:]) + '.py'
            else:
                short_path = direct_path
            # 策略3: 尝试常见映射 (app -> src)
            src_path = 'src/' + short_path if not short_path.startswith('src/') else short_path

            # 将这些路径都记录下来，后续由 _filter_valid_errors 验证哪个真实存在
            candidate_paths = list(dict.fromkeys([direct_path, short_path, src_path]))
            file_path = candidate_paths[0]  # 默认使用第一个，_filter_valid_errors 会处理

            # 尝试从 message 中提取行号（如 "line 11"）
            line_number = 0
            ln_match = re.search(r'line\s+(\d+)', message)
            if ln_match:
                line_number = int(ln_match.group(1))

            error_type = "RuntimeError" if level == "ERROR" else "RuntimeWarning"

            key = _ek(file_path, error_type, line_number)
            if key not in existing_err_set:
                existing_err_set.add(key)
                local_errors.append({
                    "type": "runtime_log",
                    "file_path": file_path,
                    "line_number": line_number,
                    "error_type": error_type,
                    "error_message": message,
                    "traceback": f"{error_type}: {message}",
                    "ci_stage": ci_stage,
                })

        for match in simple_error_pattern.finditer(content):
            error_type = match.group(1)
            error_message = match.group(2)
            file_path = ""
            line_number = 0

            # 向前查找最近的 File 行（取最后一个，最接近匹配位置）
            match_pos = match.start()
            context = content[max(0, match_pos - 1000):match_pos]
            fms = re.findall(r'File "([^"]+\.py)", line (\d+)', context)
            if fms and fms[-1][0] != "<string>":
                file_path = _normalize_ci_path(fms[-1][0])
                line_number = int(fms[-1][1])

            key = _ek(file_path, error_type, line_number)
            if key not in existing_err_set:
                existing_err_set.add(key)
                if not _is_system_path(file_path):
                    local_errors.append({
                        "type": "simple",
                        "file_path": file_path,
                        "line_number": line_number,
                        "error_type": error_type,
                        "error_message": error_message,
                        "traceback": f"{error_type}: {error_message}",
                        "ci_stage": ci_stage,
                    })

        # ----- 3d2. Python 非交互模式 SyntaxError（如: SyntaxError: invalid char (file.py, line N)） -----
        for match in syntax_inline_pattern.finditer(content):
            error_type = match.group(1)
            error_message = match.group(2).strip()
            file_path = _normalize_ci_path(match.group(3))
            line_number = int(match.group(4))
            key = _ek(file_path, error_type, line_number)
            if key not in existing_err_set and not _is_system_path(file_path):
                existing_err_set.add(key)
                local_errors.append({
                    "type": "syntax_inline",
                    "file_path": file_path,
                    "line_number": line_number,
                    "error_type": error_type,
                    "error_message": error_message,
                    "traceback": f"{error_type}: {error_message} ({file_path}, line {line_number})",
                    "ci_stage": ci_stage,
                })

        # ----- 3e. pytest 失败总结行 -----
        for match in pytest_failure_pattern.finditer(content):
            et = match.group(1)
            em = match.group(2)
            key = _ek("", et, 0)
            if key not in existing_err_set:
                existing_err_set.add(key)
                local_errors.append({
                    "type": "pytest",
                    "file_path": "",
                    "line_number": 0,
                    "error_type": et,
                    "error_message": em,
                    "traceback": f"{et}: {em}",
                    "ci_stage": ci_stage or "test",
                })

        # ----- 3f. ERROR: 前缀行 -----
        for match in error_prefix_pattern.finditer(content):
            em = match.group(1).strip()
            et = "UnknownError"
            etm = re.match(r'([A-Z][a-zA-Z0-9]*Error):', em)
            if etm:
                et = etm.group(1)
                em = em[len(et) + 1:].strip()
            key = _ek("", et, 0)
            if key not in existing_err_set:
                existing_err_set.add(key)
                local_errors.append({
                    "type": "simple",
                    "file_path": "",
                    "line_number": 0,
                    "error_type": et,
                    "error_message": em,
                    "traceback": f"{et}: {em}",
                    "ci_stage": ci_stage,
                })

        # ----- 3g. pytest 失败详情段 -----
        for match in pytest_detail_pattern.finditer(content):
            et = match.group(1)
            em = match.group(2).strip()
            context = content[max(0, match.start() - 500):match.start()]
            fp = ""
            ln = 0
            fms = re.findall(r'File "([^"]+\.py)", line (\d+)', context)
            if fms and fms[-1][0] != "<string>":
                fp = _normalize_ci_path(fms[-1][0])
                ln = int(fms[-1][1])
            key = _ek(fp, et, ln)
            if key not in existing_err_set and not _is_system_path(fp):
                existing_err_set.add(key)
                local_errors.append({
                    "type": "pytest_detail",
                    "file_path": fp,
                    "line_number": ln,
                    "error_type": et,
                    "error_message": em,
                    "traceback": f"{et}: {em}",
                    "ci_stage": ci_stage or "test",
                })

        # ----- 3h. Flask / Django 框架错误 -----
        for pattern, stage in [(flask_error_pattern, ci_stage),
                                (django_error_pattern, ci_stage)]:
            for match in pattern.finditer(content):
                et = match.group(1)
                em = match.group(2).strip()
                key = _ek("", et, 0)
                if key not in existing_err_set:
                    existing_err_set.add(key)
                    local_errors.append({
                        "type": "framework",
                        "file_path": "",
                        "line_number": 0,
                        "error_type": et,
                        "error_message": em,
                        "traceback": f"{et}: {em}",
                        "ci_stage": stage,
                    })

        # ----- 3i. 裸 Error: 前缀行 -----
        for match in bare_error_pattern.finditer(content):
            em = match.group(1).strip()
            if not em:
                continue
            et = "UnknownError"
            etm = re.match(
                r'([A-Z][a-zA-Z0-9]*(?:Error|Exception|Warning)):?\s*', em
            )
            if etm:
                et = etm.group(1)
                em = em[etm.end():].strip()
            key = _ek("", et, 0)
            if key not in existing_err_set:
                existing_err_set.add(key)
                local_errors.append({
                    "type": "bare_error",
                    "file_path": "",
                    "line_number": 0,
                    "error_type": et,
                    "error_message": em,
                    "traceback": f"{et}: {em}",
                    "ci_stage": ci_stage,
                })

        return local_errors

    # ===================== 4. CI 文件组解析 =====================
    # 新的 CI 配置在"运行时检查"阶段为每个文件生成 ::group:: 块。
    # 每个文件组独立处理，确保跨文件的同名错误不会被去重丢失。
    all_errors: List[Dict] = []

    if '::group::检查:' in processed_content:
        # 按 ::group::检查: 分割：parts[0] 为组前内容（语法检查+测试），
        # parts[1:] 格式："<file>\n<content>...::endgroup::..."
        parts = re.split(r'::group::检查:\s*', processed_content)

        # 组前内容（语法检查阶段、测试阶段输出）
        if parts[0].strip():
            all_errors.extend(_match_patterns(parts[0], ci_stage="syntax"))

        # 每个文件组独立解析
        for part in parts[1:]:
            first_nl = part.find('\n')
            if first_nl == -1:
                continue
            file_path = part[:first_nl].strip()
            group_content = part[first_nl + 1:]
            # 移除 ::endgroup:: 行
            group_content = re.sub(
                r'::endgroup::.*(\n|$)', '', group_content
            ).strip()

            file_errors = _match_patterns(group_content, ci_stage="runtime")
            for e in file_errors:
                if not e.get("file_path") and file_path:
                    e["file_path"] = file_path
                # 文件组内的错误如果没有 ci_stage，标记为 runtime
                if not e.get("ci_stage"):
                    e["ci_stage"] = "runtime"
            all_errors.extend(file_errors)
    else:
        # 无文件组标记，整体处理（兼容旧格式）
        all_errors = _match_patterns(processed_content)

    # ===================== 5. 兜底解析 =====================
    if not all_errors:
        error_keywords = ["Error", "ERROR", "FAILED", "Traceback"]
        # 排除 CI 脚本中的变量设置语句（如 HAS_ERROR=0, ##[group]Run HAS_ERROR=0）
        ci_var_keywords = ["HAS_ERROR=", "HAS_WARNING=", "EXIT_CODE="]
        added_contexts = set()
        for i, line in enumerate(processed_lines):
            # 跳过 CI 变量赋值行
            if any(kw in line for kw in ci_var_keywords):
                continue
            if any(kw in line for kw in error_keywords):
                start = max(0, i - 3)
                end = min(len(processed_lines), i + 4)
                ctx = '\n'.join(processed_lines[start:end])
                if ctx not in added_contexts:
                    added_contexts.add(ctx)
                    all_errors.append({
                        "type": "unknown",
                        "file_path": "",
                        "line_number": 0,
                        "error_type": "UnknownError",
                        "error_message": line.strip(),
                        "traceback": ctx,
                        "ci_stage": "",
                    })

    errors = all_errors

    # ===================== 6. 错误链检测与合并 =====================
    merged_errors = []
    skip_indices: set = set()

    for i, err in enumerate(errors):
        if i in skip_indices:
            continue

        file_path = err.get("file_path", "")
        error_type = err.get("error_type", "")
        error_msg = err.get("error_message", "")

        if error_type == "ImportError":
            imp_file = file_path
            if not imp_file:
                m = re.search(r"'([^']+\.py)'", error_msg)
                if m:
                    imp_file = m.group(1)
            if not imp_file:
                m = re.search(r'"([^"]+\.py)"', error_msg)
                if m:
                    imp_file = m.group(1)

            for j, err2 in enumerate(errors):
                if j in skip_indices or j == i:
                    continue
                if err2.get("error_type") != "ModuleNotFoundError":
                    continue

                e2_fp = err2.get("file_path", "")
                e2_msg = err2.get("error_message", "")
                e2_file = e2_fp
                if not e2_file:
                    m = re.search(r"'([^']+\.py)'", e2_msg)
                    if m:
                        e2_file = m.group(1)

                paths_match = (
                    (file_path and e2_fp and file_path == e2_fp)
                    or (imp_file and e2_file and imp_file == e2_file)
                    or (imp_file and e2_fp and imp_file == e2_fp)
                    or (e2_file and file_path and e2_file == file_path)
                )
                adjacent = abs(i - j) == 1

                if paths_match or (not file_path and not e2_fp and adjacent):
                    err2["is_root_cause"] = True
                    err2["chain_consequence"] = error_msg
                    skip_indices.add(i)
                    break

        if i not in skip_indices:
            err.setdefault("is_root_cause", False)
            merged_errors.append(err)

    errors = merged_errors

    # ===================== 7. 最终去重 =====================
    # 改进键：(file_path, error_type, line_number) → 不同文件的同名错误独立保留
    seen: set = set()
    unique_errors = []
    for err in errors:
        key = (
            err.get("file_path", "") or "",
            err.get("error_type", "") or "",
            err.get("line_number", 0) or 0,
        )
        if key not in seen:
            seen.add(key)
            unique_errors.append(err)

    # ===================== 8. 字段补全 =====================
    for err in unique_errors:
        if not err.get("source") and err.get("type"):
            err["source"] = err["type"]
        err.setdefault("is_root_cause", False)
        err.setdefault("chain_consequence", "")
        err.setdefault("ci_stage", "")

    return unique_errors


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

    import requests

    logger.info(f"创建PR: {repo_full_name} {head_branch} → {base_branch}")

    verify_ssl = os.getenv("SSL_VERIFY", "true").lower() != "false"
    api_url = f"https://api.github.com/repos/{repo_full_name}/pulls"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    payload = {
        "title": title,
        "body": body,
        "head": head_branch,
        "base": base_branch,
    }

    try:
        resp = requests.post(api_url, json=payload, headers=headers, verify=verify_ssl, timeout=30)
        if resp.status_code in (200, 201):
            pr_url = resp.json().get("html_url", "")
            logger.info(f"PR创建成功: {pr_url}")
            return pr_url
        else:
            return f"Error: 创建PR失败 (HTTP {resp.status_code}): {resp.text[:300]}"
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


# ── 审计日志包装 ──

from src.utils.audit import audit_logger


def _wrap_tool(t, name=None):
    """Wrap BaseTool._run with audit logging (tool_call / tool_result).

    `BaseTool.run()` 通过 `_get_runnable_config_param()` 检查 _run 签名中
    是否有 `config` 参数名来决定是否隐式传递 config。包装器必须显式声明
    `config: RunnableConfig` 参数以保持检测链完整，然后透传给 orig_run。
    """
    tool_name = name or t.name
    orig_run = t._run

    def _run(*args, config: RunnableConfig, run_manager=None, **kwargs):
        log_args = {k: v for k, v in kwargs.items()}
        audit_logger.log_event("tool_call", tool=tool_name, args=log_args)
        try:
            result = orig_run(*args, config=config, run_manager=run_manager, **kwargs)
            audit_logger.log_event("tool_result", tool=tool_name, result_summary=str(result)[:500])
            return result
        except Exception as e:
            audit_logger.log_event("tool_result", tool=tool_name, result_summary=str(e)[:500], is_error=True)
            raise

    t._run = _run
    return t


# 包装所有工具（在 _run 级别注入审计日志）
read_file = _wrap_tool(read_file)
write_file = _wrap_tool(write_file)
search_files = _wrap_tool(search_files)
search_code = _wrap_tool(search_code)
clone_repository = _wrap_tool(clone_repository)
create_branch = _wrap_tool(create_branch)
commit_changes = _wrap_tool(commit_changes)
get_diff = _wrap_tool(get_diff)
download_ci_logs = _wrap_tool(download_ci_logs)
parse_python_errors = _wrap_tool(parse_python_errors)
run_tests = _wrap_tool(run_tests)
push_branch = _wrap_tool(push_branch)
create_pull_request = _wrap_tool(create_pull_request)
execute_python_code = _wrap_tool(execute_python_code)

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
