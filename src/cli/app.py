"""CLI主入口"""
import asyncio
import signal
import sys
from typing import Optional
import typer
from rich.console import Console
from rich.traceback import install

# 安装富文本错误处理
install(show_locals=True)

# 初始化Typer应用
app = typer.Typer(
    name="spiderclaw",
    help="事件驱动的自动诊断与修复系统",
    add_completion=False,
    rich_markup_mode="rich"
)
console = Console()

# 全局状态
shutdown_event = asyncio.Event()


def handle_signal(signal_num, frame):
    """处理系统信号"""
    signal_name = signal.Signals(signal_num).name
    console.print(f"\n[yellow]Received signal {signal_name}, initiating graceful shutdown...[/yellow]")
    shutdown_event.set()


# 注册信号处理
signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


async def run_async(coro):
    """运行异步任务，支持优雅关闭"""
    task = asyncio.create_task(coro)

    # 等待任务完成或收到关闭信号
    done, pending = await asyncio.wait(
        [task, shutdown_event.wait()],
        return_when=asyncio.FIRST_COMPLETED
    )

    if shutdown_event.is_set():
        # 取消正在运行的任务
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            console.print("[green]Task cancelled successfully[/green]")

    # 检查是否有异常
    if task.done() and task.exception():
        console.print(f"[red]Task failed with exception: {task.exception()}[/red]")
        sys.exit(1)


@app.callback()
def main(
    debug: bool = typer.Option(False, "--debug", help="开启调试模式"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="配置文件路径")
):
    """全局配置"""
    pass


# 导入子命令
from .commands.webhook import webhook_app

app.add_typer(webhook_app, name="webhook", help="GitHub Webhook服务管理")


if __name__ == "__main__":
    app()
