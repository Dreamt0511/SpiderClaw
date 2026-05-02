分几个层次来测：

**1. 单元测试** — 已经通过

```bash
pytest tests/ -v
```

**2. 测试 webhook 端点** — 启动服务后用 curl 模拟

```bash
# 终端 1：启动 webhook
spiderclaw --no-dashboard --port 8000

# 终端 2：模拟采集脚本发送错误，测试的version可以设置为main
curl -X POST http://localhost:8000/webhook/log \
  -H "Content-Type: application/json" \
  -d '{
    "log": "Traceback (most recent call last):\n  File \"/app/main.py\", line 10, in <module>\n    result = 1 / 0\nZeroDivisionError: division by zero",
    "service": "order-service",
    "version": "main",
    "hostname": "test-server"
  }'
```

**3. 测试不同场景**

| 场景                      | 预期结果                       |
| ------------------------- | ------------------------------ |
| 服务未注册                | 返回正常，飞书通知"需要配置"   |
| 服务已注册但 version 为空 | 返回正常，飞书通知"版本未配置" |
| 服务已注册且 version 有值 | 尝试拉代码修复（如果仓库可达） |
| 重复发送同一 event_id     | 去重，不重复处理               |

**4. 完整流程测试**

```bash
# 1) 注册服务
spiderclaw config  # 选"服务注册"，填入一个真实仓库

# 2) 同步版本
spiderclaw sync -n order-service -v <真实commit>

# 3) 启动服务
spiderclaw --no-dashboard --port 8000

# 4) 模拟错误
curl -X POST http://localhost:8000/webhook/log \
  -H "Content-Type: application/json" \
  -d '{"log": "...真实Traceback...", "service": "order-service"}'
```

正式使用时的版本同步流程：

**核心思路**：每次部署新版本时，告诉 SpiderClaw 当前线上跑的是哪个 commit。

```bash
# 典型的 CI/CD 流程：

# 1. 开发者合并 PR，CI 部署到线上
# 2. 部署完成后，更新 SpiderClaw 的跟踪版本
spiderclaw sync -n order-service -v $(git rev-parse origin/main)
```

**具体场景**：

| 场景            | 操作                                                         |
| --------------- | ------------------------------------------------------------ |
| 首次接入        | `spiderclaw sync -n order-service -v <当前线上的commit SHA>` |
| 每次部署后      | `spiderclaw sync -n order-service -v <新版本的commit SHA>`   |
| 用 tag 管理版本 | `spiderclaw sync -n order-service -v v1.2.3`                 |

**自动化方案**（推荐）：在 CI/CD pipeline 里加一步：

```yaml
# GitHub Actions 示例
- name: Update SpiderClaw version
  run: spiderclaw sync -n order-service -v ${{ github.sha }}
```

这样每次部署自动同步，SpiderClaw 收到错误时就能精准 checkout 到出问题的代码版本来修复。

**你当前测试**的话，直接用你仓库 `AutoFix_Test_rep` 的最新 commit：

```bash
spiderclaw sync -n order-service -v main
```

或者查到具体 SHA：
```bash
git ls-remote https://github.com/Dreamt0511/AutoFix_Test_rep HEAD
```