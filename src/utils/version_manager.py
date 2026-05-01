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

    # 本地不存在 → clone
    if not os.path.exists(os.path.join(local_path, ".git")):
        logger.info(f"首次 clone: {repo_url} -> {local_path}")
        os.makedirs(local_path, exist_ok=True)
        try:
            Repo.clone_from(repo_url, local_path)
        except GitCommandError as e:
            logger.error(f"clone 失败: {e}")
            raise

    repo = Repo(local_path)

    # fetch + checkout 到指定版本
    try:
        repo.git.fetch("origin")
        repo.git.checkout(version)
        logger.info(f"checkout 到配置版本: {version}")
        return local_path, False
    except GitCommandError:
        logger.warning(f"无法 checkout 到 {version}，降级到最新 {branch}")

    # 降级：checkout 到最新分支
    try:
        repo.git.checkout(branch)
        repo.git.pull("origin", branch)
        logger.info(f"降级到最新 {branch}")
    except GitCommandError as e:
        logger.warning(f"降级到 {branch} 也失败: {e}，使用本地现有代码")

    return local_path, True
