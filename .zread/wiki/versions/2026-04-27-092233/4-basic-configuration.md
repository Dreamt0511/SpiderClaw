本页面介绍SpiderClaw的基础配置方法、加载逻辑和核心参数说明，是完成安装后必须进行的配置步骤，所有配置均有默认值，仅需修改必填项即可快速启动服务。

## 配置加载机制
SpiderClaw支持**YAML配置文件**和**环境变量**两种配置方式，配置优先级从高到低为：运行时参数覆盖 > 环境变量 > YAML配置文件 > 系统默认值。
配置加载流程如下：
```mermaid
flowchart LR
    A[启动服务] --> B[加载config/agent-config.yaml配置文件]
    B --> C[加载.env环境变量覆盖配置]
    C --> D[应用运行时传入的覆盖参数]
    D --> E[验证配置合法性]
    E --> F[生成全局配置实例]
```
系统使用Pydantic实现配置的自动类型校验和默认值填充，非法配置会在启动阶段抛出明确的错误提示，避免运行时异常。
Sources: [settings.py](src/config/settings.py#L1-L170)

## 配置文件初始化
首次使用时请按照以下步骤初始化配置文件：
1. 复制项目根目录下的`.env.example`文件为`.env`，该文件用于配置环境变量，适合容器化部署场景使用
2. 复制`config/agent-config.example.yaml`文件为`config/agent-config.yaml`，该文件为推荐的主配置文件，适合大多数部署场景使用
> 两种配置方式二选一即可，YAML配置文件的结构更清晰，推荐普通用户使用；环境变量适合CI/CD、容器部署等需要动态注入配置的场景。
Sources: [.env.example](.env.example#L1-L28), [agent-config.example.yaml](config/agent-config.example.yaml#L1-L48)

## 核心配置参数说明
下表列出所有核心配置参数的说明：
| 配置组 | 参数名 | 必填 | 默认值 | 说明 |
|--------|--------|------|--------|------|
| 通用配置 | environment | 否 | development | 运行环境，可选值：development/production |
| 通用配置 | debug | 否 | false | 是否开启调试模式，开启后会输出更详细的日志 |
| Webhook配置 | secret | 是 | 无 | GitHub Webhook的签名密钥，与GitHub Webhook后台配置保持一致 |
| Webhook配置 | host | 否 | 0.0.0.0 | Webhook服务监听地址 |
| Webhook配置 | port | 否 | 8000 | Webhook服务监听端口 |
| Webhook配置 | allowed_events | 否 | ["workflow_run", "pull_request"] | 允许处理的GitHub事件类型 |
| Agent配置 | enabled | 否 | true | 是否启用自动修复功能，关闭后仅接收事件不执行修复 |
| Agent配置 | max_retries | 否 | 3 | 单错误最大修复重试次数 |
| Agent配置 | max_change_lines | 否 | 20 | 单次修复最大允许变更的代码行数，避免大的非预期修改 |
| Agent配置 | auto_create_pr | 否 | true | 修复完成后是否自动创建PR |
| Agent配置 | require_human_approval | 否 | false | 创建PR前是否需要人工审批确认 |
| GitHub配置 | token | 是 | 无 | GitHub个人访问令牌，需要repo权限 |
| OpenAI配置 | api_key | 是 | 无 | OpenAI API密钥，也支持兼容OpenAI协议的其他大模型服务 |
| OpenAI配置 | base_url | 否 | https://api.openai.com/v1 | API基础地址，可修改为其他兼容服务的地址 |
| OpenAI配置 | model_name | 否 | gpt-4o | 使用的大模型名称，支持gpt-4o、gpt-3.5-turbo、claude-3系列等 |
| 日志配置 | level | 否 | INFO | 日志级别，可选值：DEBUG/INFO/WARNING/ERROR |
| 日志配置 | retention_days | 否 | 30 | 日志文件保留天数 |
Sources: [settings.py](src/config/settings.py#L15-L120)

## 配置验证
配置修改完成后，启动服务时系统会自动验证所有必填参数是否完整，若存在缺失会抛出明确的错误提示，例如缺少GitHub Token时会提示对应配置项为空。你也可以通过运行本地测试脚本快速验证配置有效性：
```bash
python local_test/test_fix_flow.py
```
若运行无报错则代表基础配置正确。
Sources: [test_fix_flow.py](local_test/test_fix_flow.py#L1)

## 后续步骤
基础配置完成后，你可以根据需要进行集成配置：
- 如需对接GitHub事件自动触发修复，请参考 [GitHub Webhook Configuration](6-github-webhook-configuration)
- 如需接收飞书/ Lark 通知提醒，请参考 [Feishu/Lark Notification Setup](7-feishu-lark-notification-setup)
- 配置完成后可前往 [Quick Start](2-quick-start) 启动服务体验自动修复功能