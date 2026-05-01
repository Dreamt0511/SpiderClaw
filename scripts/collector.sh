#!/bin/bash
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
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$AGENT_URL" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        --connect-timeout 10 \
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
        if [ $LINE_COUNT -ge "$MAX_BATCH_LINES" ] || \
           ([ $((now - LAST_SEND_TIME)) -ge "$BATCH_INTERVAL" ] && [ $LINE_COUNT -gt 0 ]); then
            flush_cache
        fi
    fi
done
