# Docker 部署使用说明

## 快速开始

### 1. 配置环境变量
```bash
# 复制环境变量示例文件
cp .env.docker.example .env

# 编辑 .env 文件，填入实际的配置信息
# 至少需要配置以下必填项：
# - GITHUB__TOKEN: GitHub个人访问令牌
# - OPENAI__API_KEY: OpenAI API密钥
# 如需使用飞书通知，还需要配置LARK相关配置
```

### 2. 准备配置文件
```bash
# 复制Agent配置文件
cp src/config/agent-config.example.yaml src/config/agent-config.yaml

# 复制服务配置文件
cp src/config/services.docker.yaml src/config/services.yaml

# 根据实际需求修改配置文件内容
```

### 3. 启动服务
```bash
# 构建并启动所有服务
docker compose up -d --build

# 查看服务状态
docker compose ps

# 查看日志
docker compose logs -f spiderclaw
```

## 配置说明

### 环境变量配置
所有配置都可以通过环境变量设置，支持嵌套配置（使用双下划线`__`分隔），优先级高于配置文件。

示例：
- `AGENT__ENABLED=true` 对应配置文件中 `agent.enabled`
- `LARK__APP_ID=cli_xxxxxx` 对应配置文件中 `lark.app_id`

完整的环境变量列表参考 `.env.docker.example` 文件。

### 配置文件挂载
容器内的配置文件已经挂载到宿主机，修改后无需重建镜像：
- `src/config/agent-config.yaml` → Agent主配置
- `src/config/services.yaml` → 服务注册配置

修改配置后重启服务即可生效：
```bash
docker compose restart spiderclaw
```

### 脚本文件挂载
以下脚本文件也已挂载到宿主机，修改后无需重建镜像：
- `entrypoint.sh` → 容器启动脚本
- `docker/biz-server/collector.sh` → 业务服务采集脚本
- `docker/biz-server/entrypoint.sh` → 业务服务启动脚本
- `docker/biz-server/agent-mapping.conf` → 采集器配置

## 常见问题

### 1. 无法连接GitHub
如果容器内无法访问GitHub，可以配置代理：
在 `.env` 文件中添加：
```bash
HTTP_PROXY=http://host.docker.internal:1080
HTTPS_PROXY=http://host.docker.internal:1080
NO_PROXY=localhost,127.0.0.1,spiderclaw,biz-server
```
注意：`host.docker.internal` 是Docker Desktop特有的主机别名，Linux系统需要替换为宿主机实际IP。

### 2. 飞书通知失败
确保：
1. 已正确配置 `LARK__APP_ID` 和 `LARK__APP_SECRET`
2. 飞书应用已开通所需权限（消息发送、多维表格操作等）
3. 容器内可以访问飞书API（`open.feishu.cn`）

### 3. 数据持久化
容器内的数据已经通过volume持久化：
- `spiderclaw-data` → 存放克隆的代码仓库等数据
- `spiderclaw-logs` → 存放日志文件

### 4. 常用命令
```bash
# 查看服务日志
docker compose logs -f spiderclaw

# 进入容器内部
docker compose exec spiderclaw bash

# 重启服务
docker compose restart spiderclaw

# 停止服务
docker compose down

# 停止服务并删除volume（会丢失所有数据）
docker compose down -v
```

## 业务服务部署
biz-server是模拟的业务服务，用于测试日志采集功能：
```bash
# 单独启动biz-server
docker compose up -d biz-server

# 查看biz-server日志
docker compose logs -f biz-server
```

biz-server会自动将日志写入 `/var/log/app/app.log`，采集器会监控这个文件的错误日志并上报到spiderclaw服务。
