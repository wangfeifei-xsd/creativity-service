"""会话与受控工具证据的本地复核，不从不可见来源补读标题或原文。"""

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, UnitOfWork
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.conversations.tables import metadata as conversations
from creativity_service.modules.memory.ports import SourceState
from creativity_service.modules.memory.schemas import SourceInput
from creativity_service.modules.tools.tables import metadata as tools


class DatabaseSourceReader:
    async def resolve(
        self, uow: UnitOfWork, context: AuthContext, source: SourceInput
    ) -> SourceState | None:
        scope = context.scope
        await DeletionGuard(scope).check(uow, [ContentRef(source.source_type, source.source_id)])
        if source.source_type == "message":
            row = await Repository(conversations.tables["messages"], scope).get(
                uow.connection, source.source_id
            )
            if not row or row["role"] != "user" or str(row["sequence"]) != source.source_version:
                return None
            conversation = await Repository(conversations.tables["conversations"], scope).get(
                uow.connection, row["conversation_id"]
            )
            if (
                not conversation
                or conversation["status"] in {"DELETING", "DELETED"}
                or conversation["expires_at"] <= utcnow()
            ):
                return None
            return SourceState(conversation["title"], "USER", 4, row["created_at"])
        row = await Repository(tools.tables["evidence_refs"], scope).get(
            uow.connection, source.source_id
        )
        if not row or row["source_version"] != source.source_version:
            return None
        calls = await Repository(tools.tables["tool_calls"], scope).find(
            uow.connection, state="SUCCEEDED"
        )
        supporting = [call for call in calls if row["id"] in call["evidence_ids"]]
        if not supporting:
            return None
        valid = False
        for call in supporting:
            try:
                await DeletionGuard(scope).check(uow, [ContentRef("tool_call", call["id"])])
            except ServiceError as exc:
                if exc.code != "CONTENT_DELETED":
                    raise
            else:
                valid = True
        if not valid:
            return None
        await DeletionGuard(scope).check(uow, [ContentRef(row["source_type"], row["source_id"])])
        path = row["location"].get("field_path") or []
        return SourceState(
            row["title"] or "业务工具",
            "TOOL",
            3,
            row["observed_at"],
            frozenset([str(path[-1])]) if path else frozenset(),
        )
