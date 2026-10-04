"""配置者管理订阅；批量读取复用范围判断，发送边界重新复核授权。"""

from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, UnitOfWork, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.channels.tables import metadata as channel_metadata
from creativity_service.modules.integrations.authorization import require_management
from creativity_service.modules.integrations.automation import AutomationService, owner, scope_of
from creativity_service.modules.runs.tables import metadata


class RunSubscriptions:
    def __init__(self, automation: AutomationService) -> None:
        self.automation = automation

    async def clients(
        self, connection: AsyncConnection, context: AuthContext, client_ids: list[str] | None = None
    ) -> dict[str, dict[str, Any]]:
        table = channel_metadata.tables["service_clients"]
        statement = select(table).where(
            table.c.channel_id == context.scope.channel_id,
            table.c.environment == context.scope.environment,
        )
        if client_ids is not None:
            statement = statement.where(table.c.id.in_(client_ids))
        rows = (await connection.execute(statement)).mappings()
        return {
            row["id"]: dict(row)
            for row in rows
            if context.scope.data_scope_id in row["data_scopes"]
        }

    async def options(self, context: AuthContext) -> list[dict[str, Any]]:
        await self.automation.manage(context)
        await self.automation.authorization.boundary(
            context, "run:read", "channel", context.scope.channel_id
        )
        async with self.automation.engine.connect() as connection:
            clients = await self.clients(connection, context)
        return [
            {"client_id": row["id"], "name": row["name"], "active": row["status"] == "ACTIVE"}
            for row in clients.values()
        ]

    async def validate(self, uow: UnitOfWork, context: AuthContext, client_ids: list[str]) -> None:
        if len(client_ids) != len(set(client_ids)):
            raise ServiceError("SUBSCRIPTION_INVALID", "调用服务不能重复", 422)
        if not client_ids:
            return
        # 订阅未来运行需要当前工作区的运行读取授权，不能由配置权限隐式扩权。
        await require_management(uow, context, "run:read")
        clients = await self.clients(uow.connection, context, client_ids)
        if any(key not in clients or clients[key]["status"] != "ACTIVE" for key in client_ids):
            raise ServiceError("SUBSCRIPTION_INVALID", "请选择当前工作区内启用的调用服务", 422)

    async def require_clients(self, context: AuthContext, client_ids: list[str]) -> None:
        if not client_ids:
            return
        await self.automation.authorization.boundary(
            context, "run:read", "channel", context.scope.channel_id
        )
        async with self.automation.engine.connect() as connection:
            clients = await self.clients(connection, context, client_ids)
        if any(key not in clients or clients[key]["status"] != "ACTIVE" for key in client_ids):
            raise ServiceError("SUBSCRIPTION_UNAVAILABLE", "订阅的调用服务已停用或范围已变化", 403)

    @staticmethod
    def predicate(context: AuthContext, client_ids: list[str]) -> ColumnElement[bool]:
        table = metadata.tables["runs"]
        if not client_ids:
            return Repository(table, context.scope).predicate()
        # 只对显式订阅服务展开主体，渠道、环境和数据域始终固定。
        return and_(
            table.c.channel_id == context.scope.channel_id,
            table.c.environment == context.scope.environment,
            table.c.data_scope_id == context.scope.data_scope_id,
            table.c.client_id.in_(client_ids),
        )

    async def authorize(
        self, context: AuthContext, client_ids: list[str], run: dict[str, Any]
    ) -> None:
        async with self.automation.engine.connect() as connection:
            clients = await self.clients(connection, context, client_ids) if client_ids else {}
        self.match(context, client_ids, run, clients)
        actual = scope_of(run)
        # 仅恢复数据库中的主体范围，继续以配置者身份检查当前权限。
        scoped = context.model_copy(update={"scope": actual})
        await self.automation.runs.authorization.require(scoped, "run:read", run["id"])
        async with transaction(self.automation.engine, actual, [content_key(actual)]) as uow:
            await DeletionGuard(actual).check(uow, [ContentRef("run", run["id"])])

    @staticmethod
    def match(
        context: AuthContext,
        client_ids: list[str],
        run: dict[str, Any],
        clients: dict[str, dict[str, Any]],
    ) -> None:
        actual = scope_of(run)
        source = AuthContext.model_validate(run["identity"])
        if not context.actor_id or any(
            getattr(actual, key) != getattr(context.scope, key)
            for key in ("channel_id", "environment", "data_scope_id")
        ):
            raise ServiceError("NOT_FOUND", "运行不属于当前订阅范围", 404)
        if client_ids:
            client = clients.get(run["client_id"])
            if (
                run["client_id"] not in client_ids
                or source.client_id != run["client_id"]
                or source.actor_id is not None
                or not client
                or client["status"] != "ACTIVE"
            ):
                raise ServiceError("FORBIDDEN", "运行来源已不在有效订阅范围内", 403)
        elif actual != context.scope or owner(source) != owner(context):
            raise ServiceError("NOT_FOUND", "运行不属于当前订阅范围", 404)
