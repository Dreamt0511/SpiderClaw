"""通知服务 — 飞书通知 + PR 正文构建"""

import asyncio
import datetime
import logging
import re
from typing import Any

from src.agent.state import RepairState
from src.notify.lark_notify import send_repair_notification

logger = logging.getLogger(__name__)


class NotificationService:
    """飞书通知服务"""

    def __init__(
        self,
        enabled: bool = False,
        notify_users: list[str] | None = None,
    ):
        self.enabled = enabled
        self.notify_users = notify_users or []

    def send_pr_created(self, state: RepairState, pr_url: str = "") -> None:
        """发送 PR 创建成功通知"""
        if not self.enabled or not self.notify_users:
            return

        error_types = list({
            e.error_type if hasattr(e, 'error_type') else e.get('error_type', 'Unknown')
            for e in state.error_locations
        })
        error_type_str = ', '.join(error_types)

        event = state.event
        pr_author = (
            event.payload.get('sender', {}).get('login', '未知用户')
            if isinstance(event, object) and hasattr(event, 'payload')
            else event.get('payload', {}).get('sender', {}).get('login', '未知用户')
            if isinstance(event, dict)
            else '未知用户'
        )

        bug_count = len(state.modified_files) or len({
            e.file_path if hasattr(e, 'file_path') else e.get('file_path', '')
            for e in state.error_locations
            if (e.file_path if hasattr(e, 'file_path') else e.get('file_path', ''))
        })

        branch = (
            event.branch if isinstance(event, object) and hasattr(event, 'branch')
            else event.get('branch', '') if isinstance(event, dict)
            else ''
        )

        for user_id in self.notify_users:
            # 构造原错误PR链接
            repo = (
                event.repository if isinstance(event, object) and hasattr(event, 'repository')
                else event.get('repository', '') if isinstance(event, dict)
                else ''
            )
            pr_num = (
                event.pr_number if isinstance(event, object) and hasattr(event, 'pr_number')
                else event.get('pr_number') if isinstance(event, dict)
                else None
            )
            original_pr_url = f"https://github.com/{repo}/pull/{pr_num}" if repo and pr_num else ""

            asyncio.create_task(
                send_repair_notification(
                    repair_success=True,
                    error_type=error_type_str,
                    source_branch=branch,
                    pr_url=pr_url,
                    original_pr_url=original_pr_url,
                    fix_description=state.fix_description,
                    receive_id=user_id,
                    receive_id_type="open_id",
                    pr_author=pr_author,
                    bug_count=bug_count,
                )
            )

    def send_failure(self, state: RepairState) -> None:
        """发送修复失败通知"""
        if not self.enabled or not self.notify_users:
            return

        error_types = list({
            e.error_type if hasattr(e, 'error_type') else e.get('error_type', 'Unknown')
            for e in state.error_locations
        })
        error_type_str = ', '.join(error_types) if error_types else 'Unknown'

        event = state.event
        pr_author = (
            event.payload.get('sender', {}).get('login', '未知用户')
            if isinstance(event, object) and hasattr(event, 'payload')
            else event.get('payload', {}).get('sender', {}).get('login', '未知用户')
            if isinstance(event, dict)
            else '未知用户'
        )

        bug_count = len(state.modified_files) or len({
            e.file_path if hasattr(e, 'file_path') else e.get('file_path', '')
            for e in state.error_locations
            if (e.file_path if hasattr(e, 'file_path') else e.get('file_path', ''))
        })

        branch = (
            event.branch if isinstance(event, object) and hasattr(event, 'branch')
            else event.get('branch', '') if isinstance(event, dict)
            else ''
        )

        for user_id in self.notify_users:
            asyncio.create_task(
                send_repair_notification(
                    repair_success=False,
                    error_type=error_type_str,
                    source_branch=branch,
                    pr_url="",
                    fix_description=state.fix_description or '修复失败',
                    receive_id=user_id,
                    receive_id_type="open_id",
                    error_message=state.error_message,
                    pr_author=pr_author,
                    bug_count=bug_count,
                )
            )

    @staticmethod
    def build_pr_body(state: RepairState, branch_name: str) -> str:
        """构建 PR 正文"""
        event = state.event

        # 兼容 dict 和 GitHubEvent
        def _ev(key: str, default: Any = "") -> Any:
            if isinstance(event, dict):
                return event.get(key, default)
            return getattr(event, key, default)

        pr_author_title = ""
        if isinstance(event, dict):
            pr_author_title = event.get('payload', {}).get('sender', {}).get('login', '未知用户')
        elif hasattr(event, 'payload'):
            pr_author_title = event.payload.get('sender', {}).get('login', '未知用户')

        pr_title = f"[SpiderClaw: fix]：对 {pr_author_title} 的 PR 进行的修复"
        pr_link = f"#{_ev('pr_number')}" if _ev('pr_number') else "无"
        ci_logs_link = f"[链接]({_ev('logs_url')})" if _ev('logs_url') else "无"

        review_status = "✅ 通过" if state.review_passed else "❌ 不通过"

        validation_status = state.validation_status
        validation_method = state.validation_method
        validation_command = state.validation_command

        change_lines = 0
        if state.diff_content:
            adds = len([l for l in state.diff_content.split('\n') if l.startswith('+') and not l.startswith('+++')])
            deletes = len([l for l in state.diff_content.split('\n') if l.startswith('-') and not l.startswith('---')])
            change_lines = adds + deletes

        if validation_status == 'success':
            test_status = "✅ 通过"
            test_detail = f"验证方法: {validation_method}"
            if validation_command:
                test_detail += f" | 命令: {validation_command}"
        elif validation_status == 'uncertain':
            test_status = "⚠️ 不确定"
            test_detail = f"验证方法: {validation_method}，无法完全自动验证修复正确性，请人工确认"
            if validation_command:
                test_detail += f" | 命令: {validation_command}"
        else:
            test_status = "❌ 不通过"
            test_detail = f"验证方法: {validation_method}"
            if validation_command:
                test_detail += f" | 命令: {validation_command}"

        risk_warnings = state.risk_warnings or []
        critical_risks = [r for r in risk_warnings if r.startswith('[严重]') or r.startswith('[CRITICAL]')]
        normal_warnings = [r for r in risk_warnings if not (r.startswith('[严重]') or r.startswith('[CRITICAL]'))]

        risk_section_parts = []
        if critical_risks:
            risk_section_parts.append("### ⚠️ 未解决的严重风险")
            risk_section_parts.extend(f"- {r}" for r in critical_risks)
            risk_section_parts.append("")
        if normal_warnings:
            risk_section_parts.append("### 低风险警告")
            risk_section_parts.extend(f"- {r}" for r in normal_warnings)
            risk_section_parts.append("")

        risk_warning_display = f"{len(risk_warnings)} 条" if risk_warnings else "无"
        risk_detail_section = '\n'.join(risk_section_parts) if risk_section_parts else ""

        error_types = list({
            e.error_type if hasattr(e, 'error_type') else e.get('error_type', 'Unknown')
            for e in state.error_locations
        })
        error_types_str = ', '.join(error_types)

        pr_body_parts = [f"""## 🎯 修复概览
- **系统**: SpiderClaw 自动修复系统
- 原错误分支: `{_ev('branch')}`
- 修复分支: `{branch_name}`
- 错误类型: {error_types_str}
- 修改文件: {len(state.modified_files)} 个
- 变更行数: {change_lines} 行
- 相关PR: {pr_link}
- 原始CI日志: {ci_logs_link}

## ✅ 检查结果
- 代码审查: {review_status}
- 测试验证: {test_status}
- 验证详情: {test_detail}
- 风险警告: {risk_warning_display}
{risk_detail_section}
"""]

        if critical_risks:
            pr_body_parts.insert(0, f"""| 🚨 此 PR 包含未解决的严重风险，请谨慎合并 |
| --- |
| 自动修复尝试 {state.max_retries} 次后仍存在严重风险， |
| **务必人工审查所有变更后再合并**。 |

""")

        if state.risk_level == "HIGH":
            pr_body_parts.insert(0, f"""| 🚨 禁止合并 🚨 |
| --- |
| 此PR包含高危风险（如不安全的子进程调用、硬编码密钥、函数契约破坏等）， |
| 重试 {state.max_retries} 次后仍未能消除。 |
| **强烈建议人工审查所有变更，确认安全后再决定是否合并**。 |

""")

        if validation_status == 'uncertain':
            pr_body_parts.insert(0, f"""| ⚠️ 自动验证结果不确定 |
| --- |
| 原始验证命令「{validation_command or '无'}」无法确定修复正确性。 |
| 此 PR 由系统自动生成，**请人工审查和验证后合并**。 |

""")
            pr_body_parts.append(f"""---
| ⚠️ 如需回退，请使用以下命令重置到 CI 失败前的状态 |
| --- |
| git checkout origin/{_ev('branch')} -- . |
""")

        _fixed_desc = re.sub(
            r'(?<!\n)\n(?=[-*] )', r'\n\n',
            state.fix_description
        )
        pr_body_parts.append(f"""
<details>
<summary>🔍 查看详细变更</summary>

## 修复说明
{_fixed_desc}

## 变更详情
- 修改文件: {', '.join(state.modified_files)}

## 代码Diff
```diff
{state.diff_content}
```
</details>

---
此PR由SpiderClaw自动修复系统生成
""")

        return '\n'.join(pr_body_parts)
