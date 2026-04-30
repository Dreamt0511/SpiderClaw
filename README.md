# SpiderClaw - 事件驱动的自动诊断与修复系统

> **语言声明：本文档及本项目所有文档、注释、提交信息均使用中文。所有生成的 Wiki 文档必须使用中文输出。**

## 🎯 项目定位
SpiderClaw是一个事件驱动的自动诊断与修复系统，能够自动捕获异常事件，通过大模型分析根因，生成修复代码，提交PR并通知开发者。

## ✨ 核心能力
- 🚀 **事件驱动监控**：支持文件监听、Webhook、Docker日志等多种事件源
- 🔍 **智能根因分析**：通过Traceback调用链逆向追踪，准确定位问题根因
- 🔧 **跨模块修复**：支持多文件协同修复，解决复杂代码问题
- 🤝 **多智能体协作**：主Agent + 修复/审查/测试SubAgent的多层级架构
- 🛡️ **安全护栏**：内置危险检测、变更限制、测试门禁，确保修复安全
- 📊 **实时仪表盘**：监控系统状态、事件处理进度、修复统计等
- 📝 **飞书多维表格集成**：自动上报所有修复记录，支持数据化运营和趋势分析
- 📢 **飞书通知**：实时推送修复结果到飞书，实现开发流程闭环

## 🎬 效果展示
> 4月30日实现效果
> ![alt text](assets/image.png)

## 🚀 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
pip install -e .  # 开发模式安装
```

#### 飞书CLI安装（可选，用于飞书通知和多维表格上报）
如果需要使用飞书通知或多维表格上报功能，需要通过npm安装lark-cli：

**前置要求**：已安装Node.js和npm

```bash
# 安装最新版本
npm install -g @larksuite/cli@latest

# 验证安装
lark-cli --version
```

安装完成后进行配置：
```bash
lark-cli login
```
按照提示完成飞书应用授权即可。

> 注意：lark-cli是飞书官方提供的命令行工具，通过npm分发，不支持pip安装。

### 2. 配置文件
复制配置示例文件并修改：
```bash
cp src/config/agent-config.example.yaml src/config/agent-config.yaml
```

编辑 `src/config/agent-config.yaml`，配置必要的参数：
- GitHub Token
- OpenAI API 密钥
- 飞书配置（可选）
- 多维表格配置（可选）

### 3. 启动服务
```bash
# 启动Webhook服务
spiderclaw webhook start --secret <your-webhook-secret> --port 8000

# 或直接运行
python main.py webhook start --secret <your-webhook-secret> --port 8000

#简化CLI命令(默认启动Webhook服务)
spiderclaw
```


## Webhook 调试

使用 [ngrok](https://ngrok.com/) 将本地服务暴露到公网，便于接收 GitHub Webhook。

```bash
# 1. 启动本地服务后，另开终端运行
ngrok http 8000

# 2. 将生成的公网地址（如 https://xxxx.ngrok-free.app）配置到 GitHub 仓库的 Webhook 设置中
# 完整 URL 示例：https://xxxx.ngrok-free.app/webhook/github

# 3. 在浏览器中打开以下地址，查看所有请求的完整数据
# http://127.0.0.1:4040/inspect/http
```

## 📊 飞书多维表格配置

### 功能说明
系统支持自动上报所有修复事件的详细数据到飞书多维表格，实现：
- 修复数据的永久沉淀
- 错误趋势分析
- 修复效果评估
- 系统性能监控
- 团队效率统计

### 配置步骤
1. 在飞书中创建一个新的多维表格，或者让系统自动创建
2. 在配置文件中添加以下配置：
```yaml
lark:
  # 基础飞书配置
  enabled: true
  app_id: "cli_xxxxxx"
  app_secret: "your-app-secret"
  notify_users: ["ou_xxxxxx"]
  
  # 多维表格配置
  base_enabled: true
  base_token: "bascnxxxxxx"  # 从多维表格URL中获取
  repair_table_id: "tblxxxxxx"  # 可选，为空时系统自动创建表结构
```

3. 启动服务后，系统会自动在多维表格中创建"修复记录"数据表，并包含以下字段：
   - 修复ID、修复时间、错误类型
   - 仓库名称、分支名称、PR作者
   - 原PR链接、修复PR链接
   - 修复状态、修复描述、错误信息
   - 修复文件数、变更行数、修复耗时
   - 重试次数、Token消耗、运行环境

### 数据看板
利用飞书多维表格的原生仪表盘功能，可以搭建自定义的数据看板，展示：
- 每日修复量趋势
- 错误类型分布
- 修复成功率
- 平均修复时长
- Token消耗统计

## 🔧 技术栈
| 层级       | 技术选型                | 说明                     |
| ---------- | ----------------------- | ------------------------ |
| Agent 框架 | LangGraph               | 状态机编排，多智能体调度 |
| LLM        | OpenAI / Claude         | 函数调用能力             |
| CLI 界面   | Typer + Rich            | 命令行入口               |
| Webhook    | FastAPI + uvicorn       | 接收CI/GitHub事件        |
| 事件总线   | asyncio.Queue           | 异步消息队列             |
| 代码操作   | GitPython               | Git操作                  |
| 通知       | 飞书开放API             | 审批、消息通知           |
| 仪表盘     | Textual                 | 实时监控界面             |
| 数据存储   | 飞书多维表格            | 修复数据上报与统计       |

## 📚 开发规范
- 所有Agent/工具必须严格遵循LangChain/LangGraph官方规范
- 工具使用`@tool`装饰器，禁止自定义工具类
- Agent使用`create_agent()`创建，禁止手动实现循环
- 代码必须有类型注解，异步函数使用`async/await`
- Prompt模板统一放在`src/agent/prompts/`目录

## 📄 许可证
MIT License