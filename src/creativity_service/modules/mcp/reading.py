"""批量检查管理页面的固定绑定；真实远端调用仍使用执行边界复核。"""

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata as core
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.mcp.bindings import require_current_binding
from creativity_service.modules.mcp.oauth_tables import metadata as oauth_metadata
from creativity_service.modules.mcp.repositories import repository
from creativity_service.modules.mcp.tables import metadata
from creativity_service.modules.tools.schemas import ToolBinding

if TYPE_CHECKING:
    from creativity_service.modules.mcp.services import McpService


async def import_reasons(
    service: "McpService", context: AuthContext, imports: list[dict[str, Any]]
) -> dict[str, str | None]:
    async with service.engine.connect() as connection:
        connections = await repository(context.scope, "mcp_connections").get_many(
            connection, [r["connection_id"] for r in imports]
        )
        originals = await repository(context.scope, "mcp_discoveries").get_many(
            connection, [r["discovery_id"] for r in imports]
        )
        table = metadata.tables["mcp_discoveries"]
        latest = {
            r["connection_id"]: dict(r)
            for r in (
                await connection.execute(
                    select(table)
                    .where(
                        Repository(table, context.scope).predicate(),
                        table.c.connection_id.in_(connections),
                    )
                    .distinct(table.c.connection_id)
                    .order_by(table.c.connection_id, table.c.created_at.desc(), table.c.id.desc())
                )
            ).mappings()
        }
        credentials = await Repository(core.tables["credentials"], context.scope).get_many(
            connection, [r["credential_ref"] for r in connections.values() if r["credential_ref"]]
        )
    oauth_ids = [r["id"] for r in connections.values() if r["transport"] == "oauth"]
    oauth: dict[str, str | None] = {}
    for ownership in ("user", "service"):
        remaining = [identifier for identifier in oauth_ids if identifier not in oauth]
        if not remaining:
            break
        scoped = service.oauth.scope_context(context, ownership)
        identifiers = {
            digest(
                [
                    scoped.scope.model_dump(),
                    identifier,
                    ownership,
                    service.oauth.owner(context, ownership),
                ]
            ): identifier
            for identifier in remaining
        }
        async with transaction(service.engine, scoped.scope, [content_key(scoped.scope)]) as uow:
            tokens = await Repository(
                oauth_metadata.tables["mcp_oauth_tokens"], scoped.scope
            ).get_many(uow.connection, identifiers)
            blocked = (
                await DeletionGuard(scoped.scope).blocked_refs(
                    uow, [ContentRef("oauth_token", i) for i in tokens]
                )
                if tokens
                else frozenset()
            )
            for identifier, token in tokens.items():
                oauth[identifiers[identifier]] = (
                    "委托已撤销，请重新授权"
                    if token["state"] != "ACTIVE"
                    or ContentRef("oauth_token", identifier) in blocked
                    else None
                )
    result: dict[str, str | None] = {}
    for imported in imports:
        row, original = (
            connections.get(imported["connection_id"]),
            originals.get(imported["discovery_id"]),
        )
        try:
            if row is None or original is None:
                raise ServiceError("NOT_FOUND", "连接记录不存在", 404)
            require_current_binding(
                ToolBinding(
                    adapter_key=imported["id"],
                    implementation_version=imported["schema_hash"],
                    connection_id=row["id"],
                ),
                row,
                imported,
                original,
                latest.get(row["id"]),
            )
            if row["transport"] == "oauth":
                reason = oauth.get(row["id"], "当前身份尚未完成 OAuth 授权")
                if reason:
                    raise ServiceError("OAUTH_REQUIRED", reason, 403)
            elif row["credential_ref"]:
                credential = credentials.get(row["credential_ref"])
                if (
                    credential is None
                    or credential["purpose"] != "mcp"
                    or credential["state"] != "ACTIVE"
                ):
                    raise ServiceError("MCP_AUTH_FAILED", "连接凭据不可用", 403)
                if credential["revision"] != row["credential_revision"]:
                    raise ServiceError(
                        "MCP_AUTH_FAILED", "凭据版本已变化，请更新配置并重新测试", 403
                    )
        except ServiceError as exc:
            result[imported["id"]] = exc.message
        else:
            result[imported["id"]] = None
    return result
