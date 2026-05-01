"""版本管理 — 确保仓库可用并 checkout 到正确版本（三级降级）"""
import logging
import os
import asyncio
from git import Repo, GitCommandError

logger = logging.getLogger(__name__)

# 版本未知的统一判断常量
VERSION_UNKNOWN = ("unknown", "", None)


def is_version_known(version: str) -> bool:
    """判断版本号是否有效（非未知状态）"""
    return version not in VERSION_UNKNOWN


async def ensure_repo_with_version(
    repo_url: str,
    local_path: str,
    version: str,
    branch: str = "main",
) -> tuple[str, bool]:
    """确保仓库可用并 checkout 到正确版本

    三级降级策略：
    1. version 已知 + local_path 存在 → fetch + checkout 精确 commit
    2. version 未知 + local_path 存在 → fetch + checkout branch + pull
    3. local_path 不存在 → clone 到 local_path

    Args:
        repo_url: Git 仓库 URL
        local_path: 本地持久化路径
        version: Git commit SHA（可为 "unknown" 或空）
        branch: 目标分支名

    Returns:
        (repo_path, degraded) — degraded=True 表示降级到最新代码
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

    # 情况 3：本地路径不存在 → clone
    if not os.path.exists(os.path.join(local_path, ".git")):
        logger.info(f"首次 clone: {repo_url} -> {local_path}")
        os.makedirs(local_path, exist_ok=True)
        try:
            Repo.clone_from(repo_url, local_path, branch=branch)
        except GitCommandError as e:
            logger.error(f"clone 失败: {e}")
            raise
        if is_version_known(version):
            try:
                repo = Repo(local_path)
                repo.git.fetch("origin")
                repo.git.checkout(version)
                logger.info(f"clone 后 checkout 到精确版本: {version}")
                return local_path, False
            except GitCommandError:
                logger.warning(f"无法 checkout 到 {version}，使用最新 {branch}")
        return local_path, True

    # 本地路径已存在
    repo = Repo(local_path)

    # 情况 1：version 已知 → fetch + checkout 精确 commit
    if is_version_known(version):
        try:
            repo.git.fetch("origin")
            repo.git.checkout(version)
            logger.info(f"checkout 到精确版本: {version}")
            return local_path, False
        except GitCommandError:
            logger.warning(f"无法 checkout 到 {version}，降级到最新 {branch}")

    # 情况 2：version 未知 → fetch + checkout branch + pull
    try:
        repo.git.fetch("origin")
        repo.git.checkout(branch)
        repo.git.pull("origin", branch)
        logger.info(f"更新到最新 {branch}")
    except GitCommandError as e:
        logger.warning(f"更新分支失败: {e}，使用本地现有代码")

    return local_path, True
