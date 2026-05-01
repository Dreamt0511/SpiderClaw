"""事件数据模型定义"""
from datetime import datetime
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field, ConfigDict


class BaseEvent(BaseModel):
    """基础事件模型"""
    event_id: str = Field(description="事件唯一ID")
    event_type: str = Field(description="事件类型")
    timestamp: datetime = Field(default_factory=datetime.now, description="事件生成时间")
    source: str = Field(description="事件来源")
    payload: Dict[str, Any] = Field(default_factory=dict, description="事件原始数据")


class GitHubEvent(BaseEvent):
    """GitHub Webhook事件模型"""
    action: str = Field(description="事件动作")
    repository: str = Field(description="仓库全名（owner/repo）")
    signature_valid: bool = Field(description="签名是否验证通过")

    # 衍生字段，Agent直接使用
    clone_url: str = Field(default="", description="仓库克隆地址")
    branch: str = Field(default="", description="分支名")
    pr_number: Optional[int] = Field(default=None, description="PR编号")
    logs_url: str = Field(default="", description="CI日志下载链接")
    conclusion: str = Field(default="", description="执行结果（success/failure等）")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "event_id": "a1b2c3d4-1234-5678-90ab-cdef01234567",
                "event_type": "workflow_run",
                "action": "completed",
                "timestamp": "2024-04-24T12:00:00Z",
                "source": "github_webhook",
                "repository": "owner/repo",
                "signature_valid": True,
                "clone_url": "https://github.com/owner/repo.git",
                "branch": "main",
                "pr_number": 123,
                "logs_url": "https://github.com/owner/repo/actions/runs/123/logs",
                "conclusion": "failure",
                "payload": {}
            }
        }
    )


class RuntimeLogEvent(BaseEvent):
    """运行时日志事件 — 由 /webhook/log 端点创建

    独立事件类型，不伪装为 GitHubEvent。
    下游节点通过 event.event_type == "runtime_log" 判断。
    """
    event_type: str = "runtime_log"

    # === 运行时日志自有字段 ===
    log: str = ""
    service: str = ""
    version: str = ""
    hostname: str = ""

    # === 由 Agent 端从 services.yaml 查到后填充 ===
    repo_url: str = ""
    repo_local_path: str = ""
    branch: str = ""
    path_mapping: Dict[str, str] = Field(default_factory=dict)

    # === 仅供下游节点兼容访问 ===
    repository: str = ""
    clone_url: str = ""
    action: str = ""
    signature_valid: bool = True
