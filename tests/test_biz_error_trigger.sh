#!/bin/bash
# ============================================================
# 业务服务错误触发测试脚本（从宿主机执行）
# ============================================================
# 用法:
#   bash tests/test_biz_error_trigger.sh [场景名]
#
# 前置条件:
#   - Docker 环境正在运行 (docker-compose up -d)
#   - SpiderClaw Agent 正常运行
#   - collector 脚本正常运行
#
# 原理:
#   通过 docker exec 在 biz-server 容器内执行模拟器，
#   模拟器生成的错误日志会被 collector 捕获并上报到
#   SpiderClaw Agent，触发自动修复流程。
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SCENARIO="${1:-all}"
INTERVAL="${2:-}"

echo "=========================================="
echo "  SpiderClaw 业务服务错误触发测试"
echo "=========================================="
echo ""

# 检查 Docker 容器状态
check_container() {
    local name="$1"
    if docker ps --format '{{.Names}}' | grep -q "^${name}$"; then
        echo "  ✅ $name 运行中"
        return 0
    else
        echo "  ❌ $name 未运行"
        return 1
    fi
}

echo "检查环境..."
check_container spiderclaw-agent || {
    echo "请先执行 docker-compose up -d 启动服务"
    exit 1
}
check_container biz-server || {
    echo "请先执行 docker-compose up -d 启动服务"
    exit 1
}

echo ""
echo "SpiderClaw Agent 状态:"
curl -s http://localhost:8000/health 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  (无法获取状态)"

echo ""
echo "=========================================="

case "$SCENARIO" in
    all)
        echo "将在 5 秒后依次触发所有错误场景..."
        echo "请观察 SpiderClaw 的修复流程。"
        echo ""
        echo "查看 Agent 日志: docker logs spiderclaw-agent -f"
        echo "查看采集器日志: docker logs biz-server"
        echo "查看修复详情: cat src/logs/audit.jsonl"
        sleep 5

        docker exec biz-server bash /opt/biz-app/app/tests/run_ci_trigger.sh all
        ;;

    # 连续模式：以固定间隔持续触发错误（适合长时间测试）
    continuous)
        interval="${INTERVAL:-30}"
        echo "持续模式启动，每 ${interval} 秒随机触发一次错误..."
        echo "按 Ctrl+C 停止"
        echo ""
        round=1
        while true; do
            echo "--- 第 $round 轮 ---"
            docker exec biz-server python3 -m src.service_simulator \
                --mode continuous \
                --interval 1 \
                --error-rate 1.0 \
                || true
            echo "等待 ${interval}s..."
            sleep "$interval"
            round=$((round + 1))
        done
        ;;

    # Web 模式：启动模拟器的 HTTP 接口，通过 curl 触发
    web)
        echo "启动 Web 模式模拟器（后台），可通过 API 触发错误..."
        docker exec -d biz-server python3 -m src.service_simulator \
            --mode web --port 9000
        sleep 2
        echo ""
        echo "API 用法:"
        echo "  curl http://localhost:9000/health          # 健康检查"
        echo "  curl http://localhost:9000/scenarios        # 查看场景"
        echo "  curl -X POST http://localhost:9000/trigger \\"
        echo "    -H 'Content-Type: application/json' \\"
        echo "    -d '{\"scenario\":\"divide_by_zero\",\"trigger_bug\":true}'"
        echo ""
        echo "示例：触发除零错误"
        curl -X POST http://localhost:9000/trigger \
            -H "Content-Type: application/json" \
            -d '{"scenario":"divide_by_zero","trigger_bug":true}' 2>/dev/null || \
            echo "(请确认容器内端口 9000 已映射)"
        ;;

    # 只运行自定义测试（main.py）
    main)
        echo "执行自定义测试: src/main.py..."
        docker exec biz-server bash /opt/agent-sidecar/collector.sh exec \
            python3 /opt/biz-app/app/src/main.py
        ;;

    # 直接使用 collector 的 exec 模式运行 pytest
    pytest)
        echo "通过 collector 运行 pytest 测试（会触发所有 bug）..."
        docker exec biz-server bash /opt/agent-sidecar/collector.sh exec \
            python3 -m pytest /opt/biz-app/app/src/tests/ -v
        ;;

    # 场景名直接透传
    divide_by_zero|empty_average|negative_sqrt|user_not_found|delete_nonexistent_user|discount_negative_rate|create_duplicate_email|chain_reaction)
        docker exec biz-server bash /opt/biz-app/app/tests/run_ci_trigger.sh "$SCENARIO"
        ;;

    *)
        echo "用法: $0 [场景名|continuous|web|main|pytest]"
        echo ""
        echo "场景:"
        echo "  all                    — 依次触发所有错误（默认）"
        echo "  main                   — 只运行 src/main.py 自定义测试"
        echo "  continuous [间隔]      — 持续模式，每 N 秒触发一次"
        echo "  web                    — 启动 HTTP 接口，通过 curl 控制"
        echo "  pytest                 — 运行 pytest 测试触发错误"
        echo ""
        echo "单场景:"
        echo "  divide_by_zero         — 除零错误"
        echo "  empty_average          — 空列表求平均"
        echo "  negative_sqrt          — 负数平方根"
        echo "  user_not_found         — 查询不存在的用户"
        echo "  delete_nonexistent_user — 删除不存在的用户"
        echo "  chain_reaction         — 连锁异常"
        exit 1
        ;;
esac

echo ""
echo "测试完成。"
echo "查看修复流程: docker logs spiderclaw-agent -f"
