# 飞书通知一键配置指南

## 功能说明

SpiderClaw 支持一键配置飞书通知，无需手动访问飞书开发者后台。用户只需扫码授权，系统会自动完成：
- 飞书企业自建应用创建
- 机器人能力启用
- 必要权限配置
- 应用凭据自动写入配置文件

## 安装依赖

### 方式一：使用独立脚本
```bash
# 安装项目依赖
pip install -e .

# 安装二维码生成依赖
pip install qrcode pillow lark-oapi
```

### 方式二：使用完整安装
```bash
# 安装所有依赖（包含飞书配置所需依赖）
pip install -e ".[all]"
```

## 使用方法

### 方式一：使用主CLI命令（推荐）
```bash
# 一键配置飞书
spiderclaw setup feishu

# 自定义应用名称和描述
spiderclaw setup feishu --app-name "我的自动修复机器人" --app-description "自动修复代码错误的智能助手"

# 指定配置文件路径
spiderclaw setup feishu --config /path/to/your/config.yaml
```

### 方式二：使用独立脚本
```bash
# 一键配置飞书
setup-feishu

# 自定义参数
setup-feishu --app-name "自定义应用名称" --app-description "自定义应用描述"
```

## 配置流程

1. **启动配置**
   ```
   🤖 开始配置飞书通知...
   ============================================================
   
   📱 步骤 1/3: 注册飞书应用
   ```

2. **扫码授权**
   终端会显示ASCII二维码和授权链接：
   ```
   ============================================================
   🚀 飞书应用一键注册
   ============================================================
   
   请使用飞书APP扫描以下二维码，或访问链接完成授权：
   
   🔗 授权链接: https://open.feishu.cn/open-apis/authen/v1/index?xxx
   🔢 验证码: XXXX
   ⏰ 链接有效期: 5 分钟
   
   授权完成后，系统将自动获取应用凭据...
   ```

   你可以选择：
   - 使用飞书手机APP扫描二维码
   - 或访问授权链接，输入验证码完成授权

3. **等待授权完成**
   ```
   等待用户授权...
   ✅ 授权成功！
   
   📋 应用凭据：
   App ID: cli_xxxxxxxxxxxxxxxx
   App Secret: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   
   请将以上凭据配置到 config/agent-config.yaml 中
   ============================================================
   ```

4. **配置自动写入**
   系统会自动将凭据写入配置文件：
   ```
   🔧 步骤 2/3: 更新配置文件
   ✅ 配置已保存到: config/agent-config.yaml
   ```

5. **配置完成**
   ```
   🎉 步骤 3/3: 配置完成
   ============================================================
   
   ✅ 飞书通知配置成功！
   
   📋 应用信息:
      App ID: cli_xxxxxxxxxxxxxxxx
      App Secret: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   
   💡 后续配置:
      1. 在 config/agent-config.yaml 中配置需要通知的用户/群组ID
      2. 重启服务后即可自动发送飞书通知
   
   ============================================================
   ```

## 后续配置

配置完成后，你可以在 `config/agent-config.yaml` 中进一步配置：

```yaml
lark:
  # 是否启用飞书通知
  enabled: true
  # 飞书应用ID（自动生成）
  app_id: "cli_xxxxxxxxxxxxxxxx"
  # 飞书应用密钥（自动生成）
  app_secret: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  # 需要通知的用户open_id列表
  notify_users: 
    - "ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    - "ou_yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy"
  # 需要通知的群组chat_id列表
  notify_groups:
    - "oc_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

### 获取用户open_id和群组chat_id

1. **获取用户open_id**：
   - 在飞书客户端中查看用户个人信息
   - 或使用飞书开放平台的通讯录接口查询

2. **获取群组chat_id**：
   - 在飞书群组设置中查看群组信息
   - 或使用飞书CLI工具查询：`lark-cli im +chat-search --keyword "群组名称"`

## 权限说明

配置过程中会自动申请以下权限：
- `im:message:send_as_bot`：以机器人身份发送消息
- `im:chat:readonly`：读取群组信息
- `contact:user.base:readonly`：读取用户基本信息

## 常见问题

### Q: 扫码后提示"应用不存在"或"无权访问"
A: 请确认你使用的是企业版飞书，且有应用创建权限。如果是个人版飞书，无法创建企业自建应用。

### Q: 授权超时怎么办？
A: 重新运行配置命令，二维码有效期为5分钟，超时后需要重新生成。

### Q: 配置完成后无法发送消息？
A: 检查以下几点：
1. 确认应用已发布并可用
2. 确认用户/群组ID配置正确
3. 确认机器人已被添加到对应群组中
4. 查看日志文件中的详细错误信息

### Q: 如何重新配置？
A: 直接重新运行配置命令即可，系统会创建新的应用或复用现有应用。

## 手动配置（备选方案）

如果自动配置失败，你也可以手动配置：

1. 访问 [飞书开放平台](https://open.feishu.cn/) 登录
2. 创建企业自建应用，启用机器人能力
3. 申请上述必要权限并发布应用
4. 将App ID和App Secret手动填入配置文件
