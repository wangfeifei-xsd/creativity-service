"""按已登记渠道恢复健康检查，重新核验原管理成员权限后执行。"""

import asyncio
from datetime import timedelta

from celery import shared_task
from sqlalchemy import select

from creativity_service.core.config import Settings
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import transaction
from creativity_service.core.infrastructure import Infrastructure
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, new_id, utcnow
from creativity_service.modules.channels.assembly import build_channel_services
from creativity_service.modules.mcp.assembly import build_mcp_service
from creativity_service.modules.mcp.repositories import repository
from creativity_service.modules.mcp.services import McpService
from creativity_service.modules.mcp.tables import metadata
from creativity_service.modules.tools.assembly import build_tool_services


async def check_due(service: McpService, context: AuthContext, connection_id: str) -> bool:
    await service.require(context, connection_id)
    scope = context.scope
    async with transaction(
        service.engine, scope, [record_key(scope.channel_id, "mcp_connections", connection_id)]
    ) as uow:
        repo = repository(scope, "mcp_connections")
        row = await repo.get(uow.connection, connection_id)
        if (
            row is None
            or row["status"] != "ENABLED"
            or row["auth_failed"]
            or row["next_check_at"] > utcnow()
        ):
            return False
        # 调度重复投递只领取一次；租约超过操作上限，进程退出后仍可到期恢复。
        delay = max(
            row["health_policy"]["interval_seconds"], 2 * row["timeouts"]["operation_seconds"] + 5
        )
        await repo.change(
            uow,
            connection_id,
            row["revision"],
            {"next_check_at": utcnow() + timedelta(seconds=delay)},
        )
    await service.probe(context, connection_id, True)
    return True


async def sweep() -> None:
    settings = Settings()
    infrastructure = Infrastructure(settings)
    try:
        iam, channels = build_channel_services(
            infrastructure.engine,
            infrastructure.redis_clients["redis_auth"],
            settings.redis_key_prefix,
        )
        tools = build_tool_services(infrastructure.engine, iam.authorization)
        service = build_mcp_service(infrastructure.engine, iam.authorization, tools)
        async with infrastructure.engine.connect() as conn:
            channel_ids = await channels.channels.repository.directory(conn)
        table = metadata.tables["mcp_connections"]
        for channel_id in channel_ids:
            async with infrastructure.engine.connect() as conn:
                rows = (
                    (
                        await conn.execute(
                            select(table)
                            .where(
                                table.c.channel_id == channel_id,
                                table.c.status == "ENABLED",
                                table.c.next_check_at <= utcnow(),
                                table.c.auth_failed.is_(False),
                            )
                            .order_by(table.c.next_check_at)
                            .limit(100)
                        )
                    )
                    .mappings()
                    .all()
                )
            for row in rows:
                context = AuthContext(
                    scope=Scope(
                        channel_id=channel_id,
                        environment=row["environment"],
                        data_scope_id=row["health_data_scope_id"],
                    ),
                    principal_type="worker",
                    principal_id=row["health_actor_id"],
                    actor_id=row["health_actor_id"],
                    request_id=new_id("mcp_health"),
                )
                try:
                    await check_due(service, context, row["id"])
                except ServiceError:
                    # 权限撤销或并发配置更新时不继续外部请求，下次调度重新核验。
                    continue
    finally:
        await infrastructure.close()


def sweep_mcp() -> None:
    asyncio.run(sweep())


sweep_mcp_task = shared_task(name="mcp.sweep", ignore_result=True)(sweep_mcp)
