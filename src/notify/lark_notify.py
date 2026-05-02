"""飞书通知模板生成器
基于飞书CLI的lark-im技能实现，无需手动处理认证和令牌
"""
import asyncio
import json
import subprocess
import sys
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


def generate_repair_notification(
    repair_success: bool,
    error_type: str,
    source_branch: str,
    pr_url: str,
    fix_description: str,
    error_message: str = "",
) -> Dict[str, Any]:
    """
    生成飞书修复通知卡片内容

    Args:
        repair_success: 修复是否成功
        error_type: 错误类型
        source_branch: 原错误分支名
        pr_url: 生成的PR链接（如果成功）
        fix_description: 修复描述
        error_message: 失败时的错误信息（可选）

    Returns:
        飞书卡片消息格式的字典
    """
    import re as _re

    # 确保 fix_description 中的列表项与上文有空格分隔
    _desc = _re.sub(r'(?<!\n)\n(?=[-*] )', r'\n\n', fix_description)

    status_emoji = "✅" if repair_success else "❌"
    status_text = "修复成功" if repair_success else "修复失败"

    # 基础卡片内容
    card_content = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🤖 SpiderClaw 自动修复通知"},
            "template": "green" if repair_success else "red",
        },
        "elements": [
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag": "markdown",
                            "content": f"**状态**\n{status_emoji} {status_text}",
                        },
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "markdown",
                            "content": f"**错误类型**\n{error_type}",
                        },
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "markdown",
                            "content": f"**原分支**\n{source_branch}",
                        },
                    },
                ],
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "markdown",
                    "content": f"**修复说明**\n{_desc}",
                },
            },
        ],
    }

    # 成功时添加PR链接
    if repair_success and pr_url:
        card_content["elements"].append(
            {
                "tag": "div",
                "text": {
                    "tag": "markdown",
                    "content": f"**PR链接**\n🔗 [查看PR]({pr_url})",
                },
            }
        )

    # 失败时添加错误信息
    if not repair_success and error_message:
        card_content["elements"].append(
            {
                "tag": "div",
                "text": {
                    "tag": "markdown",
                    "content": f"**错误信息**\n❌ {error_message}",
                },
            }
        )

    # 添加页脚
    card_content["elements"].append({"tag": "hr"})
    card_content["elements"].append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "此通知由 SpiderClaw 自动修复系统生成",
                }
            ],
        }
    )

    return {
        "msg_type": "interactive",
        "card": card_content,
    }


def generate_simple_notification(content: str, title: str = "SpiderClaw 通知") -> Dict[str, Any]:
    """
    生成简单的纯文本飞书通知

    Args:
        content: 通知内容
        title: 通知标题

    Returns:
        飞书消息格式的字典
    """
    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": [[{"tag": "text", "text": content}]],
                }
            }
        },
    }


async def send_message(
    receive_id: str,
    message: Dict[str, Any],
    receive_id_type: str = "open_id",
    as_bot: bool = True,
) -> bool:
    """
    使用lark-cli发送飞书消息

    Args:
        receive_id: 接收者ID（用户open_id/群组chat_id）
        message: 消息内容字典（由generate_*_notification函数生成）
        receive_id_type: 接收者ID类型：open_id / chat_id
        as_bot: 是否以机器人身份发送

    Returns:
        是否发送成功
    """
    try:
        msg_type = message["msg_type"]
        content = json.dumps(message["content"], ensure_ascii=False)

        # 构建命令参数
        cmd = [
            "lark-cli", "im", "+messages-send",
            "--as", "bot" if as_bot else "user",
            f"--{receive_id_type.replace('_', '-')}", receive_id,
            "--msg-type", msg_type,
            "--content", content
        ]

        logger.info(f"发送飞书消息: {' '.join(cmd[:-2])}...")  # 不打印敏感内容

        # 执行命令 — Windows 下 .cmd 文件需要通过 shell 执行
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        except FileNotFoundError:
            proc = await asyncio.create_subprocess_shell(
                " ".join(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info("飞书消息发送成功")
            return True
        else:
            error_msg = stderr.decode('utf-8', errors='ignore')
            logger.error(f"飞书消息发送失败: {error_msg}")
            return False

    except Exception as e:
        logger.error(f"发送飞书消息异常: {e}", exc_info=True)
        return False


async def send_markdown_message(
    receive_id: str,
    markdown_content: str,
    title: str = "",
    receive_id_type: str = "open_id",
    as_bot: bool = True,
    is_card: bool = False,
) -> bool:
    """
    发送markdown格式的飞书消息或交互式卡片

    Args:
        receive_id: 接收者ID
        markdown_content: markdown格式的内容或卡片JSON
        title: 消息标题（可选）
        receive_id_type: 接收者ID类型
        as_bot: 是否以机器人身份发送
        is_card: 是否为交互式卡片消息

    Returns:
        是否发送成功
    """
    try:
        # 构建命令参数
        lark_cmd = "lark-cli.cmd" if sys.platform == "win32" else "lark-cli"
        cmd = [
            lark_cmd, "im", "+messages-send",
            "--as", "bot" if as_bot else "user",
        ]
        # 根据ID类型选择参数
        if receive_id_type == "open_id":
            cmd.extend(["--user-id", receive_id])
        elif receive_id_type == "chat_id":
            cmd.extend(["--chat-id", receive_id])
        else:
            raise ValueError(f"不支持的接收ID类型: {receive_id_type}")

        if is_card:
            # 发送交互式卡片
            cmd.extend([
                "--content", markdown_content,
                "--msg-type", "interactive"
            ])
        else:
            # 发送普通markdown
            escaped_markdown = markdown_content.replace('"', '\\"').replace('\n', '\\n')
            cmd.extend(["--markdown", escaped_markdown])

        logger.info(f"发送飞书markdown消息到: {receive_id}")

        # 执行命令 — Windows 下 .cmd 文件需要通过 shell 执行
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        except FileNotFoundError:
            proc = await asyncio.create_subprocess_shell(
                " ".join(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info("飞书markdown消息发送成功")
            return True
        else:
            error_msg = stderr.decode('utf-8', errors='ignore')
            logger.error(f"飞书markdown消息发送失败: {error_msg}")
            return False

    except Exception as e:
        logger.error(f"发送飞书markdown消息异常: {e}", exc_info=True)
        return False


async def send_repair_notification(
    repair_success: bool,
    error_type: str,
    source_branch: str,
    pr_url: str,
    fix_description: str,
    receive_id: str,
    receive_id_type: str = "open_id",
    error_message: str = "",
    pr_author: str = "未知用户",
    bug_count: int = 0,
    original_pr_url: str = "",
    change_lines: int = 0,
    base_url: str = "",
    service_version: str = "",
    environment: str = "开发",
) -> bool:
    """
    发送修复结果通知（使用飞书卡片格式）

    Args:
        repair_success: 修复是否成功
        error_type: 错误类型
        source_branch: 原错误分支名
        pr_url: 生成的PR链接（如果成功）
        fix_description: 修复描述
        receive_id: 接收者ID
        receive_id_type: 接收者ID类型
        error_message: 失败时的错误信息（可选）
        pr_author: PR提交者昵称
        bug_count: 修复的bug数量
        original_pr_url: 原错误PR链接（可选）

    Returns:
        是否发送成功
    """
    import re as _re
    import json

    # 确保 fix_description 中的列表项与上文有空格分隔
    _desc = _re.sub(r'(?<!\n)\n(?=[-*] )', r'\n\n', fix_description)

    # 构造飞书卡片
    if repair_success and bug_count > 0:
        # 成功卡片
        card_content = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🤖 SpiderClaw 自动修复通知"},
                "template": "green"
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**🎉 自动修复完成**\n已修复 **{pr_author}** 提交的PR中的代码错误（涉及 {bug_count} 个文件），请 review！"
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "fields": [
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**变更行数**\n{change_lines} 行"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**分支**\n{source_branch}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**跟踪版本**\n`{service_version}`" if service_version else "**跟踪版本**\n未配置"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**环境**\n{environment}"}},
                    ]
                },
                {
                    "tag": "markdown",
                    "content": f"**📝 修复说明**\n{_desc}"
                }
            ]
        }

        # 添加PR链接按钮
        actions = []
        if pr_url:
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔗 查看修复PR"},
                "url": pr_url,
                "type": "primary"
            })
        if original_pr_url:
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "📋 查看原PR"},
                "url": original_pr_url,
                "type": "default"
            })
        if base_url:
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "📊 查看数据统计"},
                "url": base_url,
                "type": "default"
            })
        if actions:
            card_content["elements"].append({
                "tag": "action",
                "actions": actions
            })
    else:
        # 失败卡片
        card_content = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🤖 SpiderClaw 自动修复通知"},
                "template": "red"
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**❌ 自动修复失败**\n修复 **{pr_author}** 提交的PR时遇到问题"
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "fields": [
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**变更行数**\n{change_lines} 行"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**分支**\n{source_branch}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**跟踪版本**\n`{service_version}`" if service_version else "**跟踪版本**\n未配置"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**环境**\n{environment}"}},
                    ]
                }
            ]
        }

        # 添加bug数量
        if bug_count > 0:
            card_content["elements"].append({
                "tag": "markdown",
                "content": f"**待修复bug数**\n{bug_count}个"
            })

        # 添加失败原因
        if error_message:
            card_content["elements"].append({
                "tag": "markdown",
                "content": f"**❌ 失败原因**\n{error_message}"
            })

        # 添加操作按钮
        actions = []
        if original_pr_url:
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "📋 查看原PR"},
                "url": original_pr_url,
                "type": "default"
            })
        if pr_url:
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔗 查看相关PR"},
                "url": pr_url,
                "type": "default"
            })
        if base_url:
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "📊 查看数据统计"},
                "url": base_url,
                "type": "default"
            })
        if actions:
            card_content["elements"].append({
                "tag": "action",
                "actions": actions
            })

    # 添加页脚
    card_content["elements"].extend([
        {"tag": "hr"},
        {
            "tag": "note",
            "elements": [
                {"tag": "plain_text", "content": "此通知由 SpiderClaw 自动修复系统生成"}
            ]
        }
    ])

    # 序列化为JSON字符串，不额外转义（命令行自动处理）
    content_json = json.dumps(card_content, ensure_ascii=False)

    # 发送交互式卡片消息
    return await send_markdown_message(
        receive_id=receive_id,
        markdown_content=content_json,
        receive_id_type=receive_id_type,
        is_card=True
    )


async def send_config_needed_notification(
    service_name: str,
    error_summary: str,
    receive_id: str,
    receive_id_type: str = "open_id",
    reason: str = "未注册",
) -> bool:
    """发送"需要配置"通知 — 服务未注册或版本未配置时触发

    Args:
        service_name: 服务名称
        error_summary: 错误摘要
        receive_id: 接收者ID
        receive_id_type: 接收者ID类型
        reason: 原因（"未注册" 或 "版本未配置"）
    """
    import json

    card_content = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "⚙️ SpiderClaw 需要配置"},
            "template": "orange"
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"**无法自动修复错误**\n\n**原因**：服务 `{service_name}` {reason}"
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**服务名**\n`{service_name}`"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**问题**\n{reason}"}},
                ]
            },
            {
                "tag": "markdown",
                "content": f"**错误摘要**\n```\n{error_summary[:500]}\n```"
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": "**请配置后重试**\n\n1. 编辑 `src/config/services.yaml`，添加服务的 `version` 字段\n2. 或运行 `spiderclaw sync --name {0} --version <版本号>` 拉取代码".format(service_name)
            },
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": "配置完成后，后续同类错误将自动修复"}
                ]
            }
        ]
    }

    content_json = json.dumps(card_content, ensure_ascii=False)
    return await send_markdown_message(
        receive_id=receive_id,
        markdown_content=content_json,
        receive_id_type=receive_id_type,
        is_card=True
    )


async def send_already_fixing_notification(
    fingerprint: str,
    pr_url: str,
    service: str,
    receive_id: str,
    receive_id_type: str = "open_id",
) -> bool:
    """发送"跳过重复修复"卡片通知"""
    import json

    pr_line = f"**修复 PR**：{pr_url}" if pr_url else ""
    card_content = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "⏭️ SpiderClaw 跳过重复修复"},
            "template": "blue"
        },
        "elements": [
            {"tag": "markdown", "content": "**相同错误已有修复在等待部署，跳过本次修复**"},
            {"tag": "hr"},
            {"tag": "div", "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**服务名**\n{service}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**错误指纹**\n`{fingerprint}`"}},
            ]},
            *([{"tag": "markdown", "content": pr_line}] if pr_line else []),
            {"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "部署修复后，相同错误将不再触发"}]}
        ]
    }
    content_json = json.dumps(card_content, ensure_ascii=False)
    return await send_markdown_message(
        receive_id=receive_id,
        markdown_content=content_json,
        receive_id_type=receive_id_type,
        is_card=True
    )


APPROVAL_WIDGET_ID = "event_summary"
_APPROVAL_CONFIG_PATH = "data/approval_config.json"


def _load_approval_code() -> str:
    """从本地文件读取已保存的 approval_code"""
    import os
    try:
        if os.path.exists(_APPROVAL_CONFIG_PATH):
            with open(_APPROVAL_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("approval_code", "")
    except Exception as e:
        logger.warning(f"读取审批配置失败: {e}")
    return ""


def _save_approval_code(approval_code: str):
    """保存 approval_code 到本地文件"""
    import os
    os.makedirs(os.path.dirname(_APPROVAL_CONFIG_PATH), exist_ok=True)
    with open(_APPROVAL_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"approval_code": approval_code}, f, ensure_ascii=False, indent=2)
    logger.info(f"审批配置已保存: {_APPROVAL_CONFIG_PATH}")


async def ensure_approval_definition(
    config_approval_code: str = "",
    approver_open_id: str = "",
) -> tuple[str, str]:
    """确保审批定义存在，返回 (approval_code, widget_id)

    优先使用配置中的 approval_code，否则自动创建并保存。
    """
    # 1. 优先用配置值
    if config_approval_code:
        return config_approval_code, APPROVAL_WIDGET_ID

    # 2. 检查本地缓存
    saved = _load_approval_code()
    if saved:
        return saved, APPROVAL_WIDGET_ID

    # 3. 自动创建审批定义
    if not approver_open_id:
        logger.error("无法创建审批定义：缺少审批人 open_id（请配置 notify_users）")
        return "", ""

    import sys as _sys
    lark_cmd = "lark-cli.cmd" if _sys.platform == "win32" else "lark-cli"

    node_id = "approval_node_1"
    form_content = json.dumps([{
        "id": APPROVAL_WIDGET_ID,
        "name": "@i18n@event_summary",
        "required": False,
        "type": "textarea",
    }], ensure_ascii=False)

    request_body = {
        "approval_name": "@i18n@approval_name",
        "viewers": [{"viewer_type": "TENANT"}],
        "form": {"form_content": form_content},
        "node_list": [
            {"id": "START"},
            {
                "id": node_id,
                "name": "@i18n@approve_node",
                "node_type": "OR",
                "approver": [{"type": "Personal", "user_id": approver_open_id}],
            },
            {"id": "END"},
        ],
        "i18n_resources": [{
            "locale": "zh-CN",
            "is_default": True,
            "texts": [
                {"key": "@i18n@approval_name", "value": "SpiderClaw 待处理事件确认"},
                {"key": "@i18n@event_summary", "value": "事件摘要"},
                {"key": "@i18n@approve_node", "value": "审批"},
            ],
        }],
        "icon": 1,
    }

    cmd = [
        lark_cmd, "api", "POST",
        "/open-apis/approval/v4/approvals",
        "--as", "bot",
        "--data", json.dumps(request_body, ensure_ascii=False),
    ]

    logger.info("自动创建飞书审批定义...")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="ignore")
            logger.error(f"创建审批定义失败: {error_msg}")
            return "", ""

        output = stdout.decode("utf-8", errors="ignore")
        logger.info(f"创建审批定义响应: {output}")

        import re
        match = re.search(r'"approval_code"\s*:\s*"([^"]+)"', output)
        if match:
            new_code = match.group(1)
            _save_approval_code(new_code)
            logger.info(f"审批定义创建成功: {new_code}")
            return new_code, APPROVAL_WIDGET_ID

        logger.error("未能从响应中提取 approval_code")
        return "", ""

    except Exception as e:
        logger.error(f"创建审批定义异常: {e}", exc_info=True)
        return "", ""


async def subscribe_approval_events(approval_code: str) -> bool:
    """订阅审批事件（只需调用一次，重复调用返回 1390007 可忽略）

    调用: lark-cli api POST /open-apis/approval/v4/approvals/{approval_code}/subscribe --as bot
    """
    import sys as _sys
    lark_cmd = "lark-cli.cmd" if _sys.platform == "win32" else "lark-cli"
    cmd = [lark_cmd, "api", "POST",
           f"/open-apis/approval/v4/approvals/{approval_code}/subscribe",
           "--as", "bot"]

    logger.info(f"订阅审批事件: {approval_code}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        output = stdout.decode("utf-8", errors="ignore")
        error_output = stderr.decode("utf-8", errors="ignore")
        # 1390007 = 已订阅，也算成功
        if proc.returncode == 0 or "1390007" in output or "1390007" in error_output:
            logger.info("审批事件订阅成功（或已订阅）")
            return True
        logger.error(f"审批事件订阅失败: {error_output}")
        return False
    except Exception as e:
        logger.error(f"订阅审批事件异常: {e}", exc_info=True)
        return False


async def create_pending_events_approval(
    event_summaries: list[dict],
    total_count: int,
    approval_code: str,
    widget_id: str,
    open_id: str = "",
) -> str:
    """创建飞书审批实例，返回 instance_code（失败返回空字符串）

    调用: lark-cli api POST /open-apis/approval/v4/instances --as bot
    """
    from datetime import datetime
    import uuid as _uuid

    # 构造事件列表文本
    event_lines = []
    for i, evt in enumerate(event_summaries[:10], 1):
        try:
            created = datetime.fromtimestamp(evt["created_at"]).strftime("%m-%d %H:%M")
            type_label = "CI事件" if evt["event_type"] == "github" else "运行时日志"
            event_lines.append(f"{i}. {evt['source']} ({type_label}, {created})")
        except Exception as e:
            logger.warning(f"解析事件 {i} 失败: {e}")
    if total_count > 10:
        event_lines.append(f"... 还有 {total_count - 10} 个事件")

    description = (
        f"服务重启后发现 {total_count} 个未处理的事件，请确认是否恢复处理：\n\n"
        + "\n".join(event_lines)
    )

    # 构造表单数据
    form_data = json.dumps([{
        "id": widget_id,
        "type": "textarea",
        "value": description,
    }], ensure_ascii=False)

    # 构造请求体
    request_body_dict = {
        "approval_code": approval_code,
        "form": form_data,
        "uuid": str(_uuid.uuid4()),
    }
    # 审批发起人 open_id（必填，与 user_id 二选一）
    if open_id:
        request_body_dict["open_id"] = open_id
    request_body = json.dumps(request_body_dict, ensure_ascii=False)

    import sys as _sys
    lark_cmd = "lark-cli.cmd" if _sys.platform == "win32" else "lark-cli"
    cmd = [
        lark_cmd, "api", "POST",
        "/open-apis/approval/v4/instances",
        "--as", "bot",
        "--data", request_body,
    ]

    logger.info(f"创建审批实例: {total_count} 个待处理事件")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="ignore")
            logger.error(f"创建审批实例失败: {error_msg}")
            return ""

        output = stdout.decode("utf-8", errors="ignore")
        logger.info(f"审批实例创建响应: {output}")

        # 提取 instance_code
        import re
        match = re.search(r'"instance_code"\s*:\s*"([^"]+)"', output)
        if match:
            instance_code = match.group(1)
            logger.info(f"审批实例创建成功: {instance_code}")
            return instance_code

        logger.error("未能从响应中提取 instance_code")
        return ""

    except Exception as e:
        logger.error(f"创建审批实例异常: {e}", exc_info=True)
        return ""


async def send_pending_events_notification(
    event_summaries: list[dict],
    total_count: int,
    notify_users: list[str],
    approval_code: str = "",
    widget_id: str = "",
) -> str:
    """发送待处理事件恢复通知（通过飞书审批）

    创建飞书审批实例，用户在审批中心确认或拒绝。

    Returns:
        创建成功返回 instance_code，失败返回空字符串
    """
    if not approval_code or not widget_id:
        logger.error("审批配置不完整（approval_code 或 widget_id 为空）")
        return ""

    # 获取审批发起人的 open_id（使用第一个 notify_user）
    open_id = notify_users[0] if notify_users else ""

    instance_code = await create_pending_events_approval(
        event_summaries, total_count, approval_code, widget_id, open_id,
    )
    return instance_code


async def send_runtime_repair_notification(
    repair_success: bool,
    service: str,
    error_type: str,
    error_location: str,
    fix_description: str,
    receive_id: str,
    receive_id_type: str = "open_id",
    error_message: str = "",
    file_count: int = 0,
    change_lines: int = 0,
    pr_url: str = "",
    base_url: str = "",
    duplicate_info: dict | None = None,
) -> bool:
    """
    发送运行时错误修复通知（Web 服务专用卡片格式）

    Args:
        repair_success: 修复是否成功
        service: 服务名称
        error_type: 错误类型
        error_location: 错误位置（如 /app/main.py:10）
        fix_description: 修复描述
        receive_id: 接收者ID
        receive_id_type: 接收者ID类型
        error_message: 失败时的错误信息
        file_count: 修复文件数
        change_lines: 变更行数
        pr_url: 修复PR链接
        base_url: 数据统计链接
    """
    import re as _re
    import json

    _desc = _re.sub(r'(?<!\n)\n(?=[-*] )', r'\n\n', fix_description) if fix_description else ""

    if repair_success:
        card_content = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🤖 SpiderClaw 生产环境修复通知"},
                "template": "green"
            },
            "elements": [
                {"tag": "markdown", "content": f"**✅ 服务 {service} 的运行时错误已自动修复**"},
                {"tag": "hr"},
                {"tag": "div", "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**错误类型**\n{error_type}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**错误位置**\n`{error_location}`"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**环境**\n生产"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**修复文件**\n{file_count} 个"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**变更行数**\n{change_lines} 行"}},
                ]},
                {"tag": "markdown", "content": f"**📝 修复说明**\n{_desc}"},
            ]
        }

        actions = []
        if pr_url:
            actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "🔗 查看修复PR"}, "url": pr_url, "type": "primary"})
        if base_url:
            actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "📊 查看数据统计"}, "url": base_url, "type": "default"})
        if actions:
            card_content["elements"].append({"tag": "action", "actions": actions})
    else:
        if duplicate_info:
            # 重复修复：合并为一条通知，展示已有修复信息
            fp = duplicate_info.get("fingerprint", "")
            existing_pr = duplicate_info.get("pr_url", "")
            card_content = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "⏭️ SpiderClaw 跳过重复修复"},
                    "template": "blue"
                },
                "elements": [
                    {"tag": "markdown", "content": f"**⏭️ 服务 {service} 的相同错误已有修复在等待部署，跳过本次修复**"},
                    {"tag": "hr"},
                    {"tag": "div", "fields": [
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**错误类型**\n{error_type}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**错误位置**\n`{error_location}`"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**环境**\n生产"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**错误指纹**\n`{fp}`"}},
                    ]},
                ]
            }
            if existing_pr:
                card_content["elements"].append({"tag": "markdown", "content": f"**修复 PR**：{existing_pr}"})
            card_content["elements"].append({"tag": "note", "elements": [{"tag": "plain_text", "content": "部署修复后，相同错误将不再触发"}]})
        else:
            card_content = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "🤖 SpiderClaw 生产环境修复通知"},
                    "template": "red"
                },
                "elements": [
                    {"tag": "markdown", "content": f"**❌ 服务 {service} 的运行时错误修复失败**"},
                    {"tag": "hr"},
                    {"tag": "div", "fields": [
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**错误类型**\n{error_type}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**错误位置**\n`{error_location}`"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**环境**\n生产"}},
                    ]},
                ]
            }

            if error_message:
                card_content["elements"].append({"tag": "markdown", "content": f"**❌ 失败原因**\n{error_message}"})

        actions = []
        if base_url:
            actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "📊 查看数据统计"}, "url": base_url, "type": "default"})
        if actions:
            card_content["elements"].append({"tag": "action", "actions": actions})

    card_content["elements"].extend([
        {"tag": "hr"},
        {"tag": "note", "elements": [{"tag": "plain_text", "content": "此通知由 SpiderClaw 自动修复系统生成"}]}
    ])

    content_json = json.dumps(card_content, ensure_ascii=False)
    return await send_markdown_message(
        receive_id=receive_id,
        markdown_content=content_json,
        receive_id_type=receive_id_type,
        is_card=True
    )
