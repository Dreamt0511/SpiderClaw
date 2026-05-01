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
            "欢迎使用 [bold #20d5f0]SpiderClaw[/bold #20d5f0] 飞书配置向导\n\n[dim]在跳转链接创建好应用后后台将自动完成配置写入。[/dim]",
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


@app.command("config")
def config_wizard():
    """系统配置向导 — 统一入口"""
    import questionary
    from rich.panel import Panel

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

    console.print(
        Panel(
            "欢迎使用 [bold #20d5f0]SpiderClaw[/bold #20d5f0] 配置向导\n\n"
            "[dim]选择要配置的项目：[/dim]",
            title="[bold white]系统配置[/bold white]",
            border_style="#20d5f0",
        )
    )

    choice = questionary.select(
        "选择配置项目：",
        choices=[
            questionary.Choice("飞书通知 — 创建飞书应用并配置通知", value="feishu"),
            questionary.Choice("服务注册 — 注册需要监控的远程服务", value="service"),
        ],
        style=spider_style,
    ).ask()

    if not choice:
        console.print("[dim]>> 配置已取消[/dim]")
        return

    if choice == "feishu":
        _setup_feishu()
    else:
        _register_service()


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


def _register_service():
    """服务注册向导（内部函数，供 config 命令调用）"""
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

    console.print(
        Panel(
            "注册后，系统将自动监控该服务的运行时错误并尝试修复。",
            title="[bold white]服务注册[/bold white]",
            border_style="#20d5f0",
        )
    )

    mode = questionary.select(
        "选择配置方式：",
        choices=[
            questionary.Choice("自动配置 — 根据引导依次输入信息", value="auto"),
            questionary.Choice("手动配置 — 打印模板，自行编辑 services.yaml", value="manual"),
        ],
        style=spider_style,
    ).ask()

    if not mode:
        console.print("[dim]>> 配置已取消[/dim]")
        return

    if mode == "manual":
        console.print(Panel(
            "请在 [bold #20d5f0]src/config/services.yaml[/bold #20d5f0] 中添加以下内容：\n\n"
            "```yaml\n"
            "services:\n"
            '  - name: "your-service-name"\n'
            '    repo_url: "https://github.com/your-org/your-repo.git"\n'
            '    repo_local_path: "/data/repos/your-service"\n'
            '    version: ""  # 上线后运行 spiderclaw sync -n <服务名> -v <版本号>\n'
            '    git_branch: "main"\n'
            '    path_mapping:\n'
            '      "/app/": "src/"  # 可选：容器路径 → 仓库路径映射\n'
            "```\n\n"
            "配置完成后运行 [bold #20d5f0]spiderclaw sync[/bold #20d5f0] 同步代码。",
            title="[bold #20d5f0]手动配置指引[/bold #20d5f0]",
            border_style="#20d5f0",
        ))
        return

    # === 自动配置模式 ===
    console.print()

    # 1. 服务名称
    name = questionary.text(
        "服务名称（与采集脚本 SERVICE_NAME 对应）：",
        style=spider_style,
    ).ask()
    if not name:
        console.print("[dim]>> 配置已取消[/dim]")
        return

    # 2. 仓库地址
    repo_url = questionary.text(
        "Git 仓库地址：",
        style=spider_style,
        placeholder="https://github.com/your-org/your-repo.git",
    ).ask()
    if not repo_url:
        console.print("[dim]>> 配置已取消[/dim]")
        return

    # 3. 本地存储路径
    default_local = f"/data/repos/{name}"
    repo_local_path = questionary.text(
        "本地存储路径（Agent 存放代码的目录）：",
        default=default_local,
        style=spider_style,
    ).ask()
    if not repo_local_path:
        console.print("[dim]>> 配置已取消[/dim]")
        return

    # 4. 目标分支
    git_branch = questionary.text(
        "目标分支（PR 合入分支）：",
        default="main",
        style=spider_style,
    ).ask()
    if not git_branch:
        git_branch = "main"

    # 5. 路径映射
    add_mapping = questionary.confirm(
        "是否需要路径映射？（容器内路径 → 仓库路径）",
        default=False,
        style=spider_style,
    ).ask()

    path_mapping = {}
    if add_mapping:
        console.print("[dim]输入映射规则，格式: /app/=src/（输入空行结束）[/dim]")
        while True:
            rule = questionary.text(
                "映射规则：",
                style=spider_style,
                placeholder="/app/=src/",
            ).ask()
            if not rule:
                break
            if "=" in rule:
                k, v = rule.split("=", 1)
                path_mapping[k.strip()] = v.strip()
                console.print(f"  [green]✓[/green] {k.strip()} → {v.strip()}")
            else:
                console.print("  [yellow]⚠[/yellow] 格式错误，应为 /app/=src/")

    # 6. 是否立即同步版本
    sync_now = questionary.confirm(
        "是否现在同步代码？（需要提供版本号）",
        default=False,
        style=spider_style,
    ).ask()

    version = ""
    if sync_now:
        version = questionary.text(
            "目标版本（commit SHA 或 tag，留空则拉取最新分支）：",
            style=spider_style,
            placeholder="abc123 或 v1.0.0",
        ).ask() or ""

    # 写入 services.yaml
    config_path = Path("src/config/services.yaml")

    with Status("[bold #20d5f0]正在写入配置...[/bold #20d5f0]", spinner="dots", spinner_style="#20d5f0"):
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        services = data.get("services") or []

        # 检查是否已存在
        existing_idx = -1
        for i, s in enumerate(services):
            if s.get("name") == name:
                existing_idx = i
                break

        svc_data = {
            "name": name,
            "repo_url": repo_url,
            "repo_local_path": repo_local_path,
            "version": version,
            "git_branch": git_branch,
        }
        if path_mapping:
            svc_data["path_mapping"] = path_mapping

        if existing_idx >= 0:
            services[existing_idx] = svc_data
        else:
            services.append(svc_data)

        data["services"] = services

        config_path.parent.mkdir(exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # 如果指定了版本，执行同步
    sync_status = ""
    if version:
        from git import Repo, GitCommandError
        import os

        local_abs = os.path.abspath(repo_local_path)
        degraded = False

        with Status(f"[bold #20d5f0]正在同步代码到 {version}...[/bold #20d5f0]", spinner="dots", spinner_style="#20d5f0"):
            try:
                if not os.path.exists(os.path.join(local_abs, ".git")):
                    os.makedirs(local_abs, exist_ok=True)
                    Repo.clone_from(repo_url, local_abs)

                repo = Repo(local_abs)
                repo.git.fetch("origin")
                repo.git.checkout(version)
            except GitCommandError:
                try:
                    repo = Repo(local_abs)
                    repo.git.checkout(git_branch)
                    repo.git.pull("origin", git_branch)
                    degraded = True
                except GitCommandError as e:
                    sync_status = f"\n[yellow]⚠[/yellow] 同步失败: {e}"
                    degraded = True

        if not sync_status:
            sync_status = f"\n代码已同步到 {version}" + ("（降级到最新分支）" if degraded else "")
    else:
        sync_status = "\n[dim]跳过同步，后续运行 spiderclaw sync -n {0} -v <版本号> 同步代码[/dim]".format(name)

    console.print(
        Panel(
            f"服务名: [#20d5f0]{name}[/#20d5f0]\n"
            f"仓库地址: [#20d5f0]{repo_url}[/#20d5f0]\n"
            f"本地路径: [#20d5f0]{repo_local_path}[/#20d5f0]\n"
            f"目标分支: [#20d5f0]{git_branch}[/#20d5f0]\n"
            f"跟踪版本: [#20d5f0]{version or '未配置'}[/#20d5f0]\n"
            f"路径映射: [#20d5f0]{path_mapping or '无'}[/#20d5f0]"
            f"{sync_status}",
            title="[bold #20d5f0]服务注册完成[/bold #20d5f0]",
            border_style="#20d5f0",
        )
    )


@app.command("sync")
def sync_service(
    name: str = typer.Option(..., "-n", "--name", help="服务名称（对应 services.yaml 中的 name）"),
    version: str = typer.Option(..., "-v", "--version", help="目标版本（commit SHA 或 tag）"),
):
    """同步服务代码到本地 — 上线新版本时运行此命令

    从 services.yaml 读取服务配置，clone/fetch 仓库并 checkout 到指定版本，
    同时更新 services.yaml 中的 version 字段。
    """
    import yaml
    from pathlib import Path
    from rich.panel import Panel
    from rich.status import Status

    config_path = Path("src/config/services.yaml")
    if not config_path.exists():
        console.print("[bold #ff4444]错误：[/bold #ff4444] src/config/services.yaml 不存在")
        raise typer.Exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    services = data.get("services") or []
    svc = None
    svc_index = -1
    for i, s in enumerate(services):
        if s.get("name") == name:
            svc = s
            svc_index = i
            break

    if not svc:
        console.print(f"[bold #ff4444]错误：[/bold #ff4444] 服务 '{name}' 未在 services.yaml 中注册")
        console.print("[dim]请先运行 spiderclaw register 注册服务，或手动编辑 services.yaml[/dim]")
        raise typer.Exit(1)

    repo_url = svc.get("repo_url", "")
    local_path = svc.get("repo_local_path", "")
    branch = svc.get("git_branch", "main")

    if not repo_url or not local_path:
        console.print(f"[bold #ff4444]错误：[/bold #ff4444] 服务 '{name}' 缺少 repo_url 或 repo_local_path")
        raise typer.Exit(1)

    # 拉取代码
    from git import Repo, GitCommandError
    import os

    local_path = os.path.abspath(local_path)
    degraded = False

    with Status(f"[bold #20d5f0]正在同步 {name} 到版本 {version}...[/bold #20d5f0]", spinner="dots", spinner_style="#20d5f0"):
        try:
            if not os.path.exists(os.path.join(local_path, ".git")):
                console.print(f"[dim]首次 clone: {repo_url}[/dim]")
                os.makedirs(local_path, exist_ok=True)
                Repo.clone_from(repo_url, local_path)

            repo = Repo(local_path)
            repo.git.fetch("origin")
            repo.git.checkout(version)
            console.print(f"[green]✓[/green] checkout 到 {version}")
        except GitCommandError:
            console.print(f"[yellow]⚠[/yellow] 无法 checkout 到 {version}，降级到最新 {branch}")
            try:
                repo = Repo(local_path)
                repo.git.checkout(branch)
                repo.git.pull("origin", branch)
            except GitCommandError as e:
                console.print(f"[bold #ff4444]错误：[/bold #ff4444] 降级也失败: {e}")
                raise typer.Exit(1)
            degraded = True

    # 更新 services.yaml 中的 version 字段
    services[svc_index]["version"] = version
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    status_text = "（降级到最新分支）" if degraded else ""
    console.print(Panel(
        f"服务: [#20d5f0]{name}[/#20d5f0]\n"
        f"版本: [#20d5f0]{version}[/#20d5f0]\n"
        f"本地路径: [#20d5f0]{local_path}[/#20d5f0]\n"
        f"状态: {'⚠️ 降级' if degraded else '✅ 精确匹配'} {status_text}\n\n"
        f"services.yaml 已更新 version 字段。",
        title="[bold #20d5f0]sync 完成[/bold #20d5f0]",
        border_style="#20d5f0",
    ))


# 子命令
from .commands.webhook import webhook_app

app.add_typer(webhook_app, name="webhook", help="GitHub Webhook服务管理")


if __name__ == "__main__":
    app()
