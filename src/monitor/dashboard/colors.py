"""SpiderClaw 色彩体系 — 暗夜蓝 + 冷灰 + 暗金点缀

从 cli/app.py 的配色方案提取：
  logo_color / primary  = #20d5f0  主色调（青蓝）
  ice / text            = #e8eef5  冰白
  warm_gold / accent    = #1ed3c1  暗金
  muted_gray            = #7a8ba0  中等灰
  dim_gray / dim        = #5a6b7c  暗灰
  error                 = #ff4444  错误红
"""

PRIMARY    = "#20d5f0"     # 主色调（青蓝）— 面板边框、标题、强调
ICE        = "#e8eef5"     # 冰白 — 正文文字
ACCENT     = "#1ed3c1"     # 暗金 — 装饰、分隔线
MUTED      = "#7a8ba0"     # 中等灰 — 次要信息
DIM        = "#5a6b7c"     # 暗灰 — 时间戳、提示

SUCCESS    = "#00ff88"     # 成功 / 工具结果
ERROR      = "#ff4444"     # 错误 / 失败
WARNING    = "#ffaa00"     # 警告 / 工具调用
INFO       = ACCENT        # 信息（改用青绿色，避免和边框青蓝色撞色）
DARK_BG    = "#0a0a1a"     # 深色背景

# 事件类型 → 颜色映射
EVENT_COLORS = {
    "node_enter":    ACCENT,    # 进入节点改用青绿色，和边框区分
    "node_exit":     ICE,
    "tool_call":     WARNING,
    "tool_result":   SUCCESS,
    "llm_call":      INFO,
    "llm_response":  ICE,
    "error":         ERROR,
    "system_action": PRIMARY,   # 系统动作改用主色调青蓝色
    "app_log":       MUTED,
    "milestone":     ACCENT,
}

# 事件类型 → 中文标签
EVENT_LABELS = {
    "node_enter":    "▶ 进入节点",
    "node_exit":     "◀ 离开节点",
    "tool_call":     "🔧 工具调用",
    "tool_result":   "📦 工具结果",
    "llm_call":      "🧠 LLM请求",
    "llm_response":  "💬 LLM回复",
    "error":         "❌ 错误",
    "system_action": "⚙ 系统",
    "app_log":       "📋 日志",
    "milestone":     "🏁 里程碑",
}

# Agent 中文状态
AGENT_STATUS_CN = {
    "idle":         "空闲",
    "thinking":     "思考中",
    "calling_tool": "调用工具中",
    "error":        "错误",
}

STATUS_COLORS = {
    "idle":         DIM,
    "thinking":     PRIMARY,
    "calling_tool": WARNING,
    "error":        ERROR,
}

# 节点名 → 友好中文名
NODE_ALIAS = {
    "collect_context":  "收集上下文中",
    "collect_runtime_context": "收集上下文中",
    "fix_agent":        "修复Agent",
    "validation_gate":  "验证门禁",
    "review_changes":   "审查Agent",
    "run_tests":        "测试Agent",
    "create_pr":        "提交PR",
    "handle_failure":   "处理失败",
    "agent":            "主Agent",
    "tools":            "工具节点",
    # 生命周期里程碑
    "service_start":    "🚀 服务启动",
    "webhook_event":    "📩 收到Webhook",
    "repair_start":     "🔧 修复流程启动",
    "repair_complete":  "✅ 修复流程完成",
    "lark_notify":      "📢 发送飞书通知",
}
