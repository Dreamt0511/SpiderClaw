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

# 保持容器运行
tail -f /var/log/app/app.log
