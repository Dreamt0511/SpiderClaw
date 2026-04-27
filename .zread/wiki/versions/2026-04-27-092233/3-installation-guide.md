本页面面向初级开发者，提供SpiderClaw自动诊断修复系统的完整本地安装指南，覆盖环境准备、依赖安装和结果验证全流程，所有步骤均经过Windows环境兼容性测试。

## 前置依赖要求
安装前请确认你的本地环境满足以下最低要求：
1. Python版本 >= 3.10
2. 已安装Git版本管理工具
3. （推荐）已安装venv/conda等虚拟环境管理工具，避免全局依赖冲突
Sources: [pyproject.toml](pyproject.toml#L9)

## 安装流程
```mermaid
flowchart LR
A[克隆代码仓库] --> B[配置虚拟环境]
B --> C[安装依赖包]
C --> D[验证安装结果]
```

### 步骤1：克隆代码仓库
打开Windows终端，执行以下命令拉取代码并进入项目目录：
```bash
git clone <项目仓库地址>
cd SpiderClaw
```

### 步骤2：配置虚拟环境（推荐）
执行以下命令创建并激活虚拟环境，避免全局依赖污染：
```bash
# 创建虚拟环境
python -m venv venv
# 激活虚拟环境
.\venv\Scripts\activate
```
激活成功后终端前缀会出现`(venv)`标识。

### 步骤3：安装依赖包
提供两种安装方式，可根据使用场景选择：
| 安装方式 | 适用场景 | 执行命令 |
| --- | --- | --- |
| 稳定依赖安装 | 普通用户使用 | `pip install -r requirements.txt` |
| 可编辑模式安装 | 二次开发场景 | `pip install -e .` |
Sources: [requirements.txt](requirements.txt#L1-L38), [pyproject.toml](pyproject.toml#L10-L27)

### 步骤4：验证安装结果
执行以下命令验证安装是否成功：
```bash
spiderclaw --version
```
如果输出`spiderclaw 0.1.0`则代表安装成功。如果出现命令找不到的提示，可以使用`python -m src.entry --version`作为替代命令。
Sources: [pyproject.toml](pyproject.toml#L30)

## 常见安装问题排查
| 问题现象 | 可能原因 | 解决方案 |
| --- | --- | --- |
| pip安装依赖时报错"Permission denied" | 终端没有系统写入权限 | 使用管理员权限运行终端，或者在安装命令末尾添加`--user`参数安装到用户目录 |
| 运行spiderclaw命令提示"command not found" | 虚拟环境未激活，或者脚本路径未加入系统PATH | 确认虚拟环境已激活，或者使用`python -m src.entry`代替直接调用spiderclaw命令 |
| 安装依赖时报Python版本不符错误 | 当前Python版本低于3.10 | 安装Python 3.10+版本，使用conda/pyenv等工具管理多版本Python |

## 下一步操作
安装完成后，你可以按照以下路径继续使用系统：
1. 前往[Basic Configuration](4-basic-configuration)完成基础参数配置
2. 参考[Quick Start](2-quick-start)运行你的第一个自动修复测试用例
3. 如需配置通知能力，可查看[Feishu/Lark Notification Setup](7-feishu-lark-notification-setup)