"""测试线程方式：纯线程 + queue.Queue（和 test_approval_ws.py 一致）"""
import json
import queue
import sys
import threading
import time


def start_lark_ws(app_id, app_secret, event_queue):
    """守护线程：lark SDK WebSocket 客户端"""
    from lark_oapi import ws as lark_ws, LogLevel as LarkLogLevel
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

    def on_approval_event(data):
        try:
            event = data.event or {}
            instance_code = event.get("instance_code", "")
            status = event.get("status", "")
            print(f"[ws-thread] 收到审批事件: instance_code={instance_code}, status={status}", flush=True)
            if instance_code:
                event_queue.put((instance_code, status))
        except Exception as e:
            print(f"[ws-thread] 回调异常: {e}", flush=True)

    event_handler = EventDispatcherHandler.builder("", "") \
        .register_p1_customized_event("approval_instance", on_approval_event) \
        .build()

    client = lark_ws.Client(
        app_id=app_id, app_secret=app_secret,
        event_handler=event_handler,
        log_level=LarkLogLevel.DEBUG,
        auto_reconnect=True,
    )
    print("[ws-thread] 启动 WebSocket 客户端...", flush=True)
    client.start()


if __name__ == "__main__":
    sys.path.insert(0, ".")
    from src.config.settings import get_settings
    settings = get_settings()

    APP_ID = settings.lark.app_id
    APP_SECRET = settings.lark.app_secret
    NOTIFY_USER = settings.lark.notify_users[0] if settings.lark.notify_users else ""

    APPROVAL_CODE = settings.lark.approval_code or ""
    if not APPROVAL_CODE:
        try:
            with open("data/approval_config.json", "r", encoding="utf-8") as f:
                APPROVAL_CODE = json.load(f).get("approval_code", "")
        except Exception:
            pass

    print(f"APP_ID: {APP_ID}")
    print(f"APPROVAL_CODE: {APPROVAL_CODE}")
    print(f"NOTIFY_USER: {NOTIFY_USER}")
    print()

    event_queue = queue.Queue()

    # 启动 WebSocket 线程
    ws_thread = threading.Thread(
        target=start_lark_ws,
        args=(APP_ID, APP_SECRET, event_queue),
        daemon=True,
        name="lark-ws",
    )
    ws_thread.start()
    print(f"[main] WebSocket 线程已启动")

    # 等待连接建立
    print("[main] 等待 WebSocket 连接...")
    time.sleep(8)

    # 创建审批实例
    if APPROVAL_CODE and NOTIFY_USER:
        import subprocess
        import uuid

        lark_cmd = "lark-cli.cmd" if sys.platform == "win32" else "lark-cli"

        form_data = json.dumps([{
            "id": "event_summary",
            "type": "textarea",
            "value": "线程测试 - 请点击同意或拒绝",
        }], ensure_ascii=False)

        request_body = json.dumps({
            "approval_code": APPROVAL_CODE,
            "form": form_data,
            "open_id": NOTIFY_USER,
            "uuid": str(uuid.uuid4()),
        }, ensure_ascii=False)

        print(f"\n[main] 创建审批实例...")
        cmd = [
            lark_cmd, "api", "POST",
            "/open-apis/approval/v4/instances",
            "--as", "bot",
            "--data", request_body,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        print(f"[main] 创建结果: {result.stdout[:500]}")
        if result.returncode != 0:
            print(f"[main] 错误: {result.stderr[:500]}")
    else:
        print(f"[main] 未配置 approval_code 或 notify_users，跳过创建审批")

    # 等待事件（纯阻塞，不用 asyncio）
    print("\n[main] 等待审批事件... (Ctrl+C 退出)")
    print("[main] 请在飞书中同意或拒绝审批\n")

    try:
        while True:
            try:
                instance_code, status = event_queue.get(timeout=5)
                print(f"[main] 收到事件: instance_code={instance_code}, status={status}")
            except queue.Empty:
                pass
            if not ws_thread.is_alive():
                print("[main] WebSocket 线程已退出!")
                break
    except KeyboardInterrupt:
        print("\n[main] 退出")
