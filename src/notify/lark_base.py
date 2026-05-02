"""飞书多维表格客户端
基于lark-cli实现，自动上报修复记录到多维表格
"""
import asyncio
import json
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from collections import defaultdict

from src.notify.lark_notify import send_markdown_message

logger = logging.getLogger(__name__)


class LarkBaseClient:
    """飞书多维表格客户端"""

    # 字段类型映射：数字类型 → lark-cli +field-create 小写字符串类型
    FIELD_TYPE_MAP = {
        1: "text",          # 单行文本
        2: "number",        # 数字
        3: "select",        # 单选
        5: "datetime",      # 日期时间
        15: "url",          # 超链接
    }

    # 修复记录表预定义字段
    REPAIR_TABLE_FIELDS = [
        {
            "field_name": "修复时间",
            "type": 5,  # 日期时间
            "property": {"date_format": "yyyy-MM-dd HH:mm"}
        },
        {
            "field_name": "修复状态",
            "type": 3,  # 单选
            "property": {
                "options": [
                    {"name": "成功", "color": 44},  # 绿色
                    {"name": "失败", "color": 1}   # 红色
                ]
            }
        },
        {"field_name": "仓库名称", "type": 1},  # 单行文本
        {"field_name": "分支名称", "type": 1},  # 单行文本
        {"field_name": "PR作者", "type": 1},  # 单行文本
        {
            "field_name": "原PR链接",
            "type": 15,  # 超链接
            "property": {"open_type": "new_window"}
        },
        {
            "field_name": "修复PR链接",
            "type": 15,  # 超链接
            "property": {"open_type": "new_window"}
        },
        {
            "field_name": "错误类型",
            "type": 3,  # 单选
            "property": {
                "options": [
                    {"name": "SyntaxError", "color": 1},
                    {"name": "ImportError", "color": 2},
                    {"name": "AttributeError", "color": 3},
                    {"name": "TypeError", "color": 4},
                    {"name": "ValueError", "color": 5},
                    {"name": "其他错误", "color": 6}
                ]
            }
        },
        {"field_name": "修复描述", "type": 1},  # 单行文本，长文本用type=1
        {"field_name": "错误信息（失败时）", "type": 1},  # 单行文本
        {"field_name": "修复文件数", "type": 2},  # 数字
        {"field_name": "变更行数", "type": 2},  # 数字
        {"field_name": "修复耗时（秒）", "type": 2},  # 数字
        {"field_name": "重试次数", "type": 2},  # 数字
        {"field_name": "Token消耗", "type": 2},  # 数字
        {"field_name": "相关文件名", "type": 1},  # 单行文本，每行一个文件名
        {
            "field_name": "环境",
            "type": 3,  # 单选
            "property": {
                "options": [
                    {"name": "开发", "color": 7},
                    {"name": "测试", "color": 8},
                    {"name": "生产", "color": 9}
                ]
            }
        }
    ]

    def __init__(
        self,
        base_token: str,
        repair_table_id: str = "",
        as_bot: bool = True,
        max_retry: int = 3,
        check_version: bool = True,
        auto_create_table: bool = True,
        auto_fix_fields: bool = True,
        alert_on_failure: bool = True,
        alert_threshold: int = 3,
        notify_users: List[str] = None,
        notify_groups: List[str] = None,
    ):
        """
        初始化飞书多维表格客户端

        Args:
            base_token: 多维表格token
            repair_table_id: 修复记录表ID，如果为空则会自动创建
            as_bot: 是否以机器人身份操作
            max_retry: 最大重试次数
            check_version: 是否检查lark-cli版本
            auto_create_table: 表不存在时是否自动创建
            auto_fix_fields: 字段缺失时是否自动补全
            alert_on_failure: 上报失败时是否发送告警通知
            alert_threshold: 连续失败多少次后发送告警
            notify_users: 告警接收用户列表
            notify_groups: 告警接收群组列表
        """
        self.base_token = base_token
        self.repair_table_id = repair_table_id
        self.as_user = "bot" if as_bot else "user"
        self.max_retry = max_retry
        self.auto_create_table = auto_create_table
        self.auto_fix_fields = auto_fix_fields
        self.alert_on_failure = alert_on_failure
        self.alert_threshold = alert_threshold
        self.notify_users = notify_users or []
        self.notify_groups = notify_groups or []

        self._field_cache: Dict[str, str] = {}  # 字段名到字段ID的映射
        self._tables: Dict[str, str] = {}  # table_name -> table_id 缓存
        self._version_checked = False
        self._failure_count: Dict[str, int] = defaultdict(int)  # 按错误类型统计连续失败次数
        self._last_alert_time: Dict[str, datetime] = {}  # 按错误类型记录上次告警时间
        self._alert_cooldown = 600  # 相同错误告警冷却时间：10分钟
        self._initialized = False  # 是否已完成初始化（版本校验+字段校验）
        self._last_fields_refresh: Optional[datetime] = None  # 上次字段缓存刷新时间
        self._fields_cache_ttl = 300  # 字段缓存有效期：5分钟

        # 版本和字段校验推迟到第一次上报时执行，避免在初始化时创建异步任务导致问题
        self._check_version = check_version

    async def check_lark_cli_version(self) -> bool:
        """
        检查lark-cli版本是否符合要求（>=1.0.21）

        Returns:
            版本符合要求返回True，否则返回False
        """
        try:
            import sys
            import os
            # Windows环境下使用lark-cli.cmd
            lark_cmd = "lark-cli.cmd" if sys.platform == "win32" else "lark-cli"

            proc = await asyncio.create_subprocess_exec(
                lark_cmd, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                version_output = stdout.decode('utf-8', errors='ignore').strip()
                # 解析版本号，格式可能是"lark-cli/1.0.21"、"1.0.21"或"v1.0.21"
                import re
                match = re.search(r'(\d+\.\d+\.\d+)', version_output)
                if match:
                    version = match.group(1)
                    # 解析版本号为元组比较
                    try:
                        from packaging.version import parse
                        current_version = parse(version)
                        required_version = parse("1.0.21")
                        version_ok = current_version >= required_version
                    except ImportError:
                        # 如果没有packaging模块，直接比较版本字符串（简单版本比较，可能不够准确但不影响使用）
                        logger.warning("缺少packaging模块，跳过严格版本校验")
                        version_ok = True
                    except Exception as e:
                        logger.warning(f"版本解析失败: {e}，跳过版本校验")
                        version_ok = True

                    if version_ok:
                        logger.info(f"lark-cli版本符合要求: {version}")
                        return True
                    else:
                        logger.error(f"lark-cli版本过低: {version}，需要>=1.0.21，请升级")
                        logger.error("升级命令: npm install -g @larksuite/cli@latest")
                        return False
                else:
                    logger.warning(f"无法解析lark-cli版本号: {version_output}，跳过版本校验")
                    return True
            else:
                error_msg = stderr.decode('utf-8', errors='ignore')
                logger.error(f"获取lark-cli版本失败: {error_msg}")
                logger.error("请先安装lark-cli: npm install -g @larksuite/cli")
                return False

        except Exception as e:
            logger.warning(f"检查lark-cli版本异常: {e}，跳过版本校验")
            return True

    async def _run_command(self, cmd: List[str]) -> Optional[Dict[str, Any]]:
        """
        执行lark-cli命令

        Args:
            cmd: 命令参数列表

        Returns:
            命令执行结果，失败返回None
        """
        try:
            # 构造完整命令
            import sys
            import os
            # Windows环境下使用lark-cli.cmd
            lark_cmd = "lark-cli.cmd" if sys.platform == "win32" else "lark-cli"
            full_cmd = [lark_cmd, "base"] + cmd + [
                "--base-token", self.base_token,
                "--as", self.as_user
            ]

            logger.debug(f"执行lark-cli命令: {' '.join(full_cmd)}")

            try:
                proc = await asyncio.create_subprocess_exec(
                    *full_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
            except FileNotFoundError:
                proc = await asyncio.create_subprocess_shell(
                    " ".join(full_cmd),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                try:
                    result = json.loads(stdout.decode('utf-8', errors='ignore'))
                    logger.debug(f"命令执行成功: {result}")
                    return result
                except json.JSONDecodeError:
                    logger.error(f"命令输出不是JSON格式: {stdout.decode('utf-8', errors='ignore')}")
                    return None
            else:
                error_msg = stderr.decode('utf-8', errors='ignore')
                # 800070003: "no operation produced" — 字段已是目标状态，视为成功
                if "800070003" in error_msg:
                    logger.debug(f"字段已是目标状态，无需修改: {error_msg}")
                    try:
                        return json.loads(error_msg)
                    except json.JSONDecodeError:
                        return {"ok": True, "noop": True}
                logger.error(f"命令执行失败, code={proc.returncode}, error={error_msg}")
                return None

        except Exception as e:
            logger.error(f"执行lark-cli命令异常: {e}", exc_info=True)
            return None

    async def list_tables(self) -> List[Dict[str, Any]]:
        """列出所有数据表"""
        result = await self._run_command(["+table-list"])
        return result.get("data", {}).get("tables", []) if result else []

    async def create_repair_table(self, table_name: str = "修复记录") -> Optional[str]:
        """
        创建或查找修复记录表

        Args:
            table_name: 表名

        Returns:
            表ID，失败返回None
        """
        # 检查表是否已存在
        tables = await self.list_tables()
        for table in tables:
            if table.get("name") == table_name:
                tid = table.get("id") or table.get("table_id")
                self._tables[table_name] = tid
                self.repair_table_id = tid
                logger.info(f"表已存在，名称: {table_name}, ID: {tid}")
                await self._init_fields()
                await self.validate_and_migrate_fields()
                return self.repair_table_id

        # 创建新表
        # 转换字段定义中的数字类型为字符串类型，+table-create 不支持 property
        fields = []
        for f in self.REPAIR_TABLE_FIELDS:
            fd = {"field_name": f["field_name"], "type": self.FIELD_TYPE_MAP.get(f["type"], "text")}
            fields.append(fd)

        cmd = [
            "+table-create",
            "--name", table_name,
            "--fields", json.dumps(fields, ensure_ascii=False)
        ]

        result = await self._run_command(cmd)
        if result and result.get("ok"):
            table_id = result.get("data", {}).get("table", {}).get("id")
            self._tables[table_name] = table_id
            self.repair_table_id = table_id
            logger.info(f"表创建成功，名称: {table_name}, ID: {table_id}")
            await self._init_fields()
            # 更新日期时间字段格式（+table-create 不支持 style 属性）
            await self._fix_datetime_field_format()
            return table_id
        else:
            logger.error(f"表创建失败: {table_name}")
            return None

    async def _init_fields(self) -> None:
        """初始化字段缓存，获取所有字段的ID映射"""
        if not self.repair_table_id:
            return

        result = await self._run_command([
            "+field-list",
            "--table-id", self.repair_table_id
        ])

        # lark-cli 返回结构: {"ok": true, "data": {"fields": [...], "total": N}}
        if result and result.get("ok"):
            fields = result.get("data", {}).get("fields", [])
            self._field_cache = {field["name"]: field["id"] for field in fields}
            logger.debug(f"字段缓存初始化完成: {self._field_cache}")

    async def _fix_datetime_field_format(self) -> None:
        """修复日期时间字段的格式，确保显示时分秒"""
        if not self.repair_table_id:
            return

        # 找到日期时间字段定义
        datetime_fields = [f for f in self.REPAIR_TABLE_FIELDS if f["type"] == 5]
        if not datetime_fields:
            return

        for field_def in datetime_fields:
            field_name = field_def["field_name"]
            field_id = self._field_cache.get(field_name)
            if not field_id:
                continue

            # 获取期望的格式
            expected_format = field_def.get("property", {}).get("date_format", "yyyy-MM-dd HH:mm")

            # 更新字段格式
            field_json = {
                "name": field_name,
                "type": "datetime",
                "style": {"format": expected_format}
            }

            cmd = [
                "+field-update",
                "--table-id", self.repair_table_id,
                "--field-id", field_id,
                "--json", json.dumps(field_json, ensure_ascii=False),
            ]

            result = await self._run_command(cmd)
            if result and result.get("ok"):
                logger.info(f"✅ 日期时间字段格式已更新: {field_name} -> {expected_format}")
            else:
                logger.warning(f"⚠️ 更新日期时间字段格式失败: {field_name}")

    async def validate_and_migrate_fields(self, auto_fix: bool = True) -> bool:
        """
        校验字段完整性，自动创建缺失的字段

        Args:
            auto_fix: 是否自动修复缺失的字段

        Returns:
            校验通过或修复成功返回True，否则返回False
        """
        if not self.repair_table_id:
            logger.warning("修复记录表ID为空，无法校验字段")
            return False

        # 先初始化字段缓存
        await self._init_fields()
        existing_fields = set(self._field_cache.keys())
        expected_fields = {field["field_name"] for field in self.REPAIR_TABLE_FIELDS}
        missing_fields = expected_fields - existing_fields

        if not missing_fields:
            logger.info("✅ 多维表格字段校验通过，所有字段都存在")
            # 确保日期时间字段格式正确
            await self._fix_datetime_field_format()
            return True

        logger.warning(f"⚠️ 发现缺失字段: {', '.join(missing_fields)}")

        if not auto_fix:
            logger.warning("自动修复已关闭，请手动创建缺失字段")
            return False

        logger.info("开始自动创建缺失字段...")
        all_created = True
        permission_error = False

        # 创建每个缺失的字段
        for field_def in self.REPAIR_TABLE_FIELDS:
            field_name = field_def["field_name"]
            if field_name not in missing_fields:
                continue

            try:
                # 构造创建字段命令
                # +field-create --json 格式：name 为键，type 为小写字符串，options/date_format 在顶层
                field_json = {
                    "name": field_name,
                    "type": self.FIELD_TYPE_MAP.get(field_def["type"], "text"),
                }

                # 添加字段属性
                if "property" in field_def:
                    # 单选类型的options（直接放在顶层）
                    if field_def["type"] == 3 and "options" in field_def["property"]:
                        options = []
                        for opt in field_def["property"]["options"]:
                            option = {"name": opt["name"]}
                            if "color" in opt:
                                option["color"] = opt["color"]
                            options.append(option)
                        field_json["options"] = options

                    # 日期时间字段的格式（放在 style 中）
                    if field_def["type"] == 5 and "property" in field_def:
                        date_fmt = field_def["property"].get("date_format", "yyyy-MM-dd HH:mm")
                        field_json["style"] = {"format": date_fmt}

                cmd = [
                    "+field-create",
                    "--table-id", self.repair_table_id,
                    "--json", json.dumps(field_json, ensure_ascii=False),
                ]

                result = await self._run_command(cmd)
                if result and result.get("ok"):
                    logger.info(f"✅ 成功创建字段: {field_name}")
                else:
                    # 检查是否是权限错误
                    if result and "error" in result and "base:field:create" in str(result["error"]):
                        permission_error = True
                        logger.error(f"❌ 创建字段失败: {field_name}，应用缺少base:field:create权限")
                        logger.error("请访问以下链接为应用添加权限: https://open.feishu.cn/app/cli_a964adeb53211bcb/auth?q=base:field:create")
                        break  # 权限错误，不再继续尝试创建其他字段
                    else:
                        logger.error(f"❌ 创建字段失败: {field_name}")
                        all_created = False

            except Exception as e:
                logger.error(f"❌ 创建字段 {field_name} 异常: {e}", exc_info=True)
                all_created = False

        # 权限错误处理
        if permission_error:
            logger.error("⚠️  由于应用缺少字段创建权限，自动补全失败，请手动添加权限或手动创建字段")
            logger.error("手动创建字段说明: https://open.feishu.cn/document/server-docs/docs/base/field/create")
            self.auto_fix_fields = False  # 关闭自动补全，避免下次重试
            return False

        # 重新初始化字段缓存
        await self._init_fields()

        if all_created:
            logger.info("✅ 所有缺失字段创建完成")
            return True
        else:
            logger.error("❌ 部分字段创建失败，请手动检查")
            logger.error("⚠️  已自动关闭自动补全功能，避免重复尝试")
            self.auto_fix_fields = False  # 关闭自动补全，避免下次重试
            return False

    async def _get_field_id(self, field_name: str) -> Optional[str]:
        """获取字段ID，缓存，带有效期的缓存机制"""
        now = datetime.now()

        # 缓存存在且未过期，直接返回
        if field_name in self._field_cache:
            if self._last_fields_refresh and (now - self._last_fields_refresh).total_seconds() < self._fields_cache_ttl:
                return self._field_cache[field_name]

        # 缓存过期或字段不存在，刷新缓存
        await self._init_fields()
        self._last_fields_refresh = now

        return self._field_cache.get(field_name)

    async def _lazy_init(self) -> None:
        """延迟初始化：第一次上报时执行版本校验和字段校验"""
        if self._initialized:
            return

        try:
            # 版本校验
            if self._check_version:
                await self.check_lark_cli_version()

            # 如果指定了表ID，校验字段
            if self.repair_table_id and self.auto_fix_fields:
                await self.validate_and_migrate_fields(auto_fix=self.auto_fix_fields)

            self._initialized = True
            logger.info("飞书多维表格客户端延迟初始化完成")
        except Exception as e:
            logger.warning(f"延迟初始化失败: {e}，不影响主流程")

    async def report_repair_record(
        self,
        error_type: str,
        repo_name: str,
        branch_name: str,
        pr_author: str,
        original_pr_url: str,
        fix_pr_url: str,
        repair_success: bool,
        fix_description: str,
        error_message: str = "",
        file_count: int = 0,
        change_lines: int = 0,
        repair_duration: float = 0,
        retry_count: int = 0,
        token_usage: int = 0,
        related_files: List[str] = None,
        environment: str = "开发",
        table_name: str = "修复记录",
    ) -> bool:
        """
        上报修复记录到多维表格

        Args:
            error_type: 错误类型
            repo_name: 仓库名称
            branch_name: 分支名称
            pr_author: PR作者
            original_pr_url: 原PR链接
            fix_pr_url: 修复PR链接
            repair_success: 修复是否成功
            fix_description: 修复描述
            error_message: 错误信息（失败时）
            file_count: 修复文件数
            change_lines: 变更行数
            repair_duration: 修复耗时（秒）
            retry_count: 重试次数
            token_usage: Token消耗
            related_files: 修复涉及的文件名列表
            environment: 运行环境
            table_name: 目标表名，不同修复源使用不同表

        Returns:
            是否上报成功
        """
        if not self.base_token:
            logger.warning("未配置多维表格token，跳过上报")
            return False

        # 解析表ID：从缓存获取或自动创建
        if table_name not in self._tables:
            logger.info(f"表 {table_name} 未缓存，尝试查找或创建")
            table_id = await self.create_repair_table(table_name)
            if not table_id:
                logger.error(f"表 {table_name} 不存在且创建失败，无法上报")
                return False
        self.repair_table_id = self._tables[table_name]

        # 延迟初始化（版本校验 + 字段校验）
        await self._lazy_init()

        # 构造记录数据
        fields = {}

        # 修复时间（飞书 date 类型字段要求毫秒级时间戳）
        repair_time = int(datetime.now().timestamp() * 1000)
        fields["修复时间"] = repair_time

        # 错误类型：标准化处理
        standard_error_types = {"SyntaxError", "ImportError", "AttributeError", "TypeError", "ValueError"}
        display_error_type = error_type if error_type in standard_error_types else "其他错误"
        fields["错误类型"] = display_error_type

        # 基础信息
        fields["仓库名称"] = repo_name
        fields["分支名称"] = branch_name
        fields["PR作者"] = pr_author

        # PR链接（直接传URL字符串）
        if original_pr_url:
            fields["原PR链接"] = original_pr_url
        if fix_pr_url:
            fields["修复PR链接"] = fix_pr_url

        # 修复状态
        fields["修复状态"] = "成功" if repair_success else "失败"

        # 修复描述（截断避免过长）
        if fix_description:
            fields["修复描述"] = fix_description[:500] + ("..." if len(fix_description) > 500 else "")

        # 错误信息
        if error_message:
            fields["错误信息（失败时）"] = error_message[:1000] + ("..." if len(error_message) > 1000 else "")

        # 数字字段
        fields["修复文件数"] = file_count
        fields["变更行数"] = change_lines
        fields["修复耗时（秒）"] = round(repair_duration, 2) if repair_duration else 0
        fields["重试次数"] = retry_count
        fields["Token消耗"] = token_usage

        # 相关文件名（每行一个文件名）
        if related_files:
            fields["相关文件名"] = "\n".join(related_files)

        # 环境
        fields["环境"] = environment

        # 直接使用字段名作为key（+record-upsert 支持 Map<FieldNameOrID, CellValue>）
        # 对比使用字段ID更可靠，避免缓存失效或表重建导致ID不匹配
        if not fields:
            logger.error("没有有效的字段数据，无法上报")
            return False

        # 执行上报，支持重试
        for retry in range(self.max_retry):
            result = await self._run_command([
                "+record-upsert",
                "--table-id", self.repair_table_id,
                "--json", json.dumps(fields, ensure_ascii=False)
            ])

            if result and result.get("ok"):
                logger.info("修复记录上报成功")
                # 上报成功，重置失败计数
                for error_type in list(self._failure_count.keys()):
                    self._failure_count[error_type] = 0
                return True
            else:
                logger.warning(f"修复记录上报失败，重试 {retry + 1}/{self.max_retry}")
                await asyncio.sleep(1)  # 重试间隔

        logger.error(f"修复记录上报失败，已重试{self.max_retry}次")

        # 上报失败，触发告警逻辑
        if self.alert_on_failure:
            await self._on_report_failure(error_type="上报失败", error_msg=f"连续重试{self.max_retry}次仍失败")

        return False

    async def _on_report_failure(self, error_type: str, error_msg: str) -> None:
        """
        上报失败回调，发送告警通知

        Args:
            error_type: 错误类型
            error_msg: 错误详情
        """
        try:
            # 统计连续失败次数
            self._failure_count[error_type] += 1
            failure_count = self._failure_count[error_type]

            # 未达到告警阈值，不发送告警
            if failure_count < self.alert_threshold:
                logger.debug(f"上报失败次数未达到阈值({failure_count}/{self.alert_threshold})，暂不发送告警")
                return

            # 检查告警冷却时间
            now = datetime.now()
            last_alert_time = self._last_alert_time.get(error_type)
            if last_alert_time and (now - last_alert_time).total_seconds() < self._alert_cooldown:
                logger.debug(f"相同错误类型的告警处于冷却期，跳过发送")
                return

            # 没有配置接收人，不发送告警
            if not self.notify_users and not self.notify_groups:
                logger.warning("未配置告警接收人，无法发送告警通知")
                return

            # 构造告警消息
            alert_title = "🚨 飞书多维表格上报失败告警"
            alert_content = f"""
**告警类型**: {error_type}
**错误详情**: {error_msg}
**连续失败次数**: {failure_count}
**base_token**: {self.base_token[:10]}...
**表ID**: {self.repair_table_id}
**告警时间**: {now.strftime("%Y-%m-%d %H:%M:%S")}

请检查多维表格配置和网络连接，确保上报功能恢复正常。
"""

            # 发送告警通知
            logger.info(f"发送上报失败告警通知，接收人: {self.notify_users}, 接收群组: {self.notify_groups}")
            await send_markdown_message(
                title=alert_title,
                content=alert_content,
                user_ids=self.notify_users,
                chat_ids=self.notify_groups
            )

            # 更新告警时间和重置失败计数
            self._last_alert_time[error_type] = now
            self._failure_count[error_type] = 0  # 重置失败计数，避免重复告警

        except Exception as e:
            logger.error(f"发送告警通知失败: {e}", exc_info=True)


# 全局客户端实例
_lark_base_client: Optional[LarkBaseClient] = None


def init_lark_base(
    base_token: str,
    repair_table_id: str = "",
    as_bot: bool = True,
    check_version: bool = True,
    auto_create_table: bool = True,
    auto_fix_fields: bool = True,
    alert_on_failure: bool = True,
    alert_threshold: int = 3,
    notify_users: List[str] = None,
    notify_groups: List[str] = None,
) -> None:
    """初始化全局飞书多维表格客户端"""
    global _lark_base_client
    _lark_base_client = LarkBaseClient(
        base_token,
        repair_table_id,
        as_bot,
        check_version=check_version,
        auto_create_table=auto_create_table,
        auto_fix_fields=auto_fix_fields,
        alert_on_failure=alert_on_failure,
        alert_threshold=alert_threshold,
        notify_users=notify_users,
        notify_groups=notify_groups
    )
    logger.info("飞书多维表格客户端初始化完成")


def get_table_url(base_token: str, table_name: str) -> str:
    """获取飞书多维表格指定表的直接访问URL（含 ?table= 参数）

    如果表ID未缓存，返回 base 层级 URL 作为降级。
    """
    base_url = f"https://my.feishu.cn/base/{base_token}"
    global _lark_base_client
    if _lark_base_client and table_name in _lark_base_client._tables:
        table_id = _lark_base_client._tables[table_name]
        return f"{base_url}?table={table_id}"
    return base_url


async def ensure_table_ready(table_name: str) -> str | None:
    """确保表已缓存，返回表ID（供通知提前解析 table_id）"""
    global _lark_base_client
    if not _lark_base_client:
        return None
    if table_name not in _lark_base_client._tables:
        return await _lark_base_client.create_repair_table(table_name)
    return _lark_base_client._tables[table_name]


async def report_repair_record(**kwargs) -> bool:
    """上报修复记录的便捷函数"""
    if not _lark_base_client:
        logger.warning("飞书多维表格客户端未初始化，跳过上报")
        return False

    try:
        return await _lark_base_client.report_repair_record(**kwargs)
    except Exception as e:
        logger.error(f"上报修复记录异常: {e}", exc_info=True)
        return False
