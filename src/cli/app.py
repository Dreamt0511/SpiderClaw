"""CLI主入口"""

import threading
from typing import Optional
from pathlib import Path
import typer
from rich.console import Console
from rich.text import Text

app = typer.Typer(
    name="spiderclaw",
    help="SpiderClaw-事件驱动的自动诊断与修复系统",
    add_completion=False,
    rich_markup_mode="rich",
    invoke_without_command=True,
)
console = Console()
CONFIG_PATH = Path("src/config/agent-config.yaml")


def make_banner() -> Text:
    """生成启动 Logo 与标题（返回 Text 供 Live 内渲染）。"""
    logo_placeholder = r"""
                    ███      █████                             ████
                   ░░░      ░░███                             ░░███
  █████  ████████  ████   ███████   ██████  ████████   ██████  ░███   ██████   █████ ███ █████
 ███░░  ░░███░░███░░███  ███░░███  ███░░███░░███░░███ ███░░███ ░███  ░░░░░███ ░░███ ░███░░███
░░█████  ░███ ░███ ░███ ░███ ░███ ░███████  ░███ ░░░ ░███ ░░░  ░███   ███████  ░███ ░███ ░███
 ░░░░███ ░███ ░███ ░███ ░███ ░███ ░███░░░   ░███     ░███  ███ ░███  ███░░███  ░░███████████
 ██████  ░███████  █████░░████████░░██████  █████    ░░██████  █████░░████████  ░░████░████
░░░░░░   ░███░░░  ░░░░░  ░░░░░░░░  ░░░░░░  ░░░░░      ░░░░░░  ░░░░░  ░░░░░░░░    ░░░░ ░░░░
         ░███
         █████
        ░░░░░
    """

    logo_color = "#20d5f0"
    ice = "#e8eef5"
    warm_gold = "#1ed3c1"
    dim_gray = "#5a6b7c"

    banner = Text()
    banner.append(logo_placeholder, style=logo_color)
    banner.append("\n Welcome to ", style=ice)
    banner.append("SpiderClaw", style=f"bold {logo_color}")
    banner.append(" !  ", style=ice)
    banner.append("\n" + "─" * 36, style=warm_gold)
    banner.append("\n  SpiderClaw 已完成启动。按 Ctrl+C 退出。\n", style=dim_gray)
    return banner

def _setup_feishu():
    """飞书通知配置向导"""
    import questionary
    import yaml
    from rich.panel import Panel
    from rich.status import Status

    spider_style = questionary.Style(
        [
            ("qmark", "fg:#20d5f0 bold"),
            ("question", "fg:#20d5f0 bold"),
            ("answer", "fg:#20d5f0 bold"),
            ("pointer", "fg:#20d5f0 bold"),
            ("highlighted", "fg:#20d5f0 bold"),
            ("selected", "fg:#20d5f0"),
            ("instruction", "fg:#808080 dim"),
        ]
    )

    console.clear()
    console.print(
        Panel(
            "欢迎使用 [bold #20d5f0]SpiderClaw[/bold #20d5f0] 飞书配置向导\n\n[dim]扫码授权后将自动完成应用创建和配置写入。[/dim]",
            title="[bold white]飞书通知配置[/bold white]",
            border_style="#20d5f0",
        )
    )

    confirm = questionary.confirm(
        "是否开始配置飞书通知？", default=True, style=spider_style
    ).ask()

    if not confirm:
        console.print("[dim]>> 配置已取消[/dim]")
        return

    from src.notify.lark_register import register_lark_app_sync

    try:
        result = register_lark_app_sync()
    except KeyboardInterrupt:
        console.print("\n[dim]>> 用户中断配置[/dim]")
        return
    except Exception as e:
        console.print(f"[bold #ff4444][配置失败!][/bold #ff4444]  错误信息: {str(e)}")
        return

    if not result:
        console.print(
            "[bold #ff4444][配置失败!][/bold #ff4444]  授权过程出错，请重试！"
        )
        return

    with Status(
        "[bold #20d5f0]正在写入配置文件...[/bold #20d5f0]",
        spinner="dots",
        spinner_style="#20d5f0",
    ):
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
            else:
                config = {}

            if "lark" not in config:
                config["lark"] = {}

            config["lark"]["enabled"] = True
            config["lark"]["app_id"] = result["app_id"]
            config["lark"]["app_secret"] = result["app_secret"]
            config["lark"]["notify_users"] = []
            config["lark"]["notify_groups"] = []

            CONFIG_PATH.parent.mkdir(exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(
                    config,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )

        except Exception as e:
            console.print(
                f"[bold #ff4444][写入配置失败!][/bold #ff4444]  错误信息: {str(e)}"
            )
            return

    console.print(
        Panel(
            f"飞书通知配置成功！\n\n"
            f"应用信息：\n"
            f"App ID: [#20d5f0]{result['app_id']}[/#20d5f0]\n"
            f"App Secret: [#20d5f0]{result['app_secret']}[/#20d5f0]\n\n"
            f"后续配置：\n"
            f"通过在终端中输入 lark-cli auth status 获取当前登录状态，即可获取userOpenId。\n"
            f"请在 [#20d5f0]src/config/agent-config.yaml[/#20d5f0] 中添加需要通知的用户/群组ID。\n\n"
            f"配置完成后，在SpiderClaw总监控服务启动后，系统将自动发送修复结果通知！",
            border_style="#20d5f0",
        )
    )


@app.callback()
def main(
    ctx: typer.Context,
    debug: bool = typer.Option(False, "--debug", help="开启调试模式"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="配置文件路径"),
    port: int = typer.Option(8000, "--port", "-p", help="Webhook监听端口"),
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Webhook监听地址"),
    reload: bool = typer.Option(False, "--reload", help="热重载"),
):
    """
    SpiderClaw 事件驱动的自动诊断与修复系统

    不带参数直接运行将启动Webhook总监控服务
    """
    global CONFIG_PATH
    if config:
        CONFIG_PATH = Path(config)

    # 打印启动 Logo 并启动全部服务（仅无子命令时）
    if ctx.invoked_subcommand is None:
        # 后台启动 Webhook 服务
        from src.monitor.webhook_server import run_webhook_server

        webhook_thread = threading.Thread(
            target=run_webhook_server,
            kwargs={"host": host, "port": port, "reload": reload, "console_output": False},
            daemon=True,
        )
        webhook_thread.start()

        # 前台启动监控面板（banner + dashboard 统一在 Live 内渲染）
        from src.monitor.dashboard import Dashboard
        from src.monitor.dashboard.modules.log_module import LogModule
        from src.monitor.dashboard.modules.node_module import NodeModule
        from src.monitor.dashboard.modules.tool_module import ToolModule
        from src.monitor.dashboard.modules.stats_module import StatsModule
        from src.monitor.dashboard.modules.status_module import StatusModule

        dash = Dashboard("src/logs/audit.jsonl", banner=make_banner())
        dash.register(LogModule())
        dash.register(NodeModule())
        dash.register(ToolModule())
        dash.register(StatsModule())
        dash.register(StatusModule())
        dash.run()


@app.command("setup")
def setup_wizard():
    """系统配置向导（飞书通知等）"""
    _setup_feishu()


@app.command("init-sidecar")
def init_sidecar(
    output_dir: str = typer.Option("./sidecar", "-o", "--output", help="输出目录"),
    service_name: str = typer.Option("my-service", "-n", "--name", help="服务名称"),
    agent_url: str = typer.Option("http://agent-host:8000/webhook/log", "--agent-url", help="Agent Webhook 地址"),
    log_path: str = typer.Option("/var/log/app/app.log", "--log-path", help="日志文件路径"),
):
    """生成采集脚本和配置模板"""
    import os
    from rich.panel import Panel

    os.makedirs(output_dir, exist_ok=True)

    # 生成 agent-mapping.conf
    conf_content = f'''# SpiderClaw 采集脚本配置
# 部署到业务服务器: /opt/agent-sidecar/agent-mapping.conf

SERVICE_NAME="{service_name}"
SERVICE_VERSION=""  # 部署时写入: echo "SERVICE_VERSION=$(git rev-parse HEAD)" >> agent-mapping.conf
LOG_PATH="{log_path}"
AGENT_URL="{agent_url}"
'''
    conf_path = os.path.join(output_dir, "agent-mapping.conf")
    with open(conf_path, "w", encoding="utf-8") as f:
        f.write(conf_content)

    # 生成 collector.sh
    collector_content = r'''#!/bin/bash
# SpiderClaw 远程日志采集脚本
# 部署到业务服务器: /opt/agent-sidecar/collector.sh
# 启动: nohup bash /opt/agent-sidecar/collector.sh > /dev/null 2>&1 &

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/agent-mapping.conf"

AGENT_URL="${AGENT_URL:-http://agent-host:8000/webhook/log}"
DEDUP_WINDOW="${DEDUP_WINDOW:-300}"
BATCH_INTERVAL="${BATCH_INTERVAL:-10}"
MAX_BATCH_LINES="${MAX_BATCH_LINES:-50}"
MAX_BACKOFF="${MAX_BACKOFF:-300}"

LAST_HASH=""
BACKOFF=1
ERROR_CACHE=""
LAST_SEND_TIME=0
LINE_COUNT=0

# 错误哈希函数：提取 File+行号+错误类型 → MD5 前12位
compute_hash() {
    local text="$1"
    local file_line
    file_line=$(echo "$text" | grep -oP 'File "[^"]+", line \d+' | tail -1 || true)
    local error_type
    error_type=$(echo "$text" | grep -oP '[A-Z][a-zA-Z0-9]*Error' | head -1 || true)
    local key="${file_line}:${error_type}"
    echo -n "$key" | md5sum | cut -c1-12
}

send_batch() {
    local payload="$1"
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \\
        -X POST "$AGENT_URL" \\
        -H "Content-Type: application/json" \\
        -d "$payload" \\
        --connect-timeout 10 \\
        --max-time 30 || echo "000")

    if [ "$http_code" = "429" ]; then
        BACKOFF=$((BACKOFF * 2))
        [ "$BACKOFF" -gt "$MAX_BACKOFF" ] && BACKOFF=$MAX_BACKOFF
        sleep "$BACKOFF"
    elif [ "$http_code" = "200" ] || [ "$http_code" = "201" ]; then
        BACKOFF=1
    else
        BACKOFF=$((BACKOFF * 2))
        [ "$BACKOFF" -gt "$MAX_BACKOFF" ] && BACKOFF=$MAX_BACKOFF
    fi
}

flush_cache() {
    [ -z "$ERROR_CACHE" ] && return

    local hash
    hash=$(compute_hash "$ERROR_CACHE")
    local now
    now=$(date +%s)

    # 去重：相同错误在 DEDUP_WINDOW 内不重复发送
    if [ "$hash" = "$LAST_HASH" ] && [ $((now - LAST_SEND_TIME)) -lt "$DEDUP_WINDOW" ]; then
        ERROR_CACHE=""
        LINE_COUNT=0
        return
    fi

    # 构造 JSON payload
    local escaped_log
    escaped_log=$(echo "$ERROR_CACHE" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '""')
    local payload="{\"log\":${escaped_log},\"service\":\"${SERVICE_NAME}\",\"version\":\"${SERVICE_VERSION}\",\"hostname\":\"$(hostname)\"}"

    send_batch "$payload"

    LAST_HASH="$hash"
    LAST_SEND_TIME=$now
    ERROR_CACHE=""
    LINE_COUNT=0
}

# 主循环
echo "SpiderClaw collector started: service=$SERVICE_NAME log=$LOG_PATH"
tail -F "$LOG_PATH" 2>/dev/null | while IFS= read -r line; do
    # 检测错误关键词
    if echo "$line" | grep -qiE 'Error|Traceback|Exception|FAILED'; then
        ERROR_CACHE="${ERROR_CACHE}${line}\n"
        LINE_COUNT=$((LINE_COUNT + 1))

        # 批量发送条件：间隔到达 或 行数到达
        local now
        now=$(date +%s)
        if [ $LINE_COUNT -ge "$MAX_BATCH_LINES" ] || \\
           ([ $((now - LAST_SEND_TIME)) -ge "$BATCH_INTERVAL" ] && [ $LINE_COUNT -gt 0 ]); then
            flush_cache
        fi
    fi
done
'''
    collector_path = os.path.join(output_dir, "collector.sh")
    with open(collector_path, "w", encoding="utf-8") as f:
        f.write(collector_content)
    os.chmod(collector_path, 0o755)

    console.print(Panel(
        f"采集脚本模板已生成！\n\n"
        f"输出目录: [#20d5f0]{output_dir}[/#20d5f0]\n"
        f"配置文件: [#20d5f0]{conf_path}[/#20d5f0]\n"
        f"采集脚本: [#20d5f0]{collector_path}[/#20d5f0]\n\n"
        f"部署步骤：\n"
        f"1. 将上述文件复制到业务服务器 /opt/agent-sidecar/\n"
        f"2. 修改 agent-mapping.conf 中的 SERVICE_VERSION\n"
        f"3. 启动: nohup bash /opt/agent-sidecar/collector.sh > /dev/null 2>&1 &",
        title="[bold #20d5f0]init-sidecar 完成[/bold #20d5f0]",
        border_style="#20d5f0",
    ))


# 子命令
from .commands.webhook import webhook_app

app.add_typer(webhook_app, name="webhook", help="GitHub Webhook服务管理")


if __name__ == "__main__":
    app()
