"""实际参与运行后按资源与运行去重登记，配置关联本身不产生使用次数。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import read_key, record_key
from creativity_service.core.primitives import digest
from creativity_service.modules.resources.configuration import KINDS, TABLES
from creativity_service.storage import metadata


async def record_use(
    engine: AsyncEngine,
    context: AuthContext,
    run_id: str,
    kind: str,
    resource_id: str,
    *,
    resource_name: str | None = None,
    agent_name: str | None = None,
    purpose: str = "production",
) -> None:
    if kind not in KINDS:
        return
    scope = context.scope
    identifier = digest([scope.channel_id, run_id, kind, resource_id])
    async with transaction(
        engine,
        scope,
        [read_key(content_key(scope)), record_key(scope.channel_id, "resource_uses", identifier)],
    ) as uow:
        await DeletionGuard(scope).check(uow, [ContentRef("run", run_id)])
        repo = Repository(metadata.tables["resource_uses"], scope)
        if await repo.get(uow.connection, identifier):
            return
        row = await Repository(metadata.tables[TABLES[kind]], scope).get(
            uow.connection, resource_id
        )
        if row is None:
            return
        run = await Repository(metadata.tables["runs"], scope).get(uow.connection, run_id)
        actor = (run or {}).get("actor_id") or context.actor_id
        caller_name = None
        if actor:
            from creativity_service.modules.iam.tables import metadata as iam_metadata

            accounts = iam_metadata.tables["platform_accounts"]
            caller_name = await uow.connection.scalar(
                active_rows(
                    select(accounts.c.display_name).where(
                        accounts.c.channel_id == "system", accounts.c.id == actor
                    )
                )
            )
        if not caller_name and context.client_id:
            client = await Repository(metadata.tables["service_clients"], scope).get(
                uow.connection, context.client_id
            )
            caller_name = client["name"] if client else None
        await repo.add(
            uow,
            identifier,
            {
                "resource_type": kind,
                "resource_id": resource_id,
                "resource_name": resource_name or row["name"],
                "run_id": run_id,
                "agent_name": (run or {}).get("agent_name") or agent_name,
                "caller_name": caller_name,
                "purpose": (run or {}).get("purpose") or purpose,
            },
        )
