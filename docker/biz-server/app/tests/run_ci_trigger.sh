#!/bin/bash
# ============================================================
# CI 触发器脚本 — 在 biz-server 容器内运行测试并触发 AutoFix
# ============================================================
# 用法:
#   docker exec biz-server bash /opt/biz-app/app/tests/run_ci_trigger.sh [场景名]
#
# 场景:
#   divide_by_zero   — 除零错误
#   empty_average    — 空列表求平均
#   negative_sqrt    — 负数平方根
#   user_not_found   — 查询不存在的用户
#   all              — 所有场景依次执行（默认）
# ============================================================

set -euo pipefail

APP_DIR="/opt/biz-app/app"
SCENARIO="${1:-all}"

run_test() {
    local scenario="$1"
    echo ""
    echo "=========================================="
    echo "  触发场景: $scenario"
    echo "=========================================="

    cd "$APP_DIR"
    python3 -m src.service_simulator \
        --mode single \
        --trigger "$scenario" || true

    # 等待 collector 处理并上报
    sleep 3
}

case "$SCENARIO" in
    all)
        echo "将在 5 秒后依次触发所有错误场景..."
        echo "请确保 SpiderClaw Agent 已启动，并观察修复流程。"
        sleep 5
        run_test "divide_by_zero"
        run_test "empty_average"
        run_test "negative_sqrt"
        run_test "user_not_found"
        run_test "delete_nonexistent_user"
        run_test "chain_reaction"

        # 执行自定义测试（main.py）
        echo ""
        echo "=========================================="
        echo "  执行自定义测试: src/main.py"
        echo "=========================================="
        cd "$APP_DIR"
        python3 -m src.main || true
        sleep 3

        echo ""
        echo "所有场景已触发完毕。"
        echo "查看 Agent 日志: docker logs spiderclaw-agent -f"
        echo "查看 biz-server 日志: docker logs biz-server"
        ;;
    divide_by_zero|empty_average|negative_sqrt|user_not_found|delete_nonexistent_user|discount_negative_rate|create_duplicate_email|chain_reaction)
        run_test "$SCENARIO"
        ;;
    *)
        echo "未知场景: $SCENARIO"
        echo "可用场景: all, divide_by_zero, empty_average, negative_sqrt,"
        echo "          user_not_found, delete_nonexistent_user,"
        echo "          discount_negative_rate, create_duplicate_email, chain_reaction"
        exit 1
        ;;
esac
