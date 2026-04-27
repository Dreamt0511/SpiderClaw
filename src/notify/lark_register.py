"""飞书应用一键注册工具
基于飞书OAuth 2.0 Device Authorization Grant协议实现
无需手动访问开发者后台，用户扫码授权后自动获取应用凭据
"""
import asyncio
import logging
import webbrowser
from typing import Optional, Dict
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://accounts.feishu.cn"
_ENDPOINT = "/oauth/v1/app/registration"


async def register_lark_app() -> Optional[Dict[str, str]]:
    """
    一键注册飞书企业自建应用

    Returns:
        注册成功返回包含app_id和app_secret的字典，失败返回None
    """
    from rich.console import Console
    from rich.panel import Panel
    _console = Console()

    async with httpx.AsyncClient(timeout=60) as client:

        async def _post(data: dict) -> dict:
            resp = await client.post(
                _BASE_URL + _ENDPOINT,
                content=urlencode(data),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            return resp.json()

        try:
            logger.info("开始注册飞书应用...")

            # Step 1: init - 获取支持的认证方式
            init_res = await _post({"action": "init"})
            methods = init_res.get("supported_auth_methods") or []
            if "client_secret" not in methods:
                logger.error("飞书不支持 client_secret 认证方式")
                return None

            # Step 2: begin - 获取设备授权码和二维码链接
            begin_res = await _post({
                "action": "begin",
                "archetype": "PersonalAgent",
                "auth_method": "client_secret",
                "request_user_info": "open_id",
            })

            device_code = begin_res["device_code"]
            interval = begin_res.get("interval", 5)
            expire_in = begin_res.get("expires_in", 600)
            verify_uri = begin_res["verification_uri_complete"]

            # 打开浏览器显示飞书官方二维码页面
            webbrowser.open(verify_uri)
            _console.print(Panel(
                f"浏览器已打开，请使用飞书APP扫描页面中的二维码完成授权\n\n"
                f"如果浏览器未自动打开，请手动访问：\n{verify_uri}\n\n"
                f"链接有效期: {expire_in // 60} 分钟",
                title="飞书应用一键注册",
                border_style="white"
            ))
            _console.print("等待授权...\n")

            # Step 3: 轮询授权状态
            deadline = asyncio.get_event_loop().time() + expire_in
            domain_switched = False
            base_url = _BASE_URL
            poll_timeout_retries = 0

            while asyncio.get_event_loop().time() < deadline:
                try:
                    poll_res = await _post({"action": "poll", "device_code": device_code})
                except httpx.TimeoutException:
                    poll_timeout_retries += 1
                    if poll_timeout_retries > 3:
                        logger.error("轮询多次超时，建议设置 HTTPS_PROXY 环境变量后重试")
                        return None
                    await asyncio.sleep(interval)
                    continue

                data = poll_res
                poll_timeout_retries = 0  # 成功后重置重试计数

                # 授权成功
                if data.get("client_id") and data.get("client_secret"):
                    app_id = data["client_id"]
                    app_secret = data["client_secret"]

                    _console.print(Panel(
                        f"应用凭据\n"
                        f"App ID: {app_id}\n"
                        f"App Secret: {app_secret}\n\n"
                        f"请将以上凭据配置到 src/config/agent-config.yaml 中",
                        title="授权成功",
                        border_style="white"
                    ))

                    logger.info(f"飞书应用注册成功: {app_id}")
                    return {"app_id": app_id, "app_secret": app_secret}

                # 域名切换（飞书/Lark国际版）
                user_info = data.get("user_info") or {}
                if user_info.get("tenant_brand") == "lark" and not domain_switched:
                    base_url = "https://accounts.larksuite.com"
                    domain_switched = True
                    continue

                error = data.get("error", "")

                if error == "authorization_pending":
                    await asyncio.sleep(interval)
                    continue

                if error == "slow_down":
                    interval += 5
                    await asyncio.sleep(interval)
                    continue

                if error == "access_denied":
                    logger.error("用户拒绝授权")
                    return None

                if error == "expired_token":
                    logger.error("授权超时，请重新尝试")
                    return None

                # 其他错误
                error_desc = data.get("error_description", "未知错误")
                logger.error(f"注册失败: {error} - {error_desc}")
                return None

            logger.error("授权超时，请重新尝试")
            return None

        except httpx.TimeoutException:
            logger.error("连接飞书服务器超时，请检查网络连接和代理设置（HTTP_PROXY/HTTPS_PROXY）")
            return None
        except Exception as e:
            logger.error(f"飞书应用注册异常: {e}", exc_info=True)
            return None


def register_lark_app_sync(**kwargs) -> Optional[Dict[str, str]]:
    """同步版本的注册方法，用于CLI调用"""
    return asyncio.run(register_lark_app(**kwargs))
