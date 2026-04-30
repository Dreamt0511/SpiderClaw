"""配置校验模块
提供系统启动前的配置校验功能，确保配置完整性和有效性
"""
import logging
from typing import Optional
from pydantic import ValidationError

from src.config.settings import get_settings, Settings

logger = logging.getLogger(__name__)


def validate_config(settings: Optional[Settings] = None) -> bool:
    """
    校验全局配置是否完整有效

    Args:
        settings: 配置实例，为空则自动加载

    Returns:
        校验通过返回True，失败返回False
    """
    if settings is None:
        settings = get_settings()

    logger.info("开始校验系统配置...")
    all_passed = True

    # 校验Webhook配置
    if not validate_webhook_config(settings):
        all_passed = False

    # 校验Agent配置
    if not validate_agent_config(settings):
        all_passed = False

    # 校验飞书配置
    if not validate_lark_config(settings):
        all_passed = False

    if all_passed:
        logger.info("✅ 所有配置校验通过")
    else:
        logger.error("❌ 部分配置校验失败，请检查配置文件")

    return all_passed


def validate_webhook_config(settings: Settings) -> bool:
    """校验Webhook相关配置"""
    if not settings.webhook.secret:
        logger.warning("Webhook secret未配置，将无法验证Webhook请求签名")

    return True


def validate_agent_config(settings: Settings) -> bool:
    """校验Agent相关配置"""
    if settings.agent.enabled:
        if not settings.openai.api_key:
            logger.error("Agent已启用但OpenAI API Key未配置")
            return False

        if not settings.github.token:
            logger.warning("GitHub Token未配置，将无法创建PR和访问仓库")

    return True


def validate_lark_config(settings: Settings) -> bool:
    """校验飞书相关配置"""
    if settings.lark.enabled:
        # 基础通知配置
        if not settings.lark.notify_users and not settings.lark.notify_groups:
            logger.warning("飞书通知已启用但未配置通知用户或群组，将无法发送通知")

        # 多维表格配置
        if settings.lark.base_enabled:
            if not settings.lark.base_token:
                logger.error("飞书多维表格已启用但base_token未配置")
                return False

            # 检查表ID配置，为空时允许自动创建
            if not settings.lark.repair_table_id:
                logger.info("飞书多维表格repair_table_id未配置，系统将自动创建修复记录表")

    return True


def validate_lark_base_token(base_token: str) -> bool:
    """
    校验飞书多维表格token是否有效

    Args:
        base_token: 多维表格token

    Returns:
        token有效返回True，否则返回False
    """
    if not base_token:
        return False

    # 简单格式校验：base_token通常以"bascn"开头
    if not base_token.startswith(("bascn", "bas", "u")):
        logger.warning("飞书多维表格base_token格式可能不正确，建议检查配置")

    return True
