本文档面向高级开发者，介绍如何为SpiderClaw Agent系统开发符合LangChain标准的自定义工具，扩展系统的自动化处理能力。仅涉及工具开发相关规范、流程和最佳实践，其他模块内容请参考对应文档页面。

## 工具开发基础规范
SpiderClaw的工具系统基于LangChain Core的Tool标准实现，所有自定义工具必须遵循以下统一规范：
1. **装饰器要求**：所有工具函数必须使用`@tool`装饰器标注，自动完成参数解析和Schema生成
2. **上下文管理**：禁止硬编码环境参数，通过`get_tool_context()`获取运行时上下文（包含仓库路径、GitHub Token、配置参数等），工具执行前可通过`set_tool_context()`注入全局上下文
3. **安全约束**：所有涉及文件系统、网络请求的工具必须实现路径穿越检查，禁止访问工作目录外的系统资源
4. **错误返回规范**：执行失败时返回字符串必须以`Error:`开头，便于Agent识别错误类型
5. **权限标记**：敏感操作工具需要在注释中添加`权限: <权限类型>`标记，供编排层做权限校验

Sources: [langchain_tools.py](src/agent/tools/langchain_tools.py#L7-L35)
## 自定义工具开发流程
完整的工具开发流程如下图所示：
```mermaid
flowchart LR
A[定义工具函数] --> B[添加@tool装饰器]
B --> C[编写函数注释（含参数说明、权限标记）]
C --> D[实现业务逻辑，添加上下文读取、安全检查]
D --> E[统一错误处理格式]
E --> F[添加到all_tools导出列表]
F --> G[在__init__.py中导出新增工具]
G --> H[编写单元测试验证功能]
```

### 步骤详解
1. **函数定义与注释**：工具函数的注释会自动转换为Agent可以识别的工具描述，必须包含功能说明、参数含义、返回值说明三部分，敏感操作需添加权限标记
2. **上下文与安全实现**：所有操作必须从上下文获取工作目录，对用户传入的路径参数必须做绝对路径校验，确保不超出仓库范围
3. **注册到工具集**：新增工具需要同时添加到`langchain_tools.py`末尾的`all_tools`列表，以及`__init__.py`的导出列表中，系统会自动加载所有注册的工具供Agent调用

Sources: [langchain_tools.py](src/agent/tools/langchain_tools.py#L38-L73), [__init__.py](src/agent/tools/__init__.py#L2-L36)
## 自定义工具示例：代码统计工具
以下是一个完整的自定义工具示例，实现统计仓库中指定类型代码行数的功能：
| 开发阶段 | 代码示例 |
| --- | --- |
| 基础实现 | ```python
@tool
def count_code_lines(file_type: str = "py") -> str:
    """
    统计仓库中指定类型代码的总行数。
    Args:
        file_type: 要统计的文件类型，默认为py
    Returns:
        str: 统计结果，失败返回错误信息
    """
    repo_path = get_tool_context().get("repo_path", "")
    if not repo_path:
        return "Error: 仓库路径未设置，请先克隆仓库"
    # 路径安全检查
    if ".." in file_type or "/" in file_type:
        return "Error: 非法的文件类型参数"
    total_lines = 0
    files = search_files.invoke({"pattern": f"**/*.{file_type}"})
    for file_path in files:
        content = read_file.invoke({"file_path": file_path})
        if not content.startswith("Error:"):
            total_lines += len(content.split('\n'))
    return f"Total {file_type} code lines: {total_lines}"
``` |
| 注册到工具集 | 在`langchain_tools.py`末尾的`all_tools`列表添加`count_code_lines`，同时在`__init__.py`的导出列表中添加对应条目 |

Sources: [langchain_tools.py](src/agent/tools/langchain_tools.py#L889-L903)
## 工具权限体系
系统支持以下工具权限类型，可根据工具安全等级选择：
| 权限类型 | 适用场景 | 说明 |
| --- | --- | --- |
| 无标记 | 公开读取类工具 | 所有子Agent均可调用，无权限限制 |
| ORCHESTRATOR_ONLY | 写入/修改类工具 | 仅主编排Agent可以调用，子Agent无法直接调用，防止误操作 |
| ADMIN_ONLY | 系统级操作工具 | 需要管理员授权才能调用，适用于部署、配置修改等高风险操作 |

Sources: [langchain_tools.py](src/agent/tools/langchain_tools.py#L77-L81)
## 后续步骤
完成自定义工具开发后，您可以：
1. 学习如何为工具编写配套的调用提示词：[Prompt Customization Guide](20-prompt-customization-guide)
2. 开发自定义子Agent使用新增工具：[Custom Subagent Development](19-custom-subagent-development)
3. 按照单元测试规范编写工具测试用例：[Unit Testing Best Practices](22-unit-testing-best-practices)