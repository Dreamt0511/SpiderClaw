# AutoFix Agent MVP 实施方案

## 一、MVP 定位与目标

### 1.1 核心目标
实现**三个演示场景**的端到端全流程，覆盖从本地开发到线上运维的完整开发周期，展示系统的核心价值：
- 本地开发实时错误捕获与修复
- CI 测试失败自动修复
- 线上服务零侵入错误修复

### 1.2 价值主张
"常规监控只能告诉您哪里炸了，我的 Agent 系统会顺着调用链向上追溯，定位真正的根因，自动生成修复代码并提交 PR。"

---

## 二、功能范围定义

### ✅ MVP 必做功能
| 模块 | 功能点 | 说明 |
|------|--------|------|
| **监控层** | 进程 stdout/stderr 捕获 | Agent 启动 Web 服务，实时监听输出 |
| | Webhook 服务 | 两个端点：<br>- `/webhook/log`：接收生产日志<br>- `/webhook/ci`：接收 GitHub Actions CI 事件 |
| | 基础过滤引擎 | 正则匹配 Traceback/Error/Exception |
| **事件总线** | 事件 Schema 定义 | 标准化事件结构 |
| | 内存队列实现 | asyncio.Queue |
| | 简单去重 | Traceback hash + 5分钟TTL |
| **Agent层** | 主 Agent 调度 | 协调修复/审查/测试流程 |
| | 修复 Agent | Traceback 解析 + 根因分析 + 修复代码生成 |
| | 审查 Agent | 危险操作检测 + 变更范围限制（≤20行） |
| | 测试 Agent | pytest 运行封装 + 简单回归检测 |
| **工具层** | 文件读取工具 | 读取源代码文件 |
| | Git 操作 | 分支创建 + 代码提交 + PR 生成 |
| **CLI** | 基础命令 | `agentctl watch --process` + `agentctl watch --webhook` |
| **通知** | 飞书卡片通知 | 修复完成后推送结果 |

### ❌ MVP 暂不实现（后续版本）
- Docker 日志监听
- 分布式追踪（Jaeger/OpenTelemetry）
- 跨服务错误诊断
- 语义过滤层
- Docker 沙箱隔离
- TUI 实时面板（只用简单 Rich 输出）
- 复杂分级触发策略（只保留 P0 立即触发）

---

## 三、技术架构（MVP 简化版）

```
┌──────────────────────────────────────────────────────┐
│                   监控器层                            │
│  ┌────────────────────┐  ┌────────────────────────┐  │
│  │ 进程捕获           │  │ FastAPI Webhook        │  │
│  │ (asyncio.subprocess)│  │ /webhook/log          │  │
│  │                    │  │ /webhook/ci           │  │
│  └──────────┬─────────┘  └──────────┬─────────────┘  │
└─────────────┼────────────────────────┼────────────────┘
              ▼                        ▼
┌──────────────────────────────────────────────────────┐
│                   事件总线层                          │
│                asyncio.Queue + 去重                   │
└──────────────────────────┬───────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────┐
│                   Agent 层                           │
│  主 Agent → 修复 Agent → 审查 Agent → 测试 Agent      │
└──────────────────────────┬───────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────┐
│                   输出层                              │
│          Git 提交 PR  +  飞书通知                      │
└──────────────────────────────────────────────────────┘
```

---

## 四、监控源详细设计

### 4.1 三个核心监控源

| 监控源 | 数据链路 | 适用场景 | 实现方式 |
|--------|----------|----------|----------|
| **进程捕获（本地）** | Agent 启动 Web 服务 → 监听 stdout/stderr | 本地开发实时捕获 | `asyncio.subprocess` 启动子进程，逐行解析输出 |
| **Webhook（CI事件）** | GitHub Actions → POST → Agent | CI 测试失败自动修复 | FastAPI 端点 `/webhook/ci`，解析 GitHub Actions payload |
| **Webhook（远程日志）** | 生产采集器 → POST → Agent | 线上服务零侵入修复 | FastAPI 端点 `/webhook/log`，接收日志行 |

### 4.2 Webhook 接口设计

```python
# POST /webhook/log （生产日志上报）
请求体：
{
  "log": "Traceback (most recent call last): ...",
  "service": "order-service",
  "timestamp": "2024-01-01T12:00:00Z"
}

# POST /webhook/ci （CI 事件上报）
请求体：（GitHub Actions 标准 payload）
{
  "action": "completed",
  "workflow_run": {
    "conclusion": "failure",
    "head_branch": "feature/xxx",
    "head_sha": "a1b2c3d"
  },
  "repository": {
    "full_name": "myteam/order-service",
    "clone_url": "https://github.com/myteam/order-service.git"
  }
}
```

### 4.3 进程捕获实现逻辑
```python
# 伪代码
async def watch_process(command: str):
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    async for line in proc.stderr:
        line = line.decode('utf-8')
        if 'Traceback' in line or 'ERROR' in line:
            # 推送到事件总线
            await event_bus.push({
                'type': 'process_error',
                'content': line,
                'source': command
            })
```

---

## 五、演示场景设计（完整故事线）

### 场景一：本地开发实时修复
**流程**：
1. 开发者执行：`agentctl watch --process "python examples/demo_app.py"`
2. Agent 启动 demo 服务，监听 stdout/stderr
3. 开发者故意在代码中写一个 Bug（如 ZeroDivisionError）
4. 访问服务触发错误，stderr 输出 Traceback
5. Agent 实时捕获错误，解析 Traceback
6. 修复 Agent 分析根因，生成修复代码
7. 审查 Agent 检查安全，测试 Agent 验证修复
8. Agent 自动提交 PR 到本地仓库
9. 飞书推送修复通知

**演示亮点**：零配置，实时捕获，开发效率提升

---

### 场景二：CI 测试失败自动修复
**流程**：
1. 开发者提交有 Bug 的代码到 GitHub
2. GitHub Actions 自动运行测试，测试失败
3. CI 通过 Webhook 推送失败事件到 Agent
4. Agent 拉取对应仓库的代码，解析测试失败日志
5. 修复 Agent 定位问题，生成修复代码
6. 自动提交修复到当前 PR
7. CI 重新运行测试，测试通过
8. 飞书通知开发者修复完成

**演示亮点**：CI 流程自动化，减少人工 fix 成本

---

### 场景三：线上服务零侵入修复
**流程**：
1. 生产服务运行中，出现错误并写入日志
2. 轻量采集器（简单 bash 脚本：`tail -F app.log | curl -X POST -d @- http://agent/webhook/log`）转发日志到 Agent
3. Agent 接收日志，解析 Traceback
4. 通过服务注册表找到对应代码仓库
5. 修复 Agent 分析根因，生成修复代码
6. 提交 PR 到代码仓库，通知负责人审核
7. 飞书推送修复通知，包含 diff 和 PR 链接

**演示亮点**：零侵入，不需要修改现有服务代码，直接部署使用

---

## 六、开发计划与时间安排

基于 1 人全职开发，**总周期 12 天**：

| 周期 | 模块 | 任务 | 里程碑 |
|------|------|------|--------|
| **Day 1** | 项目初始化 | 目录结构搭建 + 依赖安装 + 基础配置 | 项目可运行 |
| **Day 2-3** | 监控层 + 事件总线 | 1. FastAPI Webhook 服务（两个端点）<br>2. 进程捕获实现<br>3. 事件 Schema + 内存队列 + 去重 | 事件能从三个源流入系统 |
| **Day 4-6** | Agent 核心 | 1. LangGraph 状态图定义<br>2. 主 Agent 调度逻辑<br>3. 修复 Agent 实现（Traceback 解析 + 提示词 + 修复生成） | 能独立生成修复 patch |
| **Day 7** | 审查 + 测试 Agent | 1. 审查 Agent（危险模式检测 + 变更范围检查）<br>2. 测试 Agent（pytest 运行封装） | 能过滤不安全/无效修复 |
| **Day 8** | 工具层 | 1. 文件读取工具<br>2. Git 操作封装（分支 + 提交 + PR） | 能提交 PR 到 GitHub |
| **Day 9** | CLI + 通知 | 1. Typer 基础命令（`watch --process`/`watch --webhook`）<br>2. 飞书通知实现 | 能通过 CLI 启动系统 |
| **Day 10-11** | 集成调试 | 1. 端到端流程串联<br>2. 异常处理完善<br>3. Demo 服务准备 | 三个场景都能跑通 |
| **Day 12** | 演示优化 | 1. 演示脚本编写<br>2. 文档完善 | 可用于比赛演示 |

---

## 七、核心技术栈与依赖

```txt
# 核心依赖
langgraph>=1.1,<2.0          # Agent 编排
langchain-openai>=1.1,<2.0   # LLM 调用
fastapi>=0.135,<1.0          # Webhook 服务
uvicorn>=0.42,<1.0           # ASGI 服务器
typer>=0.15,<1.0             # CLI
rich>=13.0,<14.0             # 美化输出
gitpython>=3.1,<4.0          # Git 操作
pydantic>=2.12,<3.0          # 数据验证
pyyaml>=6.0,<7.0             # 配置文件
pytest>=8.0,<9.0             # 测试运行
```

---

## 八、MVP 验收标准

### 功能验收
1. ✅ 三个监控源都能正常接收事件
2. ✅ 本地进程捕获场景端到端跑通
3. ✅ CI Webhook 场景端到端跑通
4. ✅ 远程日志 Webhook 场景端到端跑通
5. ✅ 修复代码能正确提交 PR
6. ✅ 飞书通知能正常推送

### 非功能验收
1. 单次修复总耗时 ≤ 2 分钟（从事件接收到 PR 提交）
2. 修复准确率 ≥ 80%（针对简单 Python 语法/逻辑错误）
3. 没有危险代码提交（如 rm -rf、DROP TABLE 等）

---

## 九、风险与应对

| 风险 | 应对方案 |
|------|----------|
| LangGraph 学习成本高 | 先实现简单的线性流程，不需要复杂的状态机 |
| LLM 修复效果不稳定 | 限制修复场景为简单的 Python 错误，提供详细的示例提示词 |
| GitHub API 调用复杂 | 先使用本地 Git 提交，不需要真正推送 GitHub，演示时模拟即可 |
| 飞书通知配置麻烦 | MVP 阶段可以先用控制台输出替代，最后再集成 |

## 补充说明
github webhook 最终勾选的三种事件
事件名	          对应场景	              说明
Workflow runs	  CI测试失败  CI流水线结束时触发
Pull requests	  PR事件      知道PR编号和分支
Check runs	  CI中单个检查	更细粒度，知道具体哪个检查挂了

默认以spiderclaw进入cli模式，其他cli命令遵循以spiderclaw开头的命令
例如：`spiderclaw watch --process`

agent开发过程中请使用langchain或者Langgraph构架，已安装了相关skill，请遵循skill的使用规范