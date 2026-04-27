本页提供SpiderClaw所有公开REST接口的详细参考文档，面向需要对接系统能力的中间层开发者，包含接口定义、请求/响应格式、签名验证规则和错误码说明。当前版本仅包含GitHub Webhook接收和服务健康检查两类接口。
Sources: [webhook_server.py](src/monitor/webhook_server.py#L1-L399)

## 接口概览
所有接口默认运行在 `http://<部署地址>:8000` 基础路径下，可通过启动参数修改监听端口。

| 请求方法 | 路径 | 功能描述 | 认证方式 |
| --- | --- | --- | --- |
| GET | `/health` | 服务健康状态查询 | 无 |
| POST | `/webhook/github` | 接收GitHub事件通知 | HMAC SHA256签名验证 |
Sources: [webhook_server.py](src/monitor/webhook_server.py#L98-L110)

## 健康检查接口
### 功能说明
用于监控服务运行状态、事件总线统计数据和启动时长，可对接K8s等容器编排系统的存活/就绪探针。
### 请求参数
无查询参数或请求体
### 响应格式
```json
{
  "status": "ok",
  "service": "github-webhook",
  "start_time": "2024-05-20T14:30:00.123456",
  "pending_events": 0,
  "processed_events": 12,
  "failed_events": 0
}
```
### 状态码
- `200 OK`：服务运行正常
Sources: [webhook_server.py](src/monitor/webhook_server.py#L98-L107)

## GitHub Webhook 接收接口
### 功能说明
接收GitHub平台触发的事件，验证合法性后投递到内部事件总线，触发自动修复流程。对接配置请参考[GitHub Webhook Configuration](6-github-webhook-configuration)。
### 请求头要求
| 头字段 | 必填 | 说明 |
| --- | --- | --- |
| `X-GitHub-Delivery` | 是 | GitHub生成的唯一事件ID |
| `X-GitHub-Event` | 是 | 事件类型，当前支持 `workflow_run`、`pull_request`、`check_run` |
| `X-Hub-Signature-256` | 是 | 事件签名，格式为 `sha256=<签名值>` |
Sources: [webhook_server.py](src/monitor/webhook_server.py#L113-L119)

### 签名验证规则
1. 使用配置的Webhook Secret作为密钥，以SHA256算法对请求原始Body进行HMAC加密
2. 将加密结果与请求头`X-Hub-Signature-256`中的值进行对比，不一致则拒绝请求
3. 签名对比使用抗时序攻击的`hmac.compare_digest`方法实现
Sources: [webhook_server.py](src/monitor/webhook_server.py#L186-L205)

### 事件过滤规则
并非所有符合签名要求的事件都会被处理，系统会自动过滤以下场景：
| 事件类型 | 触发条件 | 忽略条件 |
| --- | --- | --- |
| `pull_request` | PR新建、代码更新（`opened`/`synchronize`动作） | 其他动作（关闭、重开等） |
| `workflow_run` | CI工作流执行失败 | 执行成功、取消等其他状态 |
| `check_run` | 检查任务执行失败 | 执行成功、取消等其他状态 |
Sources: [webhook_server.py](src/monitor/webhook_server.py#L155-L177)

### 响应格式
#### 事件接受成功
```json
{
  "status": "accepted",
  "event_id": "a1b2c3d4e5f6g7h8"
}
```
#### 事件被忽略
```json
{
  "status": "ignored",
  "reason": "unsupported event type"
}
```
Sources: [webhook_server.py](src/monitor/webhook_server.py#L124-L184)

## 错误码参考
| HTTP状态码 | 错误描述 | 排查建议 |
| --- | --- | --- |
| 400 | 缺少必填请求头、JSON格式错误或事件解析失败 | 检查GitHub Webhook配置是否正确，Payload格式是否为JSON |
| 403 | 签名验证失败 | 确认Webhook Secret与GitHub配置完全一致 |
| 503 | 事件队列已满，服务繁忙 | 调大事件队列配置参数或扩容服务实例 |
Sources: [webhook_server.py](src/monitor/webhook_server.py#L118-L182)

## 下一步
- 完成Webhook对接配置：[GitHub Webhook Configuration](6-github-webhook-configuration)
- 查看生产部署最佳实践：[Production Deployment Guide](23-production-deployment-guide)