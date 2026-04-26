"""飞书通知模板生成器
基于飞书CLI的lark-im技能实现，无需手动处理认证和令牌
"""
import asyncio
import json
import subprocess
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
                            "tag": "lark_md",
                            "content": f"**状态**\n{status_emoji} {status_text}",
                        },
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"**错误类型**\n{error_type}",
                        },
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"**原分支**\n{source_branch}",
                        },
                    },
                ],
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**修复说明**\n{fix_description}",
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
                    "tag": "lark_md",
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
                    "tag": "lark_md",
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
) -> bool:
    """
    发送markdown格式的飞书消息（更简单的接口，不需要手动构建消息结构）

    Args:
        receive_id: 接收者ID
        markdown_content: markdown格式的内容
        title: 消息标题（可选）
        receive_id_type: 接收者ID类型
        as_bot: 是否以机器人身份发送

    Returns:
        是否发送成功
    """
    try:
        # 构建命令参数
        cmd = [
            "lark-cli", "im", "+messages-send",
            "--as", "bot" if as_bot else "user",
            f"--{receive_id_type.replace('_', '-')}", receive_id,
            "--markdown", markdown_content
        ]

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
) -> bool:
    """
    发送修复结果通知（使用卡片格式）

    Args:
        repair_success: 修复是否成功
        error_type: 错误类型
        source_branch: 原错误分支名
        pr_url: 生成的PR链接（如果成功）
        fix_description: 修复描述
        receive_id: 接收者ID
        receive_id_type: 接收者ID类型
        error_message: 失败时的错误信息（可选）

    Returns:
        是否发送成功
    """
    # 构建markdown内容
    status_emoji = "✅" if repair_success else "❌"
    status_text = "修复成功" if repair_success else "修复失败"

    markdown = f"""# 🤖 SpiderClaw 自动修复通知

**状态**: {status_emoji} {status_text}
**错误类型**: {error_type}
**原分支**: {source_branch}

**修复说明**:
{fix_description}
"""

    if repair_success and pr_url:
        markdown += f"\n**PR链接**: 🔗 [查看PR]({pr_url})"

    if not repair_success and error_message:
        markdown += f"\n**错误信息**: ❌ {error_message}"

    markdown += "\n---\n*此通知由 SpiderClaw 自动修复系统生成*"

    return await send_markdown_message(
        receive_id=receive_id,
        markdown_content=markdown,
        receive_id_type=receive_id_type
    )
