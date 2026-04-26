"""CLI主入口"""
import sys
from typing import Optional
from pathlib import Path
import typer
from rich.console import Console

app = typer.Typer(
    name="spiderclaw",
    help="SpiderClaw-事件驱动的自动诊断与修复系统",
    add_completion=False,
    rich_markup_mode="rich",
    invoke_without_command=True
)
console = Console()
CONFIG_PATH = Path("config/agent-config.yaml")


def _setup_feishu():
    """飞书通知配置向导"""
    import questionary
    import yaml
    from rich.panel import Panel
    from rich.status import Status

    spider_style = questionary.Style([
        ('qmark', 'fg:#4488ff bold'),
        ('question', 'fg:#66bbff bold'),
        ('answer', 'fg:#4488ff bold'),
        ('pointer', 'fg:#66bbff bold'),
        ('highlighted', 'fg:#66bbff bold'),
        ('selected', 'fg:#66bbff'),
        ('instruction', 'fg:#808080 dim'),
    ])

    console.clear()
    console.print(Panel(
        "欢迎使用 [bold #4488ff]SpiderClaw[/bold #4488ff] 飞书配置向导\n\n[dim]扫码授权后将自动完成应用创建和配置写入。[/dim]",
        title="[bold white]飞书通知配置[/bold white]",
        border_style="#2453fc"
    ))

    confirm = questionary.confirm(
        "是否开始配置飞书通知？",
        default=True,
        style=spider_style
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
        console.print("[bold #ff4444][配置失败!][/bold #ff4444]  授权过程出错，请重试！")
        return

    with Status("[bold #4488ff]正在写入配置文件...[/bold #4488ff]", spinner="dots", spinner_style="#66bbff"):
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
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        except Exception as e:
            console.print(f"[bold #ff4444][写入配置失败!][/bold #ff4444]  错误信息: {str(e)}")
            return

    console.print(Panel(
        f"飞书通知配置成功！\n\n"
        f"应用信息：\n"
        f"App ID: [#4488ff]{result['app_id']}[/#4488ff]\n"
        f"App Secret: [#4488ff]{result['app_secret']}[/#4488ff]\n\n"
        f"后续配置：\n"
        f"通过在终端中输入 lark-cli auth status 获取当前登录状态，即可获取userOpenId。\n"
        f"请在 [#4488ff]config/agent-config.yaml[/#4488ff] 中添加需要通知的用户/群组ID。\n\n"
        f"配置完成后，在SpiderClaw总监控服务启动后，系统将自动发送修复结果通知！",
        border_style="#66bbff"
    ))


@app.callback()
def main(
    ctx: typer.Context,
    debug: bool = typer.Option(False, "--debug", help="开启调试模式"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="配置文件路径"),
    port: int = typer.Option(8000, "--port", "-p", help="Webhook监听端口"),
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Webhook监听地址"),
    reload: bool = typer.Option(False, "--reload", help="热重载")
):
    """
    SpiderClaw 事件驱动的自动诊断与修复系统

    不带参数直接运行将启动Webhook总监控服务
    """
    global CONFIG_PATH
    if config:
        CONFIG_PATH = Path(config)

    if ctx.invoked_subcommand is None:
        from src.monitor.webhook_server import run_webhook_server
        run_webhook_server(host=host, port=port, reload=reload)


@app.command("setup")
def setup_wizard():
    """系统配置向导（飞书通知等）"""
    _setup_feishu()


# 子命令（webhook.py 已做懒加载，模块级无重 imports）
from .commands.webhook import webhook_app

app.add_typer(webhook_app, name="webhook", help="GitHub Webhook服务管理")


if __name__ == "__main__":
    app()
