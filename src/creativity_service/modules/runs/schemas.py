"""运行传输契约与仅供服务端解析器使用的冻结定义。"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, JsonValue, model_validator

from creativity_service.core.context import Scope
from creativity_service.core.contracts import RunError, RunState
from creativity_service.core.primitives import Contract, Identifier, RunInput
from creativity_service.modules.agents.schemas import FrozenExecutionSpec
from creativity_service.modules.usage.schemas import AttemptPlan

TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"})
LABELS = {
    "QUEUED": "排队中",
    "RUNNING": "执行中",
    "CANCEL_REQUESTED": "取消中",
    "SUCCEEDED": "已完成",
    "FAILED": "失败",
    "CANCELLED": "已取消",
    "TIMED_OUT": "已超时",
}
TRANSITIONS = {
    "QUEUED": {"RUNNING", "CANCELLED", "TIMED_OUT", "FAILED"},
    "RUNNING": {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCEL_REQUESTED"},
    "CANCEL_REQUESTED": {"CANCELLED"},
}


class RunRequest(RunInput):
    """会话调用保留客户端消息标识，由 12 的事务钩子校验和关联。"""

    client_message_id: Identifier | None = None

    def semantic_digest(self) -> str:
        if self.client_message_id is None:
            return RunInput.model_validate(
                self.model_dump(exclude={"client_message_id"})
            ).semantic_digest()
        return super().semantic_digest()


class StepPolicy(Contract):
    node_key: Identifier
    kind: Literal["model", "tool", "compute"]
    target_version_id: Identifier
    read_only: bool = True
    max_retries: int = Field(default=2, ge=0, le=2)


class ExecutionPolicy(Contract):
    frozen_spec_id: Identifier | None = None
    steps: tuple[StepPolicy, ...] = Field(min_length=1)
    workload: Literal["online", "analysis"] = "online"
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    timeout_source: str = Field(default="platform-default", min_length=1, max_length=128)
    max_model_calls: int = Field(default=6, ge=1, le=100)
    max_tool_calls: int = Field(default=10, ge=0, le=100)
    max_recoveries: int = Field(default=2, ge=0, le=10)

    @model_validator(mode="after")
    def distinct_nodes(self) -> "ExecutionPolicy":
        if len({s.node_key for s in self.steps}) != len(self.steps):
            raise ValueError("执行节点不能重复")
        return self

    @property
    def deadline_seconds(self) -> int:
        return self.timeout_seconds or (300 if self.workload == "analysis" else 60)


class ResolvedDefinition(Contract):
    """只由 16/17 的受信解析器返回；HTTP 不能提交此结构。"""

    agent_id: Identifier
    agent_name: str = Field(min_length=1, max_length=128)
    version_ids: tuple[Identifier, ...] = Field(min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    policy: ExecutionPolicy
    purpose: Literal["production", "debug", "evaluation"] = "production"
    admission_plan: AttemptPlan | None = None
    frozen_spec: FrozenExecutionSpec | None = None


class AdmissionReceipt(Contract):
    run_id: str
    state: RunState
    state_label: str
    created_at: datetime
    deadline: datetime
    status_url: str
    events_url: str


class Lease(Contract):
    scope: Scope
    run_id: Identifier
    worker_id: Annotated[str, Field(min_length=1, max_length=128)]
    lease_version: int = Field(ge=1)
    expires_at: datetime


class RunSummary(AdmissionReceipt):
    name: str
    parent_run_id: str | None
    release_snapshot_id: str
    error: RunError | None


class TracePage(Contract):
    items: list[dict[str, JsonValue]]
    next_sequence: int | None


class RerunInput(Contract):
    input: dict[str, JsonValue] | None = None


__all__ = [
    "RunInput",
    "RunRequest",
    "AdmissionReceipt",
    "Lease",
    "ResolvedDefinition",
    "ExecutionPolicy",
    "StepPolicy",
    "RunSummary",
    "TracePage",
    "RerunInput",
]
