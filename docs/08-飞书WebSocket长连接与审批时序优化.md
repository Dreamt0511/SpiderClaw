# 飞书 WebSocket 长连接与审批时序优化

## 问题背景

系统在启动时恢复待处理事件，当事件数量超过阈值时会创建飞书审批。审批创建后，飞书 SDK 通过 WebSocket 长连接推送审批状态变更（PENDING/APPROVED/REJECTED）。

原来的时序存在一个问题：**审批在长连接建立之前就发出**，导致审批回调可能丢失。

## 原来的执行顺序

```
_run()
  ├── asyncio.create_task(approval_event_listener())   # 创建任务，未执行
  └── await recover_pending_events()                    # 立即执行
        └── 创建审批 ← 此时 WebSocket 还没连接
              └── approval_event_listener 才开始启动 WebSocket
```

`recover_pending_events()` 是 `await` 调用，会立即执行；而 `approval_event_listener()` 虽然先创建了 task，但要等 `recover_pending_events()` 完成后才会被调度。

## 优化后的执行顺序

```
_run()
  ├── asyncio.create_task(approval_event_listener())
  └── await recover_pending_events()
        └── await approval_ws_ready.wait()  ← 暂停，等待长连接就绪
              ↓ (同时)
              approval_event_listener()
                ├── 启动 WebSocket 线程
                ├── await ws_connected.wait()  ← 等待连接建立
                ├── subscribe_approval_events() ← 连接后才订阅
                └── approval_ws_ready.set()     ← 通知就绪
              ↓
        收到信号，创建审批
```

## 关键技术点

### 1. 如何检测 WebSocket 连接已建立

飞书 SDK 的 `ws_client.start()` 是阻塞调用，内部先执行 `_connect()` 建立连接，然后进入事件循环。线程启动不等于连接建立。

解决方案：**monkey-patch `_connect` 方法**，在连接建立后发信号。

```python
# monkey-patch _connect：连接建立后通知主线程
_original_connect = client._connect

async def _patched_connect():
    await _original_connect()
    connected_event.set()  # 连接建立后发信号

client._connect = _patched_connect
```

`_connect` 只在连接成功时完成（失败会抛异常），所以 `connected_event.set()` 只在连接真正建立后才触发。

### 2. 两个 Event 的分工

| Event | 作用 |
|-------|------|
| `ws_connected` (threading.Event) | 跨线程信号：WebSocket 线程通知主线程连接已建立 |
| `approval_ws_ready` (asyncio.Event) | 协程间信号：`approval_event_listener` 通知 `recover_pending_events` 可以创建审批 |

### 3. 重连时也要等连接

WebSocket 线程超时退出后会重启，重启时同样需要等连接建立：

```python
except asyncio.TimeoutError:
    if not ws_thread.is_alive():
        ws_connected = threading.Event()
        ws_thread = threading.Thread(
            target=_start_lark_ws_thread,
            args=(..., ws_connected),
        )
        ws_thread.start()
        await loop.run_in_executor(None, ws_connected.wait)  # 等重连
```

## 最终日志顺序

```
[approval] 飞书 WebSocket 线程已启动，等待连接建立...
[approval-ws] 启动 WebSocket 客户端
connected to wss://msg-frontier.feishu.cn/ws/v2?...   ← SDK连接建立
[approval] 飞书 WebSocket 已连接
[approval] 订阅审批事件: B1B219DA-...
审批事件订阅成功
[approval] 审批监听就绪
待处理事件数量 (7) 超过阈值 (5)，需人工确认
长连接已就绪，开始创建审批
创建审批实例: 7 个待处理事件
```

## 涉及文件

- `src/monitor/webhook_server.py` — `_start_lark_ws_thread`、`approval_event_listener`、`recover_pending_events`
