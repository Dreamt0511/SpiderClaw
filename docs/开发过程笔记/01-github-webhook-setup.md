# GitHub Webhook 服务部署指南

## 功能概述
GitHub Webhook服务负责接收GitHub发送的事件，验证签名有效性，转换为内部统一事件格式后发送到事件总线，供后续的自动诊断与修复流程使用。

## 支持的事件类型
1. **Workflow runs**: CI流水线运行结束事件，用于捕获测试失败
2. **Pull requests**: PR创建/更新事件，用于关联修复代码
3. **Check runs**: CI检查运行结束事件，用于捕获具体的检查失败项

## 部署步骤

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 生成Webhook密钥
```bash
openssl rand -hex 32
```
保存生成的密钥，后续配置会用到。

### 3. GitHub仓库配置
1. 进入GitHub仓库 → Settings → Webhooks → Add webhook
2. 填写Payload URL: `https://your-domain.com/webhook/github`
3. Content type选择`application/json`
4. Secret填写上一步生成的密钥
5. 在"Which events would you like to trigger this webhook?"中选择:
   - Workflow runs
   - Pull requests
   - Check runs
6. 点击"Add webhook"保存

### 4. 服务配置
#### 方式一：使用配置文件（推荐）
```bash
# 复制配置模板
cp src/config/agent-config.example.yaml src/config/agent-config.yaml

# 编辑配置文件，填写GitHub Webhook密钥
vim src/config/agent-config.yaml
```

#### 方式二：使用环境变量
```bash
export WEBHOOK__GITHUB__SECRET="your-webhook-secret-here"
export WEBHOOK__GITHUB__PORT=8000
```

#### 方式三：使用命令行参数
```bash
python main.py webhook start --secret "your-webhook-secret-here" --port 8000
```

### 5. 启动服务
```bash
# 使用配置文件启动
python main.py webhook start --config src/config/agent-config.yaml

# 开发模式（热重载）
python main.py webhook start --reload --secret "test-secret"
```

### 6. 验证服务
```bash
# 检查健康状态
curl http://localhost:8000/health

# 预期输出:
# {
#   "status": "ok",
#   "service": "github-webhook",
#   "start_time": "2024-04-24T12:00:00+00:00",
#   "queue_size": 0,
#   "published_count": 0,
#   "dropped_count": 0,
#   "duplicate_count": 0,
#   "processed_ids_count": 0,
#   "uptime_seconds": 123.45
# }
```

## 生产部署建议
1. **反向代理**: 使用Nginx作为反向代理，配置HTTPS和限流
2. **进程管理**: 使用systemd或supervisor管理服务进程
3. **日志收集**: 将JSON日志接入ELK或Loki等日志系统
4. **监控告警**: 监控/health端点，配置队列长度、错误率等告警规则

## 配置说明

### 核心配置项
| 配置项                             | 说明                       | 默认值                                |
| ---------------------------------- | -------------------------- | ------------------------------------- |
| webhook.github.secret              | GitHub Webhook密钥         | 必填                                  |
| webhook.github.host                | 监听主机地址               | 0.0.0.0                               |
| webhook.github.port                | 监听端口                   | 8000                                  |
| webhook.github.allowed_events      | 允许的事件类型             | workflow_run, pull_request, check_run |
| webhook.github.event_queue_maxsize | 事件队列最大容量           | 1000                                  |
| webhook.github.max_processed_ids   | 最大保存的已处理事件ID数量 | 10000                                 |
| logging.level                      | 日志级别                   | INFO                                  |
| logging.dir                        | 日志存储目录               | logs                                  |
| logging.retention_days             | 日志保留天数               | 30                                    |

## 测试方法
### 本地测试Webhook
可以使用ngrok将本地服务暴露到公网，然后在GitHub中配置Webhook地址为ngrok提供的URL。

```bash
# 启动ngrok
ngrok http 8000

# 启动本地服务
python main.py webhook start --secret "your-secret"
```

### 模拟GitHub Webhook请求
```bash
# 生成签名
SECRET="your-secret"
PAYLOAD='{"action": "completed", "repository": {"full_name": "owner/repo"}, "workflow_run": {"head_branch": "main", "conclusion": "failure"}}'
SIGNATURE="sha256=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')"

# 发送请求
curl -X POST http://localhost:8000/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Delivery: test-$(date +%s)" \
  -H "X-GitHub-Event: workflow_run" \
  -H "X-Hub-Signature-256: $SIGNATURE" \
  -d "$PAYLOAD"
```

## 常见问题

### Q: Webhook返回403 Forbidden
A: 签名验证失败，请检查GitHub配置的Secret和服务端配置的Secret是否一致。

### Q: Webhook返回503 Service Unavailable
A: 事件队列已满，说明下游处理速度跟不上事件产生速度。可以适当调大event_queue_maxsize配置，或者优化下游处理性能。

### Q: 如何查看日志？
A: 日志默认保存在logs/目录下，按天滚动。生产环境建议配置日志收集系统。
