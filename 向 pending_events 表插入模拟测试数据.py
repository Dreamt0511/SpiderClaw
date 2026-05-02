"""向 pending_events 表插入模拟测试数据，用于测试事件恢复机制。

用法: python seed_pending_events.py [--count N] [--db PATH]
"""
import argparse
import json
import sqlite3
import time
import uuid

DB_PATH = "data/repair_records.db"

TEST_EVENTS = [
    {
        "event_type": "github",
        "source": "Dreamt0511/AutoFix_Test_rep",
        "payload": {
            "event_id": "",  # 运行时填充
            "event_type": "workflow_run",
            "action": "",
            "source": "github_webhook",
            "repository": "Dreamt0511/AutoFix_Test_rep",
            "signature_valid": True,
            "payload": {},
            "clone_url": "https://github.com/Dreamt0511/AutoFix_Test_rep.git",
            "branch": "my_test_branch",
            "conclusion": "failure",
            "logs_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/actions/jobs/12345/logs",
            "pr_number": 42,
        },
    },
    {
        "event_type": "github",
        "source": "Dreamt0511/AutoFix_Test_rep",
        "payload": {
            "event_id": "",
            "event_type": "workflow_run",
            "action": "",
            "source": "github_webhook",
            "repository": "Dreamt0511/AutoFix_Test_rep",
            "signature_valid": True,
            "payload": {},
            "clone_url": "https://github.com/Dreamt0511/AutoFix_Test_rep.git",
            "branch": "fix/login-crash",
            "conclusion": "failure",
            "logs_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/actions/jobs/67890/logs",
            "pr_number": 55,
        },
    },
    {
        "event_type": "runtime_log",
        "source": "my-web-service",
        "payload": {
            "event_id": "",
            "source": "remote_log",
            "log": "TypeError: Cannot read property 'id' of undefined\n  at /app/src/user/profile.js:42\n  at async handleRequest (/app/src/router.js:18)",
            "service": "my-web-service",
            "version": "v1.3.2",
            "hostname": "prod-server-01",
            "repo_url": "https://github.com/Dreamt0511/AutoFix_Test_rep.git",
            "repo_local_path": "/data/repos/my-web-service",
            "branch": "main",
            "path_mapping": {"/app/": "src/"},
            "repository": "Dreamt0511/AutoFix_Test_rep",
            "clone_url": "https://github.com/Dreamt0511/AutoFix_Test_rep.git",
        },
    },
    {
        "event_type": "runtime_log",
        "source": "my-web-service",
        "payload": {
            "event_id": "",
            "source": "remote_log",
            "log": "ValueError: Invalid price format: 'NaN'\n  at /app/src/shop/checkout.py:108\n  in process_payment(amount, currency)",
            "service": "my-web-service",
            "version": "v1.3.2",
            "hostname": "prod-server-02",
            "repo_url": "https://github.com/Dreamt0511/AutoFix_Test_rep.git",
            "repo_local_path": "/data/repos/my-web-service",
            "branch": "main",
            "path_mapping": {"/app/": "src/"},
            "repository": "Dreamt0511/AutoFix_Test_rep",
            "clone_url": "https://github.com/Dreamt0511/AutoFix_Test_rep.git",
        },
    },
    {
        "event_type": "runtime_log",
        "source": "order-api",
        "payload": {
            "event_id": "",
            "source": "remote_log",
            "log": "KeyError: 'avatar_url'\n  at /app/src/user/profile.py:23\n  in get_user_profile(user_id)",
            "service": "order-api",
            "version": "v2.0.1",
            "hostname": "prod-server-03",
            "repo_url": "https://github.com/Dreamt0511/AutoFix_Test_rep.git",
            "repo_local_path": "/data/repos/order-api",
            "branch": "main",
            "path_mapping": {},
            "repository": "Dreamt0511/AutoFix_Test_rep",
            "clone_url": "https://github.com/Dreamt0511/AutoFix_Test_rep.git",
        },
    },
    {
        "event_type": "runtime_log",
        "source": "order-api",
        "payload": {
            "event_id": "",
            "source": "remote_log",
            "log": "AttributeError: 'NoneType' object has no attribute 'json'\n  at /app/src/api/handler.py:55\n  in fetch_external_data(url)",
            "service": "order-api",
            "version": "v2.0.1",
            "hostname": "prod-server-03",
            "repo_url": "https://github.com/Dreamt0511/AutoFix_Test_rep.git",
            "repo_local_path": "/data/repos/order-api",
            "branch": "main",
            "path_mapping": {},
            "repository": "Dreamt0511/AutoFix_Test_rep",
            "clone_url": "https://github.com/Dreamt0511/AutoFix_Test_rep.git",
        },
    },
    {
        "event_type": "runtime_log",
        "source": "my-web-service",
        "payload": {
            "event_id": "",
            "source": "remote_log",
            "log": "RuntimeError: Event loop is closed\n  at /app/src/scheduler/cron.py:31\n  in scheduled_task()",
            "service": "my-web-service",
            "version": "v1.3.1",
            "hostname": "prod-server-01",
            "repo_url": "https://github.com/Dreamt0511/AutoFix_Test_rep.git",
            "repo_local_path": "/data/repos/my-web-service",
            "branch": "main",
            "path_mapping": {"/app/": "src/"},
            "repository": "Dreamt0511/AutoFix_Test_rep",
            "clone_url": "https://github.com/Dreamt0511/AutoFix_Test_rep.git",
        },
    },
]


def main():
    parser = argparse.ArgumentParser(description="插入 pending_events 测试数据")
    parser.add_argument("--count", type=int, default=7, help="插入条数 (默认 7，最多 {})".format(len(TEST_EVENTS)))
    parser.add_argument("--db", default=DB_PATH, help=f"数据库路径 (默认 {DB_PATH})")
    parser.add_argument("--clean", action="store_true", help="插入前清空已有数据")
    args = parser.parse_args()

    count = min(args.count, len(TEST_EVENTS))

    conn = sqlite3.connect(args.db)
    cursor = conn.cursor()

    if args.clean:
        cursor.execute("DELETE FROM pending_events")
        print(f"已清空 pending_events 表")

    now = time.time()
    inserted = 0

    for i in range(count):
        evt = TEST_EVENTS[i]
        event_id = f"test-{uuid.uuid4().hex[:8]}"
        # 填充 event_id 到 payload
        payload = evt["payload"].copy()
        payload["event_id"] = event_id

        cursor.execute(
            "INSERT INTO pending_events (event_id, event_type, payload, status, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                evt["event_type"],
                json.dumps(payload, ensure_ascii=False),
                "pending",
                evt["source"],
                now - (count - i) * 60,  # 每条间隔 1 分钟
                now,
            ),
        )
        inserted += 1

    conn.commit()

    cursor.execute("SELECT id, event_id, event_type, status, source FROM pending_events ORDER BY id")
    rows = cursor.fetchall()
    conn.close()

    print(f"\n已插入 {inserted} 条测试数据，当前表中共 {len(rows)} 条：")
    print(f"{'id':<5} {'event_id':<18} {'event_type':<13} {'status':<10} {'source'}")
    print("-" * 70)
    for r in rows:
        print(f"{r[0]:<5} {r[1]:<18} {r[2]:<13} {r[3]:<10} {r[4]}")


if __name__ == "__main__":
    main()
