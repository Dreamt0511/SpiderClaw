本页面向初学者开发者，指导您在5分钟内完成SpiderClaw自动修复系统的首次本地演示，无需配置GitHub Webhook或飞书通知即可体验核心功能。
Sources: [pyproject.toml](pyproject.toml#L1-L35)

## 前置依赖确认
运行本地演示前请确保您的环境满足以下要求：
| 依赖项 | 版本要求 | 验证命令 |
|--------|----------|----------|
| Python | ≥3.10 | `python --version` |
| Git | 任意最新版本 | `git --version` |
| LLM API密钥 | OpenAI兼容服务的API密钥（如OpenAI、Azure OpenAI、本地部署的兼容接口） | - |

## 快速启动流程
```mermaid
flowchart LR
    A[克隆项目到本地] --> B[安装依赖包]
    B --> C[复制并修改配置文件]
    C --> D[运行本地修复演示脚本]
    D --> E[查看自动修复结果]
```

### 步骤1：克隆项目并安装依赖
首先将项目克隆到本地，然后进入项目目录安装依赖：
```bash
git clone <项目仓库地址>
cd SpiderClaw
pip install -r requirements.txt
```
Sources: [requirements.txt](requirements.txt)

### 步骤2：基础配置
复制示例配置文件并填写您的LLM接口信息：
```bash
copy .env.example .env
copy config/agent-config.example.yaml config/agent-config.yaml
```
打开 `config/agent-config.yaml` 填写您的OpenAI API Key、Base URL和模型名称即可完成最小配置，完整配置说明可参考 [Basic Configuration](4-basic-configuration) 页面。
Sources: [.env.example](.env.example), [agent-config.example.yaml](config/agent-config.example.yaml)

### 步骤3：运行本地修复演示
我们提供了预设的语法错误、运行时错误测试用例，您可以直接运行本地测试脚本体验完整的自动修复流程：
```bash
python local_test/test_fix_flow.py
```
脚本会自动完成以下操作：
1. 创建临时Git测试仓库
2. 运行错误示例文件获取错误日志
3. 自动解析错误位置
4. 调用修复Agent生成修复代码
5. 调用审查Agent验证修复安全性
6. 运行测试验证修复有效性
Sources: [test_fix_flow.py](local_test/test_fix_flow.py#L1-L200)

### 步骤4：查看修复结果
脚本运行过程中会实时输出各环节进度，您可以看到：
- 解析到的错误类型和位置
- 修复Agent生成的代码变更
- 审查Agent给出的审查意见和风险提示
- 最终修复是否通过测试

本地测试支持的错误类型如下，完整支持列表可参考 [Supported Error Types Reference](5-supported-error-types-reference)：
| 错误类型 | 测试文件路径 |
|----------|--------------|
| Python语法错误 | local_test/test_syntax_error_1.py ~ test_syntax_error_3.py |
| Python运行时错误 | local_test/test_runtime_error_1.py ~ test_runtime_error_2.py |

## 后续步骤
完成本地演示后，您可以按照以下顺序继续探索SpiderClaw的完整功能：
1. 完成生产环境安装：[Installation Guide](3-installation-guide)
2. 配置GitHub Webhook实现CI错误自动触发修复：[GitHub Webhook Configuration](6-github-webhook-configuration)
3. 配置飞书通知接收修复进度和结果：[Feishu/Lark Notification Setup](7-feishu-lark-notification-setup)