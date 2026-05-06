# SpiderClaw — 事件驱动的自动诊断与修复系统

> 基于大模型的代码自动修复平台，深度对接飞书生态，实现代码错误"发现即修复"

---

## 项目概述

SpiderClaw 是面向研发团队的 AI 原生自动化运维平台，通过事件驱动架构实时捕获代码异常，结合大语言模型的代码理解能力实现全自动根因定位和修复，构建"异常检测 → 根因分析 → 代码修复 → 审查测试 → PR 提交 → 通知归档"的完整闭环。

**当前支持**：Python 项目全场景自动修复，覆盖 SyntaxError、ImportError、AttributeError、TypeError、NameError 等常见异常类型。其他语言（Java/Go/JavaScript 等）可通过扩展代码解析器和 Prompt 模板接入。

**双场景覆盖**：
- **开发阶段**：接收 GitHub Webhook 事件（workflow_run / pull_request），监听到 CI 失败后自动拉取日志、定位根因、生成修复 PR、发送通知飞书
- **生产阶段**：通过 Sidecar 脚本零侵入部署到业务服务器，实时采集 Python 异常日志并上报到修复系统，拉取对应版本的源码仓库进行针对性修复、生成修复 PR、发送通知飞书

---

## 维度 1：完整性与价值

### 1.1 解决的核心痛点

- **CI 反复失败**：开发者提交 PR → CI 报错 → 人工查看日志 → 定位问题 → 修改代码 → 重新提交。一个简单的拼写错误可能需要 2-3 轮迭代才能通过 CI
- **线上故障响应**：生产环境抛出异常后，需要人工发现 → 拉取日志 → 定位根因 → 修复 → 发布，整个链路依赖资深工程师全程参与
- **重复错误浪费**：同类错误在不同 PR 中反复出现，缺乏自动化的修复经验沉淀机制
- **流程断点**：从错误发现到修复 PR 合入，中间多个环节需要人工衔接

### 1.2 AI 的关键作用

SpiderClaw 中 AI 承担了传统人工排障的核心环节：

**根因定位**：从 Python Traceback 逆向追踪调用链，区分框架代码（site-packages/、lib/python 等）与应用代码，定位真正的根因文件和行号，而非止步于异常抛出处

**代码修复**：多 Agent 协作的三级质量管控：
```
collect_context → fix_agent → validation_gate → review_changes → run_tests → create_pr
                     ↑              ↑                ↑               ↑
                  重试(强制指令)  重试(门禁拦截)   重试(审查拒绝)   重试(测试失败)
```

- **FixAgent**：分析 CI 日志和错误位置，生成最小化修复代码，支持跨文件根因修复
- **ReviewAgent**：两阶段审查 — Phase 1 由 LLM 评估修复正确性和安全性，Phase 2 对发现的安全问题自动修复（如不安全的 subprocess 调用）
- **TestAgent**：代码级验证（ast.parse 语法检查 + pytest 测试执行 + import 可用性检查），不依赖 LLM 调用

**智能去重**：基于 Traceback 指纹（应用文件名 + 异常类型 + 错误消息 → MD5 前 12 位 hex），相同错误自动识别，避免重复修复浪费 Token

### 1.3 完整闭环流程

```
事件源层              事件总线层            Agent 编排层          输出层             通知/数据层
┌──────────┐      ┌──────────────┐     ┌──────────────┐      ┌──────────┐      ┌──────────────┐
│GitHub    │────▶│              │     │ 主Orchestrator│     │          │      │ 飞书消息卡片  │
│Webhook   │      │ 过滤/去重    │────▶│              │────▶│ 修复PR   │────▶│ 飞书多维表格  │
│(CI失败)  │      │ 持久化落盘    │     │ 7节点状态图   │      │          │     │ 飞书审批流程  │
├──────────┤      │              │     ├──────────────┤      ├──────────┤      ├──────────────┤ 
│Sidecar   │────▶│ EventBus     │     │ FixAgent     │       │          │      │ 实时仪表盘    │
│采集脚本   │     │ asyncio.Queue│     │ ReviewAgent  │────▶  │ 修复PR   │────▶│ (Textual TUI) │
│(线上日志) │     │              │     │ TestAgent    │       │          │      │              │
└──────────┘     └──────────────┘     └──────────────┘       └──────────┘      └──────────────┘
```

**端到端自动化能力**：
- GitHub CI 失败 → 自动拉取 CI 日志 → 解析 Python 错误 → 克隆仓库 → Agent 修复 → 审查测试 → 创建修复 PR → 飞书通知
- 生产日志异常 → Sidecar 自动采集上报 → 定位对应服务源码仓库 → Agent 修复 → 审查测试 → 创建修复 PR → 飞书通知
- 所有修复记录自动归档到飞书多维表格，形成可检索的修复历史

### 1.4 可落地性

**双启动模式**：
- `spiderclaw`：默认模式，后台 Webhook 服务 + 前台 Textual TUI 仪表盘，适合开发调试和桌面运维
- `spiderclaw --no-dashboard`：无头模式，纯控制台日志输出，适合容器/服务器环境部署

**安全护栏**（见维度 3 详细说明）：
- 5 重后置验证门禁（导入边界 / 语法正确 / 文件完整性 / 错误覆盖 / 变更行数限制）
- 仅允许修改 `src/` 目录
- 危险操作检测（DROP TABLE / rm -rf / eval / exec / subprocess shell=True 等）
- 单次修复最大修改行数可配置（默认 50 行）

**持久化防丢**（见"持久化与重试机制"章节详细说明）：
- 事件接收即落盘 SQLite，服务崩溃不丢失
- 推送失败自动保存，后台定时重试
- 处理中标记 + 重启重置，避免僵尸事件

---

## 维度 2：创新性

### 2.1 AI 亮点

#### 2.1.1 高阶 AI 技巧

**强制指令系统注入**：重试时将被拒原因（验证门禁 / 审查 / 测试）结构化为强制指令文本，通过 `system_prompt_override` 注入 System Prompt 最高优先级层，确保 LLM 在下一次尝试中不重复同样的错误。不同拒绝来源有不同的指令生成规则：
- Gate 拒绝：根据 `violation_type`（change_limit_exceeded / file_incomplete / wrong_file_modified / error_uncovered 等）用模板引擎生成针对性指令
- Review 拒绝：直接传递审查 Agent 的结构化反馈原文
- Test 拒绝：传递失败用例列表 + 测试输出

**上下文裁剪策略**：`mandatory_instructions` 字段在多次重试时累积，超过 500 字符后只保留最新 2 条指令（从倒数第二个 `---` 分隔处截取），防止 Prompt 膨胀导致 LLM 注意力衰减。

**Token 预算双控**：重试决策同时考虑两个维度 — 重试次数（默认 3 次）和总 Token 消耗（20000），任一上限耗尽即停止重试，避免在复杂错误上无限消耗 Token。

**错误指纹与状态机去重**：基于 Traceback 内容生成稳定指纹（排除框架路径 → 提取应用文件名 + 异常类型 + 错误消息前 50 字符 → MD5 取前 12 位 hex），配合 7 种生命周期状态（FIXING → PENDING_DEPLOY → DEPLOYED / FAILED → ABANDONED / SUPERSEDED），实现：
- 相同错误正在修复中 → 跳过
- 已有修复 PR 等待部署 → 跳过
- 已部署 → 忽略
- 修复失败 → 指数退避重试（1min → 2min → 4min）
- 失败 >= 3 次 → 放弃（ABANDONED）

**错误分类与智能路由**：系统将错误分为三类，不同类别走不同路径：
- A 类（文件缺失）：如 `FileNotFoundError: requirements.txt`，直接创建空文件，不调用 Agent
- B 类（语法/逻辑错误）：全量 AST 扫描 — 迭代注释出错行 → `ast.parse()` → 发现下一个 SyntaxError，直到文件完全通过语法解析
- C 类（依赖/导入错误）：交由 FixAgent 判断，既可代码修复（添加 import / try-except 包裹），也可判定为环境问题（`is_env_error`）直接结束流程并通知

**跨文件根因定位**：从 Traceback 中的链式错误（如 `ImportError → NameError`）逆向追踪调用链，支持跨模块关联分析，在根因位置生成修复而非表层打补丁。

#### 2.1.2 人与 AI 的分工

| 环节 | AI 负责 | 人负责 |
|------|---------|--------|
| 异常检测 | 自动捕获（Webhook / Sidecar） | — |
| 根因定位 | 从 Traceback 逆向追踪 | — |
| 代码修复 | FixAgent 生成最小化修复 | — |
| 代码审查 | ReviewAgent Phase 1 + Phase 2 安全修复 | 最终 PR Review |
| 测试验证 | TestAgent ast.parse + pytest + import | 复杂业务逻辑确认 |
| PR 创建 | 自动创建分支、提交、推送、创建 PR | Merge 决策 |
| 通知归档 | 飞书卡片 + 多维表格自动上报 | 查看统计 |
| 配置管理 | — | 注册服务、配置版本 |
| 批量恢复 | — | 飞书审批确认 |

核心设计理念：**AI 做到"提交 PR 等人审核"，而非直接合入代码**，人在最终决策节点保持控制权。

#### 2.1.3 模型选型

- 通过 AgentFactory 统一创建 FixAgent / ReviewAgent / TestAgent，`system_prompt_override` 机制注入差异化指令
- 支持 OpenAI（GPT-4o）/ Claude 通过配置文件切换，LLM 参数（api_key / base_url / model）全部配置驱动
- TestAgent 完全基于代码执行（ast.parse + subprocess），不消耗 LLM Token，降低运营成本

#### 2.1.4 AI 对工作流的改变

**开发阶段**：
```
原来：提交 PR → CI 失败 → 人工查日志 → 定位问题 → 修改代码 → 重新提交 → CI 再次运行
现在：提交 PR → CI 失败 → SpiderClaw 自动修复 → 修复 PR 等待 Review → 合并
```

**生产阶段**：
```
原来：线上报错 → 人工发现 → 拉取日志 → 定位 → 修复 → 发布 → 线上生效
现在：线上报错 → Sidecar 自动采集 → SpiderClaw 自动修复 → 修复 PR 等待 Review → 合并部署
```

### 2.2 飞书生态深度融合

SpiderClaw 是目前唯一完整对接飞书全生态（消息通知 + 多维表格 + 审批流程 + WebSocket 长连接 + IM 命令交互）的自动修复平台。

#### 2.2.1 飞书消息通知

系统通过 `lark-cli` 命令行工具发送飞书交互式卡片消息（无需手动处理 API 认证和 Token 管理），覆盖 6 种场景：

| 场景 | 卡片模板 | 颜色 | 核心信息 |
|------|---------|------|---------|
| 修复成功 | 自动修复通知 | 绿色 | 变更行数、分支、版本、修复说明、PR 按钮 |
| 修复失败 | 自动修复通知 | 红色 | 失败原因、原 PR 按钮、数据统计按钮 |
| 跳过重复修复 | 跳过重复修复 | 蓝色 | 错误指纹、已有修复 PR |
| 需配置 | 需要配置 | 橙色 | 服务名、缺失项、配置指引 |
| 上报失败告警 | 上报失败告警 | 红色 | 连续失败次数、base_token、冷却机制 |
| 推送恢复通知 | 推送恢复 | — | 遗留推送重试成功/失败状态 |

通知包含交互式按钮，支持一键跳转修复 PR、原 PR、多维表格数据统计页面。

#### 2.2.2 飞书多维表格

系统自动将每次修复的详细数据上报到飞书多维表格，实现修复数据沉淀和趋势分析。

**表结构（17 个字段）**：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| 修复时间 | 日期时间 | 精确到分钟（yyyy-MM-dd HH:mm） |
| 修复状态 | 单选 | 成功（绿色）/ 失败（红色） |
| 仓库名称 | 单行文本 | 错误所属 GitHub 仓库 |
| 分支名称 | 单行文本 | 错误所在 Git 分支 |
| PR 作者 | 单行文本 | 原 PR 提交者 GitHub 用户名 |
| 原 PR 链接 | 超链接 | 点击跳转原错误 PR |
| 修复 PR 链接 | 超链接 | 点击跳转修复 PR |
| 错误类型 | 单选 | SyntaxError / ImportError / AttributeError / TypeError / ValueError / 其他错误 |
| 修复描述 | 单行文本 | 修复内容摘要（截断至 500 字符） |
| 错误信息（失败时） | 单行文本 | 失败原因详情（截断至 1000 字符） |
| 修复文件数 | 数字 | 本次修复涉及文件数 |
| 变更行数 | 数字 | 功能性代码增删行数（排除空行/注释/docstring/导入行） |
| 修复耗时（秒） | 数字 | 从事件捕获到 PR 创建总耗时 |
| 重试次数 | 数字 | 修复 Agent 尝试次数 |
| Token 消耗 | 数字 | 本次修复 LLM Token 总量 |
| 相关文件名 | 单行文本 | 修复文件列表（每行一个） |
| 环境 | 单选 | 开发（青色）/ 测试（蓝色）/ 生产（紫色） |

**自动运维特性**：
- 表不存在时自动创建（`+table-create`）
- 字段缺失时自动补全（`+field-create`，与预定义字段定义对比差异）
- 日期时间字段格式自动修正（`+field-update` 设置 `yyyy-MM-dd HH:mm`）
- 上报失败自动重试（最多 3 次，间隔 1s）
- 连续失败 >= 3 次触发告警通知（10 分钟冷却期防抖）
- base_token 无权限时自动创建新多维表格并持久化到配置文件

#### 2.2.3 飞书审批流程

**触发场景**：系统服务重启后，检测到 SQLite 中待处理事件数量超过阈值（默认 5 个，由 `pending_event_auto_threshold` 配置），自动发起飞书审批。

**完整流程**：
1. 服务启动 → `recover_pending_events()` 扫描 PendingEventStore
2. 统计待处理事件数，<= 阈值直接恢复处理，> 阈值进入审批流程
3. 等待飞书 WebSocket 长连接就绪（`approval_ws_ready` 事件）
4. 自动确保审批定义存在（初次使用时通过 `/open-apis/approval/v4/approvals` 创建，审批定义编码保存到 `data/approval_config.json`）
5. 订阅审批事件（`/open-apis/approval/v4/approvals/{code}/subscribe`，幂等）
6. 创建审批实例，列出前 10 个待处理事件摘要（服务名 + 事件类型 + 时间）
7. 管理员在飞书审批中心收到审批 → **通过**：恢复所有待处理事件到 EventBus → **拒绝/取消**：删除所有待处理记录
8. 审批结果通过 SDK WebSocket 长连接实时回调处理

**技术实现**：
- 通过 `@larksuite/oapi` SDK 的 WebSocket 客户端建立长连接（独立线程 `lark-ws`）
- 注册 `approval_instance` 事件处理器，监听审批状态变更
- 事件通过 `asyncio.Queue` 线程安全地传递到主事件循环
- WebSocket 断线自动重连（`auto_reconnect=True`，重连后自动恢复事件处理器）
- 审批记录持久化到 `PendingApprovalStore`（SQLite），支持跨重启跟踪

#### 2.2.4 IM 命令交互（/status）

用户可在飞书中向 SpiderClaw 机器人发送 `/status` 命令，系统实时生成状态报告卡片并回复。

**实现机制**：
- 通过飞书 SDK WebSocket 注册 `im.message.receive_v1` 事件处理器
- 解析消息内容，匹配 `/status` 命令
- 权限校验：仅 `notify_users` 列表中的用户有权查询，其他用户收到"权限不足"提示
- 生成状态卡片包含：运行时长、Agent 状态、Token/LLM/工具调用统计、成功/失败修复数、待处理事件数、待推送记录数
- 通过 `send_markdown_message` 以交互式卡片形式回复

### 2.3 Sidecar 零侵入部署

生产环境无需修改业务代码，只需部署一个 Shell 脚本（`collector.sh`）+ 配置文件（`agent-mapping.conf`），支持两种模式：

**日志监控模式**（`nohup bash collector.sh &`）：
- 后台 tail -F 监控日志文件，实时匹配 Python 异常关键词和 Traceback 格式
- 去重窗口（默认 300s）+ 批量发送（每 200 行或 10s 间隔）
- 指数退避重试（1s → 2s → ... → 300s）

**命令执行模式**（`bash collector.sh exec <command>`）：
- 运行命令同时捕获 stdout/stderr 到日志文件 + 终端显示
- 退出码非 0 或检测到异常关键词时自动上报
- 适用于 CI 流水线或定时任务场景

通过 `spiderclaw init-sidecar` 命令一键生成采集脚本和配置模板。

### 2.4 差异化亮点总结

| 维度 | SpiderClaw | 传统方案（Sentry + 人工） |
|------|-----------|------------------------|
| 错误发现 | 自动（Webhook / Sidecar） | 需人工查看 |
| 根因定位 | Agent 跨文件追踪调用链 | 人工逐层分析 |
| 修复方式 | 自动生成 + 审查 + 测试 | 人工编码 |
| 通知方式 | 飞书交互卡片 + 审批 | 邮件/IM 通知 |
| 数据归档 | 飞书多维表格自动上报 | 手动记录 |
| 重复错误 | 指纹去重 | 每次人工处理 |
| 业务侵入 | 零侵入 | 需接 SDK |

---

## 维度 3：技术实现性

### 3.1 技术架构

```
src/
├── agent/                  # Agent 核心实现
│   ├── subagents/          # FixAgent / ReviewAgent / TestAgent
│   ├── prompts/            # Prompt 模板（修复/审查/测试）
│   ├── tools/              # LangChain @tool 标准工具（15+ 工具）
│   │   └── langchain_tools.py  # read_file / write_file / search_files
│   ├── orchestrator.py     # LangGraph 图构建 + 节点实现 + 路由（1900+ 行）
│   ├── state.py            # RepairState Pydantic 状态定义
│   ├── validation_gate.py  # 5 重后置硬校验门禁
│   ├── agent_factory.py    # AgentFactory 统一创建三种 Agent
│   ├── instruction_templates.py # 强制指令模板引擎
│   └── notification.py     # NotificationService 飞书通知服务
├── bus/                    # 事件总线
│   ├── event_bus.py        # asyncio.Queue + 幂等去重
│   └── schemas.py          # GitHubEvent / RuntimeLogEvent 数据模型
├── cli/                    # Typer CLI
│   ├── app.py              # 主命令 + config / init-sidecar / sync
│   └── commands/           # webhook 子命令
├── config/                 # 配置管理
│   ├── settings.py         # Pydantic Settings（11 个子模型）
│   ├── agent-config.yaml   # 主配置文件
│   ├── services.yaml       # 服务注册表
│   ├── validator.py        # 配置校验
│   └── service_registry.py # 服务注册表单例
├── monitor/                # 监控器
│   ├── webhook_server.py   # FastAPI Webhook + 事件消费 + 审批监听（1500+ 行）
│   └── dashboard/          # Textual 风格 TUI 仪表盘
│       ├── app.py          # Alt Screen 手动渲染循环
│       ├── state.py        # 线程安全 DashboardState
│       ├── reader.py       # 审计日志 + 应用日志双线程 tail
│       ├── colors.py       # 暗色海洋夜蓝配色方案
│       └── modules/        # 5 个仪表盘模块
├── notify/                 # 飞书通知
│   ├── lark_notify.py      # 6 种卡片模板 + 审批创建/订阅（1000+ 行）
│   └── lark_base.py        # 飞书多维表格客户端（880+ 行）
├── safety/                 # 安全规则引擎
├── store/                  # 持久化存储
│   └── repair_store.py     # 4 种 SQLite Store + 指纹算法（770+ 行）
├── utils/                  # 工具
│   ├── audit.py            # JSONL 审计日志 + LangChain Callback
│   ├── logging.py          # structlog 日志系统
│   ├── path_mapping.py     # 容器路径 → 仓库路径映射
│   ├── rate_limiter.py     # 滑动窗口限流器
│   └── version_manager.py  # Git 版本精确定位
└── entry.py                # 控制台入口
```

### 3.2 安全护栏机制

修复代码在写入文件前，必须通过 `validation_gate.py` 的 5 重后置硬校验：

| 校验项 | 实现方式 | 拦截内容 |
|--------|---------|---------|
| 导入边界 | 剥离所有 import 行后比较核心代码，语义变更 > 3 行拦截 | ImportError 修复越界修改业务逻辑 |
| 语法正确 | `ast.parse(new_code)`，SyntaxError 即拦截 | 修复引入新语法错误 |
| 文件完整性 | 双向检查：目标文件是否遗漏 + 是否修改了非目标文件 | 遗漏修复 / 修改无关文件 |
| 错误覆盖 | 逐错误位置 ±3 行窗口对比原始/修复代码，函数前 10 行 / try-except 块变更也计入 | 部分修复通过验证 |
| 变更行数 | 仅统计功能性代码变更（排除空行/注释/docstring/导入行），小文件（<100行）允许超出 20% | 过度修改 |

变更行数统计时，`_is_functional_line()` 过滤器排除空行、纯注释行、独立 docstring 定界符行，确保 LLM 因为添加注释/docstring 不会被误拦。

### 3.3 持久化与重试机制

系统在多个层面实现了防丢和自动恢复，所有持久化基于 SQLite（`data/repair_records.db`）。

#### 3.3.1 修复生命周期状态机

`RepairStore` 维护 7 种状态的生命周期：

```
FIXING → PENDING_DEPLOY → DEPLOYED
  ↓          ↓
FAILED → ABANDONED (fail_count >= 3)
  ↓
重试 (指数退避: 60s → 120s → 240s)

PENDING_PUSH (推送失败，等待重试)
SUPERSEDED (版本变更，记录过期)
```

- 指纹唯一索引（`fingerprint UNIQUE`），upsert 保证幂等
- 失败退避：`should_retry()` 检查 `fail_count < 3` 且距上次失败超过退避时间
- 版本变更：`mark_superseded_by_version()` 将旧版本的 PENDING_DEPLOY/DEPLOYED 标记为 SUPERSEDED

#### 3.3.2 事件防丢失

`PendingEventStore` 确保事件在任何情况下不丢失：

| 阶段 | 操作 | 说明 |
|------|------|------|
| 接收事件 | `insert()` (INSERT OR IGNORE) | 落盘，防重复 |
| 开始处理 | `mark_processing()` | 标记为 processing |
| 处理成功 | `delete()` | 删除记录 |
| API 失败 | `mark_pending()` | 回退为 pending，等待定时恢复 |
| 服务重启 | `reset_processing_to_pending()` | 卡住的 processing 重置为 pending |
| 定时恢复 | 每 15 分钟扫描 | 将所有 pending 事件重新发布到 EventBus |
| 审批通过 | `delete_all_pending()` | 清空待处理记录 |

#### 3.3.3 推送失败恢复

`PendingPushStore` 处理修复完成但 git push 失败的场景：

- 修复代码已 commit 但因网络/GitHub 不可用导致 push 失败 → 保存完整的推送上下文（分支名、PR 标题、PR 正文、diff 等）
- 服务启动时自动重试：`recover_pending_pushes()` → checkout autofix 分支 → push → create PR → 更新 repair_records → 飞书通知
- 后台定时器：每 10 分钟扫描 `PendingPushStore`，自动重试所有待推送记录
- 重试次数递增，便于排查持续失败的记录

#### 3.3.4 修复流程内重试

重试由 `_can_retry()` 方法控制，两个独立维度任意一个耗尽即停止：

```
重试条件：retry_count < max_retries (默认3) OR total_token_usage < 20000
```

重试时自动回滚本地 Git 变更（`repo.git.checkout("--", ".")`），避免上次失败的修改残留。

**三种拒绝来源的重试上下文构建**：
- Gate（验证门禁）：模板引擎生成结构化指令，包含违规类型和具体参数
- Review（审查 Agent）：直接传递审查反馈原文
- Test（测试 Agent）：传递失败用例列表和测试输出

重试时额外注入上一轮的 `code_changes` 和 `fix_description`，让 LLM 知道自己改了什么、哪里被拒绝。

#### 3.3.5 服务级别修复锁

运行时日志事件处理中，同一服务同时只允许一个修复流程：
- `__fixing__:{service}` 互斥标记放入 `processed_events` 集合
- 后续同一服务的事件直接跳过，避免并发修复冲突
- `create_pr` / `handle_failure` 节点 `finally` 块释放锁

#### 3.3.6 限流保护

`ServiceRateLimiter` 滑动窗口限流：
- 每分钟最多 3 次修复（可配置）
- 每小时最多 20 次修复（可配置）
- 连续限流 >= 10 次触发告警

### 3.4 启动模式与仪表盘

#### 3.4.1 spiderclaw（默认仪表盘模式）

```
┌─────────────────────────────────────────────┐
│                  SpiderClaw Banner           │  header (18行)
├──────────────────────┬──────────────────────┤
│                      │  工具调用 (15行)       │
│   事件日志 (左侧)     │  最近12次工具执行状态   │
│   按事件类型着色       ├──────────────────────┤
│   支持滚轮查看历史     │  节点轨迹 (18行)       │
│                      │  LangGraph节点跳转链路   │
│                      │  Agent节点带流光动画    │
│                      ├──────────────────────┤
│                      │  运行统计 (9行)        │
│                      │  Token/调用/修复统计   │
│                      ├──────────────────────┤
│                      │  系统状态 (5行)        │
│                      │  模型名称/队列积压     │
└──────────────────────┴──────────────────────┘
```

5 个模块由独立的 `MonitorModule` 子类实现，从共享的 `DashboardState`（线程安全单例）获取数据：

| 模块 | 数据来源 | 展示内容 |
|------|---------|---------|
| LogModule | `state.log_entries` (deque, maxlen=500) | 审计事件 + 应用日志，按事件类型着色 |
| ToolModule | `state.tool_calls` (deque, maxlen=20) | 工具名 + 执行状态（成功/失败/执行中） |
| NodeModule | `state.node_jumps` (deque, maxlen=30) | LangGraph 节点跳转链路，Agent 思考节点流光动画 |
| StatsModule | 累积计数器 | 运行时长、Token、LLM/工具调用数、修复成功/失败数 |
| StatusModule | 当前状态字段 | LLM 模型名、队列积压数 |

**技术特点**：
- 手动 Alt Screen 控制（`\x1b[?1049h` / `\x1b[?1049l`），不依赖 rich.live（解决 Windows Git Bash 兼容问题）
- 事件驱动渲染：`AuditReader` 双线程 tail audit.jsonl + spiderclaw.log → 写入 `DashboardState` → `signal_refresh()` → 渲染帧
- 双帧率模式：Agent 思考节点带流光动画时 30 FPS，空闲时降至 20 FPS + 1s 心跳
- Windows 键盘监听（msvcrt）支持上下箭头滚动日志
- 暗色海洋夜蓝配色（`#0a0a1a` 背景 + `#20d5f0` 主色调）

**日志高亮规范**：应用日志按关键词自动着色：
- `ERROR` / 错误 / 失败 / exception / traceback → 红色
- `WARNING` / 警告 → 橙色
- `SUCCESS` / 成功 / 完成 / 启动 → 绿色
- `INFO` / 信息 → 青色
- `DEBUG` / 调试 → 灰色
- 其他 → ICE 色（`#e8eef5`，接近白）

审计事件（node_enter、tool_call 等）的 summary 颜色跟随事件类型自身颜色，禁止写死白色。

#### 3.4.2 spiderclaw --no-dashboard（无头模式）

- 主线程直接运行 Webhook 服务，使用 RichHandler 输出彩色控制台日志
- 同样启动完整后台任务：事件消费、定时推送重试、定时事件恢复、审批 WebSocket 监听
- 通过飞书 `/status` 命令远程获取系统运行状态
- 适用于容器部署（Docker / Kubernetes）、云服务器等无终端环境

### 3.5 工程规范

- 全量 Python 类型注解，Pydantic v2 运行时校验
- pytest + pytest-asyncio 测试框架
- structlog 结构化日志（JSONL 格式 + 控制台彩色输出）
- pylint + bandit 代码质量与安全检查
- GitPython 操作 Git，避免 shell 注入

---

## 快速开始

### 环境要求
- Python 3.10+
- Node.js 16+（飞书 CLI 依赖）
- GitHub API Token
- OpenAI / Claude API Key
- 飞书企业应用

### 安装部署

```bash
# 1. 克隆项目
git clone https://github.com/Dreamt0511/SpiderClaw.git
cd SpiderClaw

# 2. 安装依赖
pip install -r requirements.txt
pip install -e .

# 3. 安装飞书 CLI
npm install -g @larksuite/cli@latest
lark-cli login

# 4. 配置文件
cp src/config/agent-config.example.yaml src/config/agent-config.yaml
# 编辑 agent-config.yaml，填入 GitHub、OpenAI、飞书配置

# 5. 启动（仪表盘模式）
spiderclaw

# 6. 启动（无头模式，适合服务器）
spiderclaw --no-dashboard
```

### 最小配置

```yaml
# src/config/agent-config.yaml
github:
  token: "ghp_xxxxxxxxxxxx"
  allowed_repositories: ["your-org/*"]

openai:
  api_key: "sk-xxxxxxxxxxxx"
  model_name: "gpt-4o"

lark:
  enabled: true
  app_id: "cli_xxxxxxxxxxxx"
  app_secret: "xxxxxxxxxxxx"
  notify_users: ["ou_xxxxxxxxxxxx"]  # 用户 open_id
  base_enabled: true
  base_token: "bascnxxxxxxxxxxxx"

agent:
  enabled: true
  max_retries: 3
  max_change_lines: 50
```

### 注册生产服务

```bash
# 交互式注册
spiderclaw config  # 选择 "服务注册"

# 或手动编辑 src/config/services.yaml
# 同步代码到指定版本
spiderclaw sync -n order-service -v abc1234

# 生成 Sidecar 采集脚本
spiderclaw init-sidecar -n my-service --log-path /var/log/app/app.log
```

---

## 开发命令

```bash
# 运行测试
pytest tests/ -v
pytest tests/ -v --asyncio-mode=auto

# 代码检查
pylint src/
bandit -r src/

# Webhook 调试
ngrok http 8000
# 公网地址: https://xxxx.ngrok-free.app/webhook/github
```

---

## 路线规划

- [ ] 扩展 Java/Go/JavaScript 多语言支持
- [ ] 接入 Sentry / Prometheus 等告警事件源
- [ ] 基于修复历史数据的 Fine-tuning 提升特定场景准确率
- [ ] 飞书群组机器人交互增强（主动查询修复历史/统计）

---

## 许可证

MIT License

---

**SpiderClaw 不只是自动修 Bug 的工具，它重新定义了研发团队与代码错误的关系：让 AI 承担重复性的排障工作，人专注于真正需要创造力的决策。**
