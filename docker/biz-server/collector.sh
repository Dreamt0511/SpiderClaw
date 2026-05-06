#!/bin/bash
# SpiderClaw 远程日志采集脚本（整合版）
# ==============================================
# 两个文件即可实现全功能：collector.sh + agent-mapping.conf
# 零侵入业务代码，不需要任何修改
# ==============================================
# 使用方式：
# 1. 日志监控模式（默认）：后台运行，持续监控日志文件
#    nohup bash /opt/agent-sidecar/collector.sh > /dev/null 2>&1 &
#
# 2. 命令执行模式：运行任意命令，同时捕获输出到日志+显示到屏幕
#    bash /opt/agent-sidecar/collector.sh exec <你的命令>
#    示例：bash /opt/agent-sidecar/collector.sh exec python3 -m pytest app/tests/ -v
# ==============================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/agent-mapping.conf"

AGENT_URL="${AGENT_URL:-http://agent-host:8000/webhook/log}"
DEDUP_WINDOW="${DEDUP_WINDOW:-300}"
BATCH_INTERVAL="${BATCH_INTERVAL:-10}"
MAX_BATCH_LINES="${MAX_BATCH_LINES:-200}"
MAX_BACKOFF="${MAX_BACKOFF:-300}"

LAST_HASH=""
BACKOFF=1
ERROR_CACHE=""
LAST_SEND_TIME=$(date +%s)
LINE_COUNT=0
COLLECTING=0

# Python 错误关键词（含解释器级别的错误、pytest测试失败）
ERROR_KEYWORDS='Error|Traceback|Exception|FAILED|SyntaxError|IndentationError|ImportError|ModuleNotFoundError|NameError|RecursionError'
# Python 解释器输出特征（无时间戳的 Traceback 帧和异常行、pytest异常输出）
PYTHON_INTERPRETER_PATTERN='^Traceback|^  File ".+", line [0-9]+|^File ".+", line [0-9]+|^\w+Error:|^\w+Exception:'

# ==============================================
# 命令执行模式：运行命令并捕获输出
# ==============================================
exec_command() {
    local log_file="$LOG_PATH"
    local tmp_file=$(mktemp)
    mkdir -p "$(dirname "$log_file")"
    touch "$log_file"

    echo "[采集器] 开始执行命令：$*"
    echo "----------------------------------------"

    # 先捕获全部输出到临时文件，避免逐行写入日志导致竞态条件
    local exit_code=0
    if command -v script >/dev/null 2>&1; then
        script -q -c "$*" /dev/null > "$tmp_file" 2>&1
        exit_code=$?
    else
        "$@" > "$tmp_file" 2>&1
        exit_code=$?
    fi

    # 显示输出到终端
    cat "$tmp_file"
    echo "----------------------------------------"
    echo "[采集器] 命令执行完成，退出码：$exit_code"

    # 直接从临时文件提取错误并上报（不依赖监控模式，避免时序问题）
    # 注意：不写入 $log_file，避免后台监控模式重复采集同一份错误
    if [ "$exit_code" -ne 0 ] || grep -qiE "$ERROR_KEYWORDS" "$tmp_file" 2>/dev/null; then
        echo "[采集器] 检测到错误，正在上报..."
        # 移除 Windows 换行符后提取错误
        local clean_file=$(mktemp)
        tr -d '\r' < "$tmp_file" > "$clean_file"
        local collecting=0
        while IFS= read -r line; do
            if echo "$line" | grep -qP '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}'; then
                collecting=0
                if echo "$line" | grep -qiE "$ERROR_KEYWORDS"; then
                    ERROR_CACHE="${ERROR_CACHE}${line}\n"
                    LINE_COUNT=$((LINE_COUNT + 1))
                    collecting=1
                fi
            elif echo "$line" | grep -qE "$PYTHON_INTERPRETER_PATTERN"; then
                if [ "$collecting" -eq 0 ]; then
                    collecting=1
                    ERROR_CACHE="[no-timestamp at $(date '+%Y-%m-%d %H:%M:%S')]\n"
                    LINE_COUNT=0
                fi
                ERROR_CACHE="${ERROR_CACHE}${line}\n"
                LINE_COUNT=$((LINE_COUNT + 1))
            elif [ "$collecting" -eq 1 ]; then
                ERROR_CACHE="${ERROR_CACHE}${line}\n"
                LINE_COUNT=$((LINE_COUNT + 1))
            fi
        done < "$clean_file"
        rm -f "$clean_file"
        flush_cache
        echo "[采集器] 错误上报完成"
    fi

    rm -f "$tmp_file"
    exit $exit_code
}

# ==============================================
# 日志监控模式：原有采集逻辑
# ==============================================
compute_hash() {
    local text="$1"
    local file_line
    file_line=$(echo "$text" | grep -oP 'File "[^"]+", line \d+' | tail -1 || true)
    local error_type
    error_type=$(echo "$text" | grep -oP '[A-Z][a-zA-Z0-9]*(Error|Exception)' | head -1 || true)
    local key
    if [ -n "$file_line" ]; then
        key="${file_line}:${error_type}"
    else
        local first_line
        first_line=$(echo "$text" | head -1 | cut -c1-50)
        key="${error_type}:${first_line}"
    fi
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

    if [ "$hash" = "$LAST_HASH" ] && [ $((now - LAST_SEND_TIME)) -lt "$DEDUP_WINDOW" ]; then
        ERROR_CACHE=""
        LINE_COUNT=0
        return
    fi

    local escaped_log
    escaped_log=$(echo "$ERROR_CACHE" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '""')
    local payload="{\"log\":${escaped_log},\"service\":\"${SERVICE_NAME}\",\"version\":\"${SERVICE_VERSION}\",\"hostname\":\"$(hostname)\"}"

    send_batch "$payload"

    LAST_HASH="$hash"
    LAST_SEND_TIME=$now
    ERROR_CACHE=""
    LINE_COUNT=0
}

start_monitoring() {
    echo "SpiderClaw collector started: service=$SERVICE_NAME log=$LOG_PATH"
    touch "$LOG_PATH" 2>/dev/null || true
    while true; do
        # 内层循环：读取日志行，超时则退出（避免 while read 永久阻塞导致最后一批错误无法刷出）
        # 注意：|| true 防止 set -e 因 read -t 超时 SIGPIPE 退出脚本
        tail -n0 -F "$LOG_PATH" 2>/dev/null | while IFS= read -r -t "$BATCH_INTERVAL" line; do
            # 移除 Windows 换行符
            line="${line%$'\r'}"

            # ── 1. 时间戳行：新日志条目的开始 ──
            if echo "$line" | grep -qP '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}'; then
                if [ "$COLLECTING" -eq 1 ]; then
                    COLLECTING=0
                    # 新错误到来，先把上一组错误的缓存刷出
                    flush_cache
                fi
                if echo "$line" | grep -qiE "$ERROR_KEYWORDS"; then
                    ERROR_CACHE="${ERROR_CACHE}${line}\n"
                    LINE_COUNT=$((LINE_COUNT + 1))
                    COLLECTING=1
                    # 等待 traceback 写入（避免竞态条件）
                    sleep 0.5
                fi

            # ── 2. Python 解释器输出（无时间戳的 Traceback/语法错误等）──
            elif echo "$line" | grep -qE "$PYTHON_INTERPRETER_PATTERN"; then
                if [ "$COLLECTING" -eq 0 ]; then
                    COLLECTING=1
                    ERROR_CACHE="[no-timestamp at $(date '+%Y-%m-%d %H:%M:%S')]\n"
                    LINE_COUNT=0
                fi
                ERROR_CACHE="${ERROR_CACHE}${line}\n"
                LINE_COUNT=$((LINE_COUNT + 1))

            # ── 3. 采集模式下的续行（堆栈帧、异常描述等）──
            elif [ "$COLLECTING" -eq 1 ]; then
                ERROR_CACHE="${ERROR_CACHE}${line}\n"
                LINE_COUNT=$((LINE_COUNT + 1))

            fi

            # 批量发送（基于行数）
            if [ "$LINE_COUNT" -ge "$MAX_BATCH_LINES" ]; then
                COLLECTING=0
                flush_cache
            fi
        done || true
        # 超时或管道断开：刷出最后一批错误
        if [ "$LINE_COUNT" -gt 0 ]; then
            COLLECTING=0
            flush_cache
        fi
        sleep 1
    done
}

# ==============================================
# 主入口：判断运行模式
# ==============================================
if [ $# -gt 0 ] && [ "$1" = "exec" ]; then
    # 命令执行模式
    shift
    exec_command "$@"
else
    # 日志监控模式（默认）
    start_monitoring
fi