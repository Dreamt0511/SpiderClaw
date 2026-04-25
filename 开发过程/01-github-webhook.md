# GitHub Webhook 监听服务

完成了 GitHub Webhook 监听服务的开发，所有功能都已经实现并测试通过。

---

## 功能特性

### 1. 事件接收
支持三种 GitHub 事件：
- **Workflow runs**：CI 流水线运行结束
- **Pull requests**：PR 创建/更新
- **Check runs**：CI 检查运行结束

### 2. 安全机制
- HMAC-SHA256 签名验证，防止伪造请求
- 基于 `X-GitHub-Delivery` 的幂等去重
- 事件大小限制，防止恶意请求

### 3. 可靠性设计
- **反压机制**：队列满时返回 503，由 GitHub 自动重试
- **优雅关闭**：进程退出时先处理完队列中所有事件
- **LRU 去重**：最多保存 10000 个已处理事件 ID，自动清理过期 ID

### 4. 可观测性
- `/health` 健康检查端点，返回队列大小、处理统计等信息
- 结构化 JSON 日志，每个事件带有唯一 `event_id` 便于追踪
- 日志按天滚动，默认保留 30 天

### 5. 灵活配置
支持三种配置方式（优先级从高到低）：
1. CLI 命令行参数
2. 环境变量
3. YAML 配置文件

默认监听端口：`8000`

---

## 🚀 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 生成 Webhook 密钥
```bash
openssl rand -hex 32
```

### 3. 启动服务
```bash
python main.py webhook start --secret "your-secret-here" --port 8000
```

### 4. 配置 GitHub Webhook
- **Payload URL**: `https://your-domain.com/webhook/github`
- **Content type**: `application/json`
- **Secret**: 刚才生成的密钥
- **选择事件**: Workflow runs、Pull requests、Check runs

---

## 🧪 验证服务

### 检查健康状态
```bash
curl http://localhost:8000/health
```

### 预期输出
```json
{
  "status": "ok",
  "service": "github-webhook",
  "queue_size": 0,
  "published_count": 0
}
```

---

## 📁 项目结构

```
src/
├── bus/                    # 事件总线
│   ├── event_bus.py        # 异步事件总线实现
│   └── schemas.py          # 事件数据模型
├── monitor/                # 监控器
│   ├── base.py             # 监控器基类
│   └── webhook_server.py   # GitHub Webhook 服务
├── config/                 # 配置管理
│   └── settings.py         # 配置加载逻辑
├── utils/                  # 工具类
│   └── logging.py          # 结构化日志系统
└── cli/                    # CLI 命令
    ├── app.py              # 主入口
    └── commands/
        └── webhook.py      # Webhook 相关命令
```

---

## ✅ 测试结果

| 类型 | 结果 |
|------|------|
| 单元测试 | 16 个测试全部通过 |
| 功能测试 | 完整的端到端流程验证通过 |
| 代码质量 | 符合 Python 最佳实践 |

---

## 📚 文档

- **设计文档**：`docs/superpowers/specs/2026-04-24-github-webhook-design.md`
- **部署指南**：`docs/github-webhook-setup.md`
- **配置示例**：`config/agent-config.yaml.example`