# 防止 Dashboard 抖动

## 问题现象

仪表盘在以下时机出现严重闪烁/抖动：
- 服务启动时
- 收到 Webhook 事件时

## 根因分析

### 环境限制

用户在 **Windows Git Bash** 中运行，`stdout` 经过 pipe 到达终端。Rich 的 `detect_legacy_windows()` 返回 `True`（无法访问真实 Console handle），导致 `set_alt_screen()` 返回 `False`。

### Rich Live 的回退机制

当 `Live(screen=True)` 无法进入 Alt Screen 时，Rich Live 回退到 `position_cursor()` 模式。该模式下每帧需要：

1. 清除上一帧内容
2. 重新绘制新帧

清除和绘制之间的时间差导致终端出现可见闪烁/抖动。

### 失败的修复尝试

| 方案 | 做法 | 结果 |
|------|------|------|
| 高度锁定 | 固定所有 Panel 行数 | 无效 |
| Layout minimum_size | 防止溢出 | 无效 |
| VT 处理启用 | 通过 Win32 API 启用虚拟终端处理 | 无效（stdout 是 pipe，无法获取 Console handle） |
| 强制 Alt Screen | 绕过检测直接写 `\x1b[?1049h`，但保留 `Live` | 仍抖动（Live 的 `process_renderables` hook 仍会注入控制序列） |

## 最终解决方案：完全移除 Rich Live

核心思路：**不使用 `rich.live.Live`，直接操作终端**。

### 做法

1. **移除 `Live`** — 删除 `from rich.live import Live`，不再使用 Live 的任何功能
2. **手动进入 Alt Screen** — 直接向 stdout 写入 ANSI 序列：`\x1b[?25l\x1b[?1049h`（隐藏光标 + 进入备用缓冲区）
3. **独立渲染 Console** — 创建单独的 `Console` 实例，不经过 Live 的 render hooks
4. **Capture 渲染** — 每帧用 `console.capture()` 将布局渲染到内存字符串，不起用 Live 的 `process_renderables`
5. **一次性写入** — 每帧输出 `\x1b[2J\x1b[H`（清屏 + 归位）+ captured 内容，避免逐行渲染的中间状态
6. **恢复终端** — 退出时写 `\x1b[?25h\x1b[?1049l`（显示光标 + 恢复主缓冲区）

### 关键代码

```python
# 进入 Alt Screen
console.file.write('\x1b[?25l\x1b[?1049h')
console.file.flush()

# 每帧渲染
def _render():
    _render_all(body, module_map, self.state)
    with _render_console.capture() as capture:
        _render_console.print(root, end='')
    return capture.get()

# 输出到终端
sys.stdout.write('\x1b[2J\x1b[H' + frame)
sys.stdout.flush()

# 退出恢复
finally:
    console.file.write('\x1b[?25h\x1b[?1049l')
    console.file.flush()
```

### 对比

| 方案 | 每帧操作 | 是否抖动 |
|------|----------|----------|
| `Live` + `position_cursor()` | 清除 → 绘制 | 是 |
| `Live` + 强制 Alt Screen | `Control.home()` + 绘制（仍有 live hooks） | 是 |
| **手动渲染（无 Live）** | `\x1b[2J\x1b[H` + 已渲染字符串（一次性写出） | **否** |

### 好处

- 完全消除 React-like diff 渲染的不确定性
- 一次写入完成整帧，无中间闪烁状态
- 不依赖任何 Rich 内部机制（render hooks、shape tracking、position_cursor）
- 模块代码完全不需要修改
