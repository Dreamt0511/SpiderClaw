"""
业务服务模拟器 — 模拟真实业务系统运行，周期触发带 bug 的操作。

运行模式：
  1. 持续运行模式（默认）：后台循环，随机触发各模块操作
  2. 单次触发模式：python service_simulator.py --trigger <场景名>
  3. Web 服务模式：python service_simulator.py --web

环境变量控制：
  ERROR_RATE: 0.0~1.0，控制操作中触发 bug 的比例（默认 0.3）
  INTERVAL: 操作间隔秒数（默认 15）
  SERVICE_NAME: 上报时的服务名（默认 order-service）
"""
import argparse
import logging
import os
import random
import signal
import sys
import time

# 确保能找到同级模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logger  # noqa: F401 — 初始化日志配置（写入 /var/log/app/app.log）

from calculator import divide, average, discount, sqrt_approx
from user_service import get_user, get_user_email, create_user, delete_user

logger = logging.getLogger("service_simulator")
logger.setLevel(logging.DEBUG)

# ============================================================
# 模拟业务场景：每个场景包含"正常操作"和"会触发 bug 的异常操作"
# ============================================================

SCENARIOS = {
    "divide_by_zero": {
        "description": "除法除零错误 — ZeroDivisionError",
        "normal": lambda: divide(10, 2),
        "buggy": lambda: divide(10, 0),
    },
    "empty_average": {
        "description": "空列表求平均 — ZeroDivisionError",
        "normal": lambda: average([1, 2, 3]),
        "buggy": lambda: average([]),
    },
    "negative_sqrt": {
        "description": "负数平方根 — ValueError",
        "normal": lambda: sqrt_approx(4),
        "buggy": lambda: sqrt_approx(-1),
    },
    "user_not_found": {
        "description": "查询不存在的用户 — KeyError",
        "normal": lambda: get_user(1),
        "buggy": lambda: get_user(999),
    },
    "delete_nonexistent_user": {
        "description": "删除不存在的用户 — KeyError",
        "normal": lambda: delete_user(1),
        "buggy": lambda: delete_user(999),
    },
    "discount_negative_rate": {
        "description": "折扣率为负数，返回错误结果（逻辑错误，不抛异常）",
        "normal": lambda: discount(100, 0.2),
        "buggy": lambda: discount(100, -0.5),
    },
    "create_duplicate_email": {
        "description": "创建重复 email 用户（逻辑错误，不抛异常）",
        "normal": lambda: create_user("New", "new@test.com", "user"),
        "buggy": lambda: (create_user("dup", "alice@example.com", "user"),
                          get_user_email(1)),
    },
    "chain_reaction": {
        "description": "连锁反应：先除零再平均空列表",
        "normal": lambda: (divide(10, 2), average([1, 2])),
        "buggy": lambda: (divide(10, 0), average([])),
    },
}


def run_scenario(name: str, trigger_bug: bool):
    """运行单个场景，返回是否出错"""
    scenario = SCENARIOS.get(name)
    if not scenario:
        logger.error(f"未知场景: {name}")
        return False

    fn = scenario["buggy"] if trigger_bug else scenario["normal"]
    label = "触发 bug" if trigger_bug else "正常操作"
    try:
        fn()
        logger.info(f"[{name}] {label} — 执行完成（可能无异常）")
        return False
    except Exception as e:
        logger.exception(f"[{name}] {label} — 异常: {type(e).__name__}: {e}")
        return True


def run_random_round(error_rate: float):
    """随机执行一轮业务操作，按概率触发 bug"""
    name = random.choice(list(SCENARIOS.keys()))
    trigger = random.random() < error_rate
    return run_scenario(name, trigger)


# ============================================================
# 模式1：持续运行模式（默认）
# ============================================================
def run_continuous(interval: float = 15, error_rate: float = 0.3):
    """后台循环模式：持续模拟业务操作"""
    logger.info("=" * 60)
    logger.info("业务服务模拟器启动（持续模式）")
    logger.info(f"  操作间隔: {interval}s")
    logger.info(f"  错误概率: {error_rate:.0%}")
    logger.info(f"  场景数量: {len(SCENARIOS)}")
    logger.info("=" * 60)

    running = True

    def _stop(signum, frame):
        nonlocal running
        logger.info("收到停止信号，正在退出...")
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    round_num = 0
    while running:
        round_num += 1
        logger.info(f"\n--- 第 {round_num} 轮操作 ---")

        # 每轮执行 1~3 次操作
        ops = random.randint(1, 3)
        has_error = False
        errors = []
        for i in range(ops):
            try:
                if run_random_round(error_rate):
                    has_error = True
            except Exception as e:
                has_error = True
                errors.append(str(e))

        status = "有错误" if has_error else "全部正常"
        logger.info(f"第 {round_num} 轮完成，{ops} 次操作，{status}")
        if errors:
            logger.info(f"本轮错误: {', '.join(errors)}")

        # 等待下一次循环（每秒检查一次退出信号）
        for _ in range(int(interval)):
            if not running:
                break
            time.sleep(1)

    logger.info("业务服务模拟器已停止")


# ============================================================
# 模式2：Web 服务模式（通过 HTTP 触发操作）
# ============================================================
def run_web(host: str = "0.0.0.0", port: int = 9000):
    """启动一个简单的 HTTP 服务，通过 API 触发业务操作"""
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
    except ImportError:
        logger.error("Web 模式需要 Python 标准库 http.server，当前环境不支持")
        sys.exit(1)

    class SimulatorHandler(BaseHTTPRequestHandler):
        """HTTP 请求处理器"""

        def _send_json(self, status_code: int, data: dict):
            import json
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

        def do_GET(self):
            path = self.path.rstrip("/")

            if path == "/health":
                self._send_json(200, {
                    "status": "ok",
                    "service": "order-service-simulator",
                    "scenarios": list(SCENARIOS.keys()),
                })

            elif path == "/scenarios":
                info = {
                    n: {"description": s["description"]}
                    for n, s in SCENARIOS.items()
                }
                self._send_json(200, info)

            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.rstrip("/")

            if path == "/trigger":
                import json
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                try:
                    data = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    self._send_json(400, {"error": "invalid json"})
                    return

                name = data.get("scenario", random.choice(list(SCENARIOS.keys())))
                trigger_bug = data.get("trigger_bug", True)

                if name not in SCENARIOS:
                    self._send_json(400, {"error": f"unknown scenario: {name}"})
                    return

                try:
                    had_error = run_scenario(name, trigger_bug)
                    self._send_json(200, {
                        "scenario": name,
                        "trigger_bug": trigger_bug,
                        "had_error": had_error,
                        "available_scenarios": list(SCENARIOS.keys()),
                    })
                except Exception as e:
                    logger.exception(f"Web 触发异常: {e}")
                    self._send_json(500, {"error": str(e)})

            elif path == "/random":
                error_rate = float(self.headers.get("X-Error-Rate", "0.3"))
                had_error = run_random_round(error_rate)
                self._send_json(200, {
                    "had_error": had_error,
                    "error_rate": error_rate,
                })

            else:
                self._send_json(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            logger.debug(f"HTTP {self.command} {self.path} — {args}")

    server = HTTPServer((host, port), SimulatorHandler)
    logger.info(f"业务服务模拟器 Web 模式启动: http://{host}:{port}")
    logger.info(f"  GET  /health     — 健康检查")
    logger.info(f"  GET  /scenarios  — 查看可用场景")
    logger.info(f"  POST /trigger    — 触发指定场景")
    logger.info(f"  POST /random     — 随机触发（带 X-Error-Rate 控制）")
    logger.info(f"  可用场景: {', '.join(SCENARIOS.keys())}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Web 服务已停止")
        server.server_close()


# ============================================================
# 模式3：单次触发模式
# ============================================================
def run_single_trigger(scenario_name: str, trigger_bug: bool = True):
    """执行单个场景并退出"""
    logger.info(f"单次触发模式: scenario={scenario_name}, trigger_bug={trigger_bug}")
    had_error = run_scenario(scenario_name, trigger_bug)
    sys.exit(1 if had_error else 0)


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="业务服务模拟器 — 为 AutoFix 系统产生测试事件")
    parser.add_argument("--mode", choices=["continuous", "single", "web"],
                        default="continuous",
                        help="运行模式（默认 continuous）")
    parser.add_argument("--trigger", type=str, default=None,
                        help="单次触发的场景名（需同时 --trigger-bug 或 --no-bug）")
    parser.add_argument("--trigger-bug", action="store_true", default=True,
                        help="单次触发时执行有 bug 的操作")
    parser.add_argument("--no-bug", action="store_false", dest="trigger_bug")
    parser.add_argument("--interval", type=float,
                        default=float(os.environ.get("INTERVAL", "15")),
                        help="持续模式的循环间隔秒数（默认 15）")
    parser.add_argument("--error-rate", type=float,
                        default=float(os.environ.get("ERROR_RATE", "0.3")),
                        help="错误触发概率 0.0~1.0（默认 0.3）")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Web 模式监听地址")
    parser.add_argument("--port", type=int, default=9000,
                        help="Web 模式监听端口")
    parser.add_argument("--list-scenarios", action="store_true",
                        help="列出所有可用场景并退出")

    args = parser.parse_args()

    if args.list_scenarios:
        print("可用场景：")
        for name, info in SCENARIOS.items():
            print(f"  {name}: {info['description']}")
        return

    if args.trigger:
        if args.trigger not in SCENARIOS:
            print(f"错误：未知场景 '{args.trigger}'")
            print(f"可用场景: {', '.join(SCENARIOS.keys())}")
            sys.exit(1)
        run_single_trigger(args.trigger, args.trigger_bug)
        return

    if args.mode == "web":
        run_web(args.host, args.port)
    else:
        run_continuous(args.interval, args.error_rate)


if __name__ == "__main__":
    main()
