"""修复记录存储层测试"""

import os
import time
import pytest

from src.store.repair_store import RepairStore, RepairLifecycleStatus


@pytest.fixture
def store(tmp_path):
    """创建临时 SQLite 存储"""
    db_path = str(tmp_path / "test_repair.db")
    return RepairStore(db_path)


class TestRepairStore:
    """RepairStore CRUD 测试"""

    def test_upsert_new_record(self, store):
        """新建记录"""
        record_id = store.upsert(
            "abc123",
            RepairLifecycleStatus.PENDING_DEPLOY.value,
            service="order-service",
            error_type="ValueError",
            fix_pr_url="https://github.com/test/pull/1",
        )
        assert record_id > 0

        record = store.query_by_fingerprint("abc123")
        assert record is not None
        assert record["status"] == "pending_deploy"
        assert record["service"] == "order-service"
        assert record["error_type"] == "ValueError"

    def test_upsert_update_existing(self, store):
        """更新已有记录"""
        rid1 = store.upsert("fp1", RepairLifecycleStatus.FAILED.value, service="svc")
        rid2 = store.upsert(
            "fp1",
            RepairLifecycleStatus.PENDING_DEPLOY.value,
            fix_pr_url="https://github.com/test/pull/2",
        )
        assert rid1 == rid2  # 同一条记录

        record = store.query_by_fingerprint("fp1")
        assert record["status"] == "pending_deploy"
        assert record["fix_pr_url"] == "https://github.com/test/pull/2"

    def test_query_nonexistent(self, store):
        """查询不存在的记录返回 None"""
        assert store.query_by_fingerprint("nonexistent") is None

    def test_query_empty_fingerprint(self, store):
        """空指纹返回 None"""
        assert store.query_by_fingerprint("") is None

    def test_increment_fail_count(self, store):
        """失败计数递增"""
        store.upsert("fp1", RepairLifecycleStatus.FAILED.value, increment_fail=True)
        record = store.query_by_fingerprint("fp1")
        assert record["fail_count"] == 1

        store.upsert("fp1", RepairLifecycleStatus.FAILED.value, increment_fail=True)
        record = store.query_by_fingerprint("fp1")
        assert record["fail_count"] == 2


class TestShouldRetry:
    """退避重试逻辑测试"""

    def test_new_fingerprint_should_retry(self, store):
        """新指纹允许重试"""
        assert store.should_retry("new_fp") is True

    def test_first_fail_should_retry(self, store):
        """首次失败超过退避时间后允许重试"""
        # 手动设置 last_fail_time 为 2 分钟前，超过 fail_count=1 的 60 秒退避
        store.upsert("fp1", RepairLifecycleStatus.FAILED.value, increment_fail=True)
        import sqlite3
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE repair_records SET last_fail_time = ? WHERE fingerprint = ?",
                (time.time() - 120, "fp1"),
            )
            conn.commit()
        assert store.should_retry("fp1") is True

    def test_three_fails_should_not_retry(self, store):
        """3次失败后不再重试"""
        for _ in range(3):
            store.upsert("fp1", RepairLifecycleStatus.FAILED.value, increment_fail=True)
        assert store.should_retry("fp1") is False

    def test_abandoned_should_not_retry(self, store):
        """abandoned 状态不再重试"""
        store.upsert("fp1", RepairLifecycleStatus.ABANDONED.value)
        assert store.should_retry("fp1") is False


class TestMarkSuperseded:
    """版本过期标记测试"""

    def test_mark_superseded(self, store):
        """将旧版本 pending_deploy 标记为 superseded"""
        store.upsert(
            "fp1",
            RepairLifecycleStatus.PENDING_DEPLOY.value,
            service="order-service",
            service_version="v1.0",
        )
        store.upsert(
            "fp2",
            RepairLifecycleStatus.DEPLOYED.value,
            service="order-service",
            service_version="v1.0",
        )
        store.upsert(
            "fp3",
            RepairLifecycleStatus.PENDING_DEPLOY.value,
            service="order-service",
            service_version="v2.0",
        )

        count = store.mark_superseded_by_version("order-service", "v1.0")
        assert count == 2  # fp1 和 fp2

        assert store.query_by_fingerprint("fp1")["status"] == "superseded"
        assert store.query_by_fingerprint("fp2")["status"] == "superseded"
        assert store.query_by_fingerprint("fp3")["status"] == "pending_deploy"  # v2.0 不受影响

    def test_mark_superseded_empty_version(self, store):
        """空版本不标记"""
        assert store.mark_superseded_by_version("svc", "") == 0

    def test_mark_deployed(self, store):
        """标记为已部署"""
        store.upsert("fp1", RepairLifecycleStatus.PENDING_DEPLOY.value)
        assert store.mark_deployed("fp1") is True
        assert store.query_by_fingerprint("fp1")["status"] == "deployed"
