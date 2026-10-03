"""删除预览与进度只返回数量和状态，不返回被删原文。"""

from typing import Literal

from pydantic import Field

from creativity_service.core.primitives import Contract, Identifier


class LifecycleTarget(Contract):
    resource_type: Literal[
        "conversation",
        "message",
        "memory",
        "run",
        "tool_call",
        "artifact",
        "schedule",
        "batch",
        "batch_item",
        "webhook_endpoint",
        "webhook_delivery",
        "alert_rule",
        "provider_statement",
        "oauth_token",
    ]
    resource_id: Identifier


class CleanupCount(Contract):
    resource_type: str
    label: str
    count: int = Field(ge=0)


class LifecycleImpact(Contract):
    resources: list[CleanupCount]
    shared_memories: int
    explanation: str


class CleanupStep(Contract):
    label: str
    completed: int
    total: int
    failed: int


class LifecycleProgress(Contract):
    deletion_id: Identifier
    status: Literal["PENDING", "FAILED", "COMPLETED"]
    status_label: str
    completed: int
    total: int
    steps: list[CleanupStep]
    proof_digests: list[str]
