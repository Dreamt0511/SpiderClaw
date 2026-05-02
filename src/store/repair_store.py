"""修复记录存储层 — SQLite 实现 + traceback 指纹算法"""

import hashlib
import logging
import os
import re
import sqlite3
import time
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# 框架代码路径前缀，生成指纹时排除
_FRAMEWORK_PATHS = (
    "site-packages/",
    "lib/python",
    "Python3",
    "python3.",
    "/usr/lib/",
    "/usr/local/lib/",
)


class RepairLifecycleStatus(str, Enum):
    """修复生命周期状态"""
    PENDING_DEPLOY = "pending_deploy"    # PR 已创建，等待部署
    DEPLOYED = "deployed"                # 修复已部署上线
    SUPERSEDED = "superseded"            # 版本已变更，记录过期
    FAILED = "failed"                    # 修复尝试失败
    ABANDONED = "abandoned"              # 失败次数过多，放弃重试


def compute_traceback_fingerprint(traceback_text: str) -> str:
    """从 traceback 内容生成稳定的 12 位 hex 指纹

    策略：
    1. 排除 site-packages/、lib/python 等框架代码行
    2. 提取第一个应用文件名（不含路径、不含行号）
    3. 提取异常类型（如 ValueError）
    4. 提取错误消息前 50 字符
    5. 拼接后 MD5 取前 12 位
    """
    if not traceback_text:
        return ""

    lines = traceback_text.splitlines()

    # 过滤掉框架代码行
    app_lines = [
        ln for ln in lines
        if not any(fw in ln for fw in _FRAMEWORK_PATHS)
    ]

    # 提取第一个应用文件名
    app_file = ""
    for ln in app_lines:
        m = re.search(r'File "([^"]+)"', ln)
        if m:
            full_path = m.group(1)
            app_file = full_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            break

    # 提取异常类型
    error_type = ""
    for ln in lines:
        m = re.search(r'^([A-Z][a-zA-Z0-9]*(?:Error|Exception))', ln.strip())
        if m:
            error_type = m.group(1)
            break

    # 提取错误消息（异常类型后面的部分）
    error_msg = ""
    if error_type:
        for ln in lines:
            stripped = ln.strip()
            if stripped.startswith(error_type + ":"):
                error_msg = stripped[len(error_type) + 1:].strip()[:50]
                break
            elif stripped.startswith(error_type) and ":" in stripped:
                error_msg = stripped.split(":", 1)[1].strip()[:50]
                break

    if not error_type and not app_file:
        # 无法提取特征，用原始内容的前 200 字符
        key = traceback_text[:200]
    else:
        key = f"{error_type}:{app_file}:{error_msg}"

    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


class RepairStore:
    """SQLite 修复记录存储"""

    def __init__(self, db_path: str = "data/repair_records.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS repair_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL,
                    service TEXT DEFAULT '',
                    error_type TEXT DEFAULT '',
                    fix_pr_url TEXT DEFAULT '',
                    fix_pr_number TEXT DEFAULT '',
                    fix_description TEXT DEFAULT '',
                    service_version TEXT DEFAULT '',
                    repo_name TEXT DEFAULT '',
                    branch_name TEXT DEFAULT '',
                    fail_count INTEGER DEFAULT 0,
                    last_fail_time REAL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_fingerprint
                ON repair_records(fingerprint)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status
                ON repair_records(status)
            """)
            conn.commit()

    def query_by_fingerprint(self, fingerprint: str) -> Optional[dict]:
        """按指纹查询修复记录"""
        if not fingerprint:
            return None
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM repair_records WHERE fingerprint = ?",
                    (fingerprint,),
                ).fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"SQLite 查询失败: {e}")
            return None

    def upsert(
        self,
        fingerprint: str,
        status: str,
        *,
        service: str = "",
        error_type: str = "",
        fix_pr_url: str = "",
        fix_pr_number: str = "",
        fix_description: str = "",
        service_version: str = "",
        repo_name: str = "",
        branch_name: str = "",
        increment_fail: bool = False,
    ) -> int:
        """写入或更新修复记录，返回 record id"""
        now = time.time()
        try:
            with sqlite3.connect(self.db_path) as conn:
                existing = conn.execute(
                    "SELECT id, fail_count FROM repair_records WHERE fingerprint = ?",
                    (fingerprint,),
                ).fetchone()

                if existing:
                    record_id, fail_count = existing
                    new_fail_count = fail_count + 1 if increment_fail else fail_count
                    last_fail_time = now if increment_fail else None

                    sets = [
                        "status = ?",
                        "updated_at = ?",
                        "fail_count = ?",
                    ]
                    params = [status, now, new_fail_count]

                    for field, val in [
                        ("service", service),
                        ("error_type", error_type),
                        ("fix_pr_url", fix_pr_url),
                        ("fix_pr_number", fix_pr_number),
                        ("fix_description", fix_description),
                        ("service_version", service_version),
                        ("repo_name", repo_name),
                        ("branch_name", branch_name),
                    ]:
                        if val:
                            sets.append(f"{field} = ?")
                            params.append(val)

                    if last_fail_time is not None:
                        sets.append("last_fail_time = ?")
                        params.append(last_fail_time)

                    params.append(record_id)
                    conn.execute(
                        f"UPDATE repair_records SET {', '.join(sets)} WHERE id = ?",
                        params,
                    )
                    conn.commit()
                    return record_id
                else:
                    fail_count = 1 if increment_fail else 0
                    last_fail_time = now if increment_fail else 0
                    cursor = conn.execute(
                        """INSERT INTO repair_records
                           (fingerprint, status, service, error_type,
                            fix_pr_url, fix_pr_number, fix_description,
                            service_version, repo_name, branch_name,
                            fail_count, last_fail_time, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            fingerprint, status, service, error_type,
                            fix_pr_url, fix_pr_number, fix_description,
                            service_version, repo_name, branch_name,
                            fail_count, last_fail_time, now, now,
                        ),
                    )
                    conn.commit()
                    return cursor.lastrowid or 0
        except Exception as e:
            logger.error(f"SQLite upsert 失败: {e}")
            return 0

    def should_retry(self, fingerprint: str) -> bool:
        """检查是否应该重试（指数退避：1min → 2min → 4min，>=3 次放弃）"""
        record = self.query_by_fingerprint(fingerprint)
        if not record:
            return True

        status = record.get("status", "")
        if status == RepairLifecycleStatus.ABANDONED.value:
            return False

        fail_count = record.get("fail_count", 0)
        if fail_count >= 3:
            return False

        last_fail_time = record.get("last_fail_time", 0)
        if not last_fail_time:
            return True

        # 指数退避: 60 * 2^(fail_count-1) 秒
        wait_seconds = 60 * (2 ** max(fail_count - 1, 0))
        return (time.time() - last_fail_time) > wait_seconds

    def mark_superseded_by_version(self, service: str, old_version: str) -> int:
        """版本变更时，将旧版本的 pending_deploy/deployed 记录标记为 superseded"""
        if not old_version:
            return 0
        try:
            now = time.time()
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    """UPDATE repair_records
                       SET status = ?, updated_at = ?
                       WHERE service = ? AND service_version = ?
                       AND status IN (?, ?)""",
                    (
                        RepairLifecycleStatus.SUPERSEDED.value,
                        now,
                        service,
                        old_version,
                        RepairLifecycleStatus.PENDING_DEPLOY.value,
                        RepairLifecycleStatus.DEPLOYED.value,
                    ),
                )
                conn.commit()
                count = cursor.rowcount
                if count:
                    logger.info(
                        f"版本变更 {service}: {old_version} → 标记 {count} 条记录为 superseded"
                    )
                return count
        except Exception as e:
            logger.error(f"标记 superseded 失败: {e}")
            return 0

    def delete_by_service(self, service: str) -> int:
        """删除指定服务的所有修复记录（仓库被清除时调用）"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM repair_records WHERE service = ?",
                    (service,),
                )
                conn.commit()
                count = cursor.rowcount
                if count:
                    logger.info(f"已清除服务 {service} 的 {count} 条修复记录")
                return count
        except Exception as e:
            logger.error(f"删除修复记录失败: {e}")
            return 0

    def mark_deployed(self, fingerprint: str) -> bool:
        """将记录标记为已部署"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE repair_records SET status = ?, updated_at = ? WHERE fingerprint = ?",
                    (RepairLifecycleStatus.DEPLOYED.value, time.time(), fingerprint),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"标记 deployed 失败: {e}")
            return False


# 全局单例
_repair_store: Optional[RepairStore] = None


def get_repair_store(db_path: str = "data/repair_records.db") -> RepairStore:
    """获取全局修复记录存储实例"""
    global _repair_store
    if _repair_store is None:
        _repair_store = RepairStore(db_path)
    return _repair_store
