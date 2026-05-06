#!/bin/bash
set -e

# 确保日志目录和文件存在
mkdir -p /var/log/app
touch /var/log/app/app.log

# 给采集器工具加执行权限
chmod +x /opt/agent-sidecar/collector.sh

# 启动采集器（后台，持续监控日志文件）
bash /opt/agent-sidecar/collector.sh &
echo "[biz-server] collector started, waiting for errors in /var/log/app/app.log"

# ============================================
# 业务服务模拟器（可选）
# 通过环境变量 SIMULATOR_MODE 控制：
#   ""           — 不启动（默认）
#   "continuous" — 持续循环模式，随机触发 bug
#   "web"        — HTTP 接口模式，通过 API 触发
# ============================================
SIMULATOR_MODE="${SIMULATOR_MODE:-}"
SIMULATOR_INTERVAL="${SIMULATOR_INTERVAL:-30}"
SIMULATOR_ERROR_RATE="${SIMULATOR_ERROR_RATE:-0.3}"

if [ -n "$SIMULATOR_MODE" ]; then
    echo "[biz-server] 启动业务服务模拟器 (mode=$SIMULATOR_MODE)"
    cd /opt/biz-app/app

    case "$SIMULATOR_MODE" in
        continuous)
            python3 -m src.service_simulator \
                --mode continuous \
                --interval "$SIMULATOR_INTERVAL" \
                --error-rate "$SIMULATOR_ERROR_RATE" &
            ;;
        web)
            python3 -m src.service_simulator \
                --mode web --port 9000 &
            ;;
        *)
            echo "[biz-server] 未知的 SIMULATOR_MODE: $SIMULATOR_MODE"
            ;;
    esac
    echo "[biz-server] service simulator started (mode=$SIMULATOR_MODE)"
fi

# 保持容器运行
tail -f /var/log/app/app.log
