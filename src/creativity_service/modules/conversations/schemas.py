"""会话接口不接受渠道、身份范围或客户端指定的执行版本。"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from creativity_service.core.contracts import BusinessResult, VisibleAction
from creativity_service.core.primitives import Contract, Identifier, Page, Revision, digest
from creativity_service.modules.runs.schemas import AdmissionReceipt, RunRequest

STATES = {"ACTIVE": "使用中", "ARCHIVED": "已归档", "DELETING": "删除中", "DELETED": "已删除"}
ROLES = {"user": "用户", "assistant": "助手", "system": "系统", "tool": "工具"}
MESSAGE_STATES = {
    "PENDING": "等待处理",
    "PARTIAL": "未完成",
    "COMPLETED": "已完成",
    "FAILED": "失败",
    "CANCELLED": "已取消",
}


class TextPart(Contract):
    type: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=100000)


class AttachmentPart(Contract):
    type: Literal["attachment"] = "attachment"
    artifact_id: Identifier


ContentPart = Annotated[TextPart | AttachmentPart, Field(discriminator="type")]


class ConversationCreate(Contract):
    agent_code: Identifier
    title: str = Field(default="未命名会话", min_length=1, max_length=255)


class TitleInput(Contract):
    title: str = Field(min_length=1, max_length=255)
    revision: Revision


class MessageInput(Contract):
    client_message_id: Identifier
    content: str = Field(min_length=1, max_length=100000)
    input: dict[str, Any] | None = None
    attachments: list[AttachmentPart] = Field(default_factory=list, max_length=10)
    agent_code: Identifier | None = None


class ConversationRunRequest(RunRequest):
    """由会话服务组装的受理参数，运行 HTTP 接口不能直接提交此类型。"""

    content_parts: tuple[ContentPart, ...] = ()

    def semantic_digest(self) -> str:
        return digest(
            {
                "algorithm": "conversation-request-v1",
                **self.model_dump(mode="json", exclude={"delivery"}),
            }
        )


class AgentChoice(Contract):
    agent_code: str
    name: str


class ConversationView(Contract):
    conversation_id: str
    title: str
    agent_name: str
    agent_id: str
    subject_name: str | None
    environment: str
    environment_label: str
    status: str
    status_label: str
    revision: int
    active_run_id: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    actions: list[VisibleAction]


class ConversationList(Page[ConversationView]):
    actions: list[VisibleAction]
    agents: list[AgentChoice]
    unavailable_reason: str | None


class AttachmentView(Contract):
    artifact_id: str
    name: str
    size_bytes: int
    download_path: str


class MessageView(Contract):
    message_id: str
    turn_id: str | None
    run_id: str | None
    sequence: int
    role: str
    role_label: str
    status: str
    status_label: str
    text: str
    attachments: list[AttachmentView]
    created_at: datetime


class TurnView(Contract):
    turn_id: str
    sequence: int
    run_id: str
    version_label: str
    state: str
    state_label: str
    result: BusinessResult | None
    result_label: str | None
    output_schema: dict[str, Any]
    source_run_id: str | None
    duration_seconds: float | None
    can_cancel: bool
    can_view_run: bool


class MessagePage(Page[MessageView]):
    turns: list[TurnView]


class SummaryView(Contract):
    summary_id: str
    version: int
    content: str
    source_message_ids: list[str]
    source_sequences: list[int]
    truncation: dict[str, Any]
    created_at: datetime


class ContextView(Contract):
    run_id: str
    included_message_ids: list[str]
    summary_version: str | None
    truncation: dict[str, Any]


class ConversationDetail(Contract):
    conversation: ConversationView
    summaries: list[SummaryView]
    contexts: list[ContextView]
    input_schema: dict[str, Any]
    executable: bool
    unavailable_reason: str | None


class MessageReceipt(Contract):
    turn_id: str
    user_message_id: str
    assistant_message_id: str
    sequence: int
    run: AdmissionReceipt


class DeletionImpact(Contract):
    messages: int
    summaries: int
    runs: int
    exclusive_memories: int
    shared_memories: int
    explanation: str


class DeletionView(Contract):
    deletion_id: str
    conversation_id: str
    status: str
    status_label: str
    requested_at: datetime
    completed_at: datetime | None


class SelectedContext(Contract):
    run_id: str
    instructions: str
    current_input: dict[str, Any]
    current_content_parts: list[dict[str, Any]]
    source_run_id: str | None
    confirmed_conditions: dict[str, Any]
    followup_result: BusinessResult | None
    messages: list[dict[str, Any]]
    summary: str | None
    included_message_ids: list[str]
    truncation: dict[str, Any]
