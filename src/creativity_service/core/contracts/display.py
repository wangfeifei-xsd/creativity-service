"""服务端组装名称与状态，前端不从编码猜测可读信息。"""

from typing import Protocol

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import DisplayStatus, VersionOption
from creativity_service.core.primitives import ServiceError

STATUS_LABELS = {
    "DRAFT": ("草稿", "default"),
    "PUBLISHED": ("已发布", "success"),
    "RETIRED": ("已退役", "default"),
    "QUEUED": ("排队中", "default"),
    "RUNNING": ("执行中", "processing"),
    "WAITING_INPUT": ("等待补充", "warning"),
    "WAITING_APPROVAL": ("等待审批", "warning"),
    "CANCEL_REQUESTED": ("取消中", "warning"),
    "SUCCEEDED": ("已完成", "success"),
    "FAILED": ("失败", "error"),
    "CANCELLED": ("已取消", "default"),
    "TIMED_OUT": ("已超时", "error"),
}


class NameReader(Protocol):
    async def authorized_name(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> str | None: ...


def display_status(value: str) -> DisplayStatus:
    if value not in STATUS_LABELS:
        raise ServiceError("STATUS_LABEL_MISSING", "状态展示配置缺失", 503)
    label, tone = STATUS_LABELS[value]
    return DisplayStatus.model_validate({"value": value, "label": label, "tone": tone})


async def version_option(
    reader: NameReader,
    context: AuthContext,
    resource_type: str,
    resource_id: str,
    version_id: str,
    version_label: str | None,
    state: str,
    selectable: bool,
) -> VersionOption:
    name = await reader.authorized_name(context, resource_type, resource_id)
    available = bool(name and version_label)
    return VersionOption(
        version_id=version_id,
        resource_name=name,
        version_label=version_label,
        status=display_status(state),
        selectable=selectable and available,
        unavailable_reason=None if available else "版本信息不可用",
    )
