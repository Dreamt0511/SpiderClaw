#!/bin/bash
set -e

# 源码挂载后需要重新 install -e（更新元数据）
pip install -e . -q 2>/dev/null || true

# 初始化配置文件（如果不存在）
if [ ! -f /app/src/config/agent-config.yaml ]; then
    echo "[entrypoint] 初始化 agent-config.yaml 配置文件..."
    cp /app/src/config/agent-config.yaml.default /app/src/config/agent-config.yaml
fi

if [ ! -f /app/src/config/services.yaml ]; then
    echo "[entrypoint] 初始化 services.yaml 配置文件..."
    cp /app/src/config/services.yaml.default /app/src/config/services.yaml
fi

# 确保必要的目录存在
mkdir -p /app/data/repos
mkdir -p /app/src/logs

# Git 全局配置（使用system级别，避免被挂载的只读.gitconfig覆盖）
# 先确保系统级配置文件存在
touch /etc/gitconfig
git config --system user.name "SpiderClaw AutoFix"
git config --system user.email "spiderclaw@local.dev"
git config --system http.sslVerify false
git config --system core.autocrlf false
# 代理由Docker Desktop全局配置，无需在容器内单独设置

# Git 凭据配置（用于 push 到 GitHub）
CONFIG_FILE="/app/src/config/agent-config.yaml"
if [ -f "$CONFIG_FILE" ]; then
    GITHUB_TOKEN=$(grep -m1 'token:' "$CONFIG_FILE" | sed 's/.*token:\s*//' | tr -d ' "' | tr -d $'\r')
    if [ -n "$GITHUB_TOKEN" ]; then
        echo "[entrypoint] 配置 Git 凭据（使用 GitHub token）..."
        git config --system credential.helper store
        echo "https://Dreamt0511:${GITHUB_TOKEN}@github.com" > /root/.git-credentials
        chmod 600 /root/.git-credentials
        echo "[entrypoint] Git 凭据配置完成"
    fi
fi

# 验证 lark-cli 安装并初始化配置
if [ -x "$(command -v lark)" ]; then
    echo "[entrypoint] lark-cli 已安装，版本: $(lark --version)"

    # 从 agent-config.yaml 读取飞书凭据，自动初始化 lark-cli 配置
    CONFIG_FILE="/app/src/config/agent-config.yaml"
    if [ -f "$CONFIG_FILE" ]; then
        LARK_APP_ID=$(grep -m1 'app_id:' "$CONFIG_FILE" | sed 's/.*app_id:\s*//' | tr -d ' "' | tr -d $'\r')
        LARK_APP_SECRET=$(grep -m1 'app_secret:' "$CONFIG_FILE" | sed 's/.*app_secret:\s*//' | tr -d ' "' | tr -d $'\r')

        if [ -n "$LARK_APP_ID" ] && [ -n "$LARK_APP_SECRET" ]; then
            echo "[entrypoint] 初始化 lark-cli 配置 (app_id: $LARK_APP_ID)..."
            echo "$LARK_APP_SECRET" | lark config init --app-id "$LARK_APP_ID" --app-secret-stdin 2>&1
            echo "[entrypoint] lark-cli 配置完成"
        else
            echo "[entrypoint] WARNING: 未找到 lark app_id/app_secret，跳过 lark-cli 配置"
        fi
    fi
else
    echo "[entrypoint] WARNING: lark-cli 未安装，飞书通知功能将不可用"
fi

echo "[entrypoint] 容器初始化完成，启动命令: $*"
exec "$@"
