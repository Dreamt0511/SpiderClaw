# SpiderClaw 代码审查报告（2026-05-07）

## 范围
- Webhook 接入层：`src/monitor/webhook_server.py`
- Agent 工具层：`src/agent/tools/langchain_tools.py`
- 编排层：`src/agent/orchestrator.py`
- 配置层：`src/config/settings.py`
- 路径映射：`src/utils/path_mapping.py`

## 功能改进建议

1. **Webhook 增加请求体大小限制与速率限制**
   - 现状：配置中定义了 `max_payload_size` 与队列容量等参数，但在 Webhook 入口未见强制执行。
   - 建议：在 FastAPI 中间件中按 `Content-Length` / 实际 body 长度拒绝超限请求，并结合 IP 或 Delivery-ID 加入基础限流。

2. **CORS 按环境收敛**
   - 现状：`allow_origins=["*"]`、`allow_methods=["*"]`。
   - 建议：生产环境仅开放必要来源与方法，并关闭 `allow_credentials=True` + `*` 的组合。

3. **工具层读文件支持大小上限**
   - 现状：`read_file` 直接整文件读入内存。
   - 建议：增加最大读取字节阈值（如 1-5MB）和按行截断，避免大文件触发内存压力。

4. **搜索工具优化为流式/索引方式**
   - 现状：`search_code` 逐文件 `read_file` + Python 字符串扫描，仓库变大后效率低。
   - 建议：在安全沙箱前提下优先调用 `rg`，或实现分块读取 + 早停策略。

5. **编排层锁表清理策略**
   - 现状：`_repo_locks` 以 `repo_path` 持久缓存，长期运行可能增长。
   - 建议：加 LRU / TTL 清理，或在任务完成后按引用计数回收。

## 安全/漏洞审查发现

### 高优先级

1. **TLS 验证被全局关闭风险（潜在 MITM）**
   - 文件中存在 `urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)`，通常与 `verify=False` 组合使用。
   - 风险：若后续网络请求在无证书校验下执行，可能遭受中间人攻击并泄露 token。
   - 建议：删除全局禁用，改为“默认强校验”，仅在显式 debug 配置下、受控域名里临时放宽，并记录审计日志。

2. **Webhook CORS 过宽**
   - `*` 来源 + 全方法全头 + credentials 组合提升误配与跨域滥用风险。
   - 建议：按环境白名单收敛，最小化暴露面。

### 中优先级

3. **路径边界检查可加强（符号链接/大小写边界）**
   - 现有 `startswith(os.path.abspath(repo_path))` 基本可防路径穿越。
   - 建议升级为 `pathlib.Path(full).resolve().is_relative_to(Path(repo).resolve())`（或等价兼容写法），并加符号链接测试用例。

4. **错误信息可能外泄内部路径/异常细节**
   - `read_file`/`write_file` 直接回传异常字符串。
   - 风险：在多租户或外部可见场景泄露主机路径、编码细节。
   - 建议：对外返回泛化错误码，对内写详细日志。

5. **JSON 解析双读取可优化**
   - Webhook 中先 `request.body()` 再 `request.json()`，虽然 FastAPI 常有缓存，但建议统一使用一次解析并复用 bytes，降低歧义。

## 建议优先级路线图

- **P0（本周）**：收敛 TLS 校验策略、收敛 CORS、加入 payload 限制。
- **P1（两周）**：强化路径检查、错误脱敏、完善安全测试。
- **P2（后续）**：工具层性能优化（rg/索引）、锁清理机制。

