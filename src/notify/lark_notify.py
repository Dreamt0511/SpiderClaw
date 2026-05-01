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

        # 执行命令
        proc = await asyncio.create_subprocess_exec(
            *cmd,
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

        # 执行命令
        proc = await asyncio.create_subprocess_exec(
            *cmd,
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
