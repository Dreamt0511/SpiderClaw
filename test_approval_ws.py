"""测试飞书审批事件 WebSocket 回调"""
import json
import sys
import threading
import time

# 加载配置
sys.path.insert(0, ".")
from src.config.settings import get_settings
settings = get_settings()

APP_ID = settings.lark.app_id
APP_SECRET = settings.lark.app_secret
NOTIFY_USER = settings.lark.notify_users[0] if settings.lark.notify_users else ""

# 从配置文件读取 approval_code
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


# ===== 1. 定义回调函数 =====
def handle_approval_event(data):
    print(f"\n{'='*60}")
    print(f"[CALLBACK] 收到事件!")
    print(f"[CALLBACK] data 类型: {type(data).__name__}")
    event = data.event or {}
    print(f"[CALLBACK] event: {event}")
    instance_code = event.get("instance_code", "")
    status = event.get("status", "")
    print(f"[CALLBACK] instance_code={instance_code}, status={status}")
    print(f"{'='*60}\n")


# ===== 2. 启动 WebSocket 客户端 =====
def start_ws():
    from lark_oapi import ws as lark_ws, LogLevel as LarkLogLevel
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

    print("[WS] 构建 event_handler...")
    event_handler = EventDispatcherHandler.builder("", "") \
        .register_p1_customized_event("approval_instance", handle_approval_event) \
        .build()

    print("[WS] 创建 client...")
    client = lark_ws.Client(
        app_id=APP_ID,
        app_secret=APP_SECRET,
        event_handler=event_handler,
        log_level=LarkLogLevel.DEBUG,
        auto_reconnect=True,
    )
    print("[WS] 启动 client.start()...")
    client.start()


print("[MAIN] 启动 WebSocket 线程...")
ws_thread = threading.Thread(target=start_ws, daemon=True, name="test-ws")
ws_thread.start()

# 等待连接建立
print("[MAIN] 等待 WebSocket 连接...")
time.sleep(5)

# ===== 3. 创建审批实例 =====
if APPROVAL_CODE and NOTIFY_USER:
    import subprocess
    import uuid

    lark_cmd = "lark-cli.cmd" if sys.platform == "win32" else "lark-cli"

    form_data = json.dumps([{
        "id": "event_summary",
        "type": "textarea",
        "value": "测试审批 - 请点击同意或拒绝",
    }], ensure_ascii=False)

    request_body = json.dumps({
        "approval_code": APPROVAL_CODE,
        "form": form_data,
        "open_id": NOTIFY_USER,
        "uuid": str(uuid.uuid4()),
    }, ensure_ascii=False)

    print(f"\n[MAIN] 创建审批实例...")
    cmd = [
        lark_cmd, "api", "POST",
        "/open-apis/approval/v4/instances",
        "--as", "bot",
        "--data", request_body,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    print(f"[MAIN] 创建结果: {result.stdout[:500]}")
    if result.returncode != 0:
        print(f"[MAIN] 错误: {result.stderr[:500]}")
else:
    print(f"[MAIN] 未配置 approval_code 或 notify_users，跳过创建审批")

# ===== 4. 保持运行，等待事件 =====
print("\n[MAIN] 等待审批事件... (Ctrl+C 退出)")
print("[MAIN] 请在飞书中同意或拒绝审批\n")

try:
    while True:
        time.sleep(1)
        if not ws_thread.is_alive():
            print("[MAIN] WebSocket 线程已退出!")
            break
except KeyboardInterrupt:
    print("\n[MAIN] 退出")
