"""版本管理 — 确保仓库可用并 checkout 到配置的版本"""
import logging
import os
import asyncio
from git import Repo, GitCommandError

logger = logging.getLogger(__name__)


async def ensure_repo_with_version(
    repo_url: str,
    local_path: str,
    version: str,
    branch: str = "main",
) -> tuple[str, bool]:
    """确保仓库可用并 checkout 到指定版本

    策略：
    1. 本地不存在 → clone → checkout version
    2. 本地已存在 → fetch → checkout version
    3. checkout 失败 → 降级到最新 branch（返回 degraded=True）

    调用方（编排器）已保证 version 非空。
    此函数不做版本猜测，只负责 checkout 到指定版本。

    Args:
        repo_url: Git 仓库 URL
        local_path: 本地持久化路径
        version: Git commit SHA 或 tag（必填）
        branch: 降级时使用的分支名

    Returns:
        (repo_path, degraded) — degraded=True 表示未能 checkout 到指定版本
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _ensure_repo_sync, repo_url, local_path, version, branch
    )


def _ensure_repo_sync(
    repo_url: str,
    local_path: str,
    version: str,
    branch: str,
) -> tuple[str, bool]:
    """同步版本的仓库确保逻辑（在线程池中执行）"""
    local_path = os.path.abspath(local_path)
    git_dir = os.path.join(local_path, ".git")

    # 本地不存在 → clone
    if not os.path.exists(git_dir):
        logger.info(f"首次 clone: {repo_url} -> {local_path}")
        os.makedirs(local_path, exist_ok=True)
        try:
            Repo.clone_from(repo_url, local_path)
            logger.info(f"clone 完成，当前分支: {branch}")
        except GitCommandError as e:
            logger.error(f"clone 失败: {e}")
            raise

    repo = Repo(local_path)
    current = _current_branch(repo)

    # version == branch 且已在该分支上 → 跳过本地 checkout，直接尝试网络更新
    if version == branch and current == branch:
        logger.info(f"已在目标分支 {branch} 上，跳过本地 checkout")
    else:
        # 尝试本地 checkout
        try:
            repo.git.checkout(version)
            logger.info(f"checkout 到配置版本: {version}")
        except GitCommandError as e:
            logger.warning(f"本地无法 checkout 到 {version}: {e}，降级到最新 {branch}")
            try:
                repo.git.checkout(branch)
                logger.info(f"已降级到分支 {branch}")
            except GitCommandError as e2:
                logger.warning(f"checkout {branch} 也失败: {e2}，使用当前代码")
                return local_path, True

    # 尝试 fetch + pull 更新（网络失败不影响已有代码）
    try:
        repo.git.fetch("origin")
        logger.info("fetch 成功")
    except GitCommandError as e:
        logger.info(f"网络 fetch 失败: {e}，使用本地代码")
        return local_path, False

    try:
        repo.git.checkout(version)
        repo.git.pull("origin", version)
        logger.info(f"已更新到最新 {version}")
        return local_path, False
    except GitCommandError as e:
        logger.info(f"网络更新失败: {e}，使用本地代码")
        return local_path, False


def _current_branch(repo: Repo) -> str:
    """获取当前分支名，detached HEAD 返回空字符串"""
    try:
        return repo.active_branch.name
    except TypeError:
        return ""


async def pre_sync_repos(services: list) -> None:
    """启动时预同步所有注册服务的仓库，确保本地有可用代码"""
    for svc in services:
        if not svc.repo_url or not svc.repo_local_path:
            continue
        try:
            await ensure_repo_with_version(
                repo_url=svc.repo_url,
                local_path=svc.repo_local_path,
                version=svc.version,
                branch=svc.git_branch,
            )
            logger.info(f"预同步完成: {svc.name}")
        except Exception as e:
            logger.warning(f"预同步失败 {svc.name}: {e}")
