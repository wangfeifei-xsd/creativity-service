"""事务内审计只收录已定义的无原文诊断字段。"""

from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, UnitOfWork
from creativity_service.core.database.tables import metadata


async def append_audit(
    uow: UnitOfWork,
    context: AuthContext,
    event_id: str,
    action: str,
    target_type: str,
    target_id: str,
    summary: dict[str, Any] | None = None,
    *,
    changed_fields: tuple[str, ...] = (),
) -> None:
    allowed = {"previous_version_id", "version_id", "revision", "content_digest", "state"}
    if set(summary or {}) - allowed:
        raise ValueError("审计摘要包含未登记字段")
    if any(not isinstance(value, (str, int, type(None))) for value in (summary or {}).values()):
        raise ValueError("审计摘要只允许标识及修订值")
    if set(changed_fields) - {"name", "purpose", "description", "owner", "status"}:
        raise ValueError("审计变更字段未登记")
    await Repository(metadata.tables["audit_events"], context.scope).add(
        uow,
        event_id,
        {
            "actor_id": context.principal_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "request_id": context.request_id,
            "outcome": "SUCCEEDED",
            "summary": {
                **(summary or {}),
                **({"changed_fields": list(changed_fields)} if changed_fields else {}),
            },
        },
    )
