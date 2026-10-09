"""资源管理共用查询与发布、下架、删除规则；历史运行证据独立保留。"""

from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import DisplayStatus
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest, new_id, unavailable
from creativity_service.core.versioning import VersionValidator, version_view
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.iam.reading import resource_state
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.iam.schemas import AccessAction
from creativity_service.modules.resources.configuration import (
    LABELS,
    TABLES,
    reference_statement,
    require_dependencies,
)
from creativity_service.modules.resources.schemas import (
    ResourceKind,
    ResourceMutation,
    ResourceReferencePage,
    ResourceReferenceView,
    ResourceSummary,
    ResourceUse,
    ResourceUsePage,
)


class ResourceManagement:
    def __init__(
        self,
        engine: AsyncEngine,
        authorization: IamAuthorization,
        validators: dict[str, VersionValidator],
    ) -> None:
        self.engine, self.authorization, self.validators = engine, authorization, validators

    @staticmethod
    def permission(kind: str) -> str:
        return "model:manage" if kind == "model_route" else f"{kind}:manage"

    async def summaries(
        self, context: AuthContext, kind: ResourceKind, identifiers: list[str]
    ) -> list[ResourceSummary]:
        scope = context.scope
        policy = await self.authorization.read_policy(context)
        async with self.engine.connect() as connection:
            rows = await repository(TABLES[kind], scope).get_many(connection, identifiers)
            rows = {
                i: r
                for i, r in rows.items()
                if r["status"] != "DELETED"
                and self.permission(kind)
                in policy.actions(kind, i, resource_state(context, kind, r))
            }
            configs = await repository("resource_versions", scope).get_many(connection, rows)
            refs = reference_statement(scope, list(rows)).subquery()
            references: dict[str, int] = {
                str(r[0]): int(r[1])
                for r in (
                    await connection.execute(
                        select(refs.c.target_id, func.count()).group_by(refs.c.target_id)
                    )
                ).all()
            }
            mappings = await repository("release_mappings", scope).find_many(
                connection, "resource_id", rows, resource_type=kind
            )
            uses = repository("resource_uses", scope).table
            counts: dict[str, int] = {
                str(r[0]): int(r[1])
                for r in (
                    await connection.execute(
                        select(uses.c.resource_id, func.count())
                        .where(
                            uses.c.channel_id == scope.channel_id,
                            uses.c.resource_type == kind,
                            uses.c.resource_id.in_(rows),
                        )
                        .group_by(uses.c.resource_id)
                    )
                ).all()
            }
        published = {r["resource_id"] for r in mappings}
        result = []
        for identifier, row in rows.items():
            config = configs.get(identifier)
            permissions = policy.actions(kind, identifier, resource_state(context, kind, row))
            count = references.get(identifier, 0)
            # 重新绑定 MCP 后保留发布归属，但当前配置须重新发布才可执行。
            released = identifier in published and bool(config and config["state"] == "PUBLISHED")
            reason = f"被 {count} 个资源引用，请先解除关联" if count else None
            actions = [
                AccessAction(
                    action_key="edit",
                    label="修改",
                    enabled="version:edit" in permissions
                    and (
                        not config
                        or config["state"] != "PUBLISHED"
                        or "release:publish" in permissions
                    ),
                    disabled_reason=None
                    if "version:edit" in permissions
                    and (
                        not config
                        or config["state"] != "PUBLISHED"
                        or "release:publish" in permissions
                    )
                    else "缺少编辑或生效配置修改权限",
                )
            ]
            actions.append(
                AccessAction(
                    action_key="unpublish" if released else "publish",
                    label="下架" if released else "发布",
                    enabled="release:publish" in permissions
                    and (not released or not count)
                    and config is not None,
                    disabled_reason=reason
                    if released and count
                    else "请先保存配置"
                    if config is None
                    else "缺少发布权限"
                    if "release:publish" not in permissions
                    else None,
                )
            )
            actions.append(
                AccessAction(
                    action_key="delete", label="删除", enabled=not count, disabled_reason=reason
                )
            )
            result.append(
                ResourceSummary(
                    resource_id=identifier,
                    name=row["name"],
                    status=DisplayStatus(
                        value="PUBLISHED" if released else "UNPUBLISHED",
                        label="已发布" if released else "未发布",
                        tone="success" if released else "default",
                    ),
                    reference_count=count,
                    usage_count=counts.get(identifier, 0),
                    revision=row["revision"],
                    configuration_revision=config["revision"] if config else None,
                    updated_at=max(row["updated_at"], config["updated_at"])
                    if config
                    else row["updated_at"],
                    actions=actions,
                )
            )
        return result

    async def references(
        self,
        context: AuthContext,
        kind: ResourceKind,
        identifier: str,
        offset: int = 0,
        limit: int = 20,
    ) -> ResourceReferencePage:
        await self.authorization.boundary(context, self.permission(kind), kind, identifier)
        async with self.engine.connect() as connection:
            statement = reference_statement(context.scope, [identifier])
            total = (
                await connection.scalar(select(func.count()).select_from(statement.subquery())) or 0
            )
            refs = list(
                (
                    await connection.execute(
                        statement.order_by("resource_type", "resource_id")
                        .offset(offset)
                        .limit(limit)
                    )
                ).mappings()
            )
            names = {
                k: await repository(TABLES[k], context.scope).get_many(
                    connection, [r["resource_id"] for r in refs if r["resource_type"] == k]
                )
                for k in {r["resource_type"] for r in refs}
            }
            mappings = repository("release_mappings", context.scope).table
            releases = (
                await connection.execute(
                    select(mappings.c.resource_id, mappings.c.environment).where(
                        mappings.c.channel_id == context.scope.channel_id,
                        mappings.c.resource_id.in_([r["resource_id"] for r in refs]),
                    )
                )
            ).all()
        environments = {"dev": "开发", "test": "测试", "fat": "验收", "prod": "生产"}
        result = []
        for ref in refs:
            row = names[ref["resource_type"]].get(ref["resource_id"])
            if row:
                scopes = sorted(
                    {
                        environments.get(r.environment, r.environment)
                        for r in releases
                        if r.resource_id == row["id"]
                    }
                )
                result.append(
                    ResourceReferenceView(
                        resource_id=row["id"],
                        resource_type=ref["resource_type"],
                        resource_type_label=LABELS[ref["resource_type"]],
                        name=row["name"],
                        status=DisplayStatus(
                            value="PUBLISHED" if scopes else "UNPUBLISHED",
                            label="已发布" if scopes else "未发布",
                            tone="success" if scopes else "default",
                        ),
                        environments=scopes,
                    )
                )
        return ResourceReferencePage(items=result, total=total)

    async def uses(
        self, context: AuthContext, kind: ResourceKind, identifier: str, offset: int, limit: int
    ) -> ResourceUsePage:
        await self.authorization.boundary(context, self.permission(kind), kind, identifier)
        table = repository("resource_uses", context.scope).table
        predicate = (
            (table.c.channel_id == context.scope.channel_id)
            & (table.c.resource_type == kind)
            & (table.c.resource_id == identifier)
        )
        async with self.engine.connect() as connection:
            row = await repository(TABLES[kind], context.scope).get(connection, identifier)
            total = (
                await connection.scalar(select(func.count()).select_from(table).where(predicate))
                or 0
            )
            uses = (
                await connection.execute(
                    select(table)
                    .where(predicate)
                    .order_by(table.c.created_at.desc(), table.c.id)
                    .offset(offset)
                    .limit(limit)
                )
            ).mappings()
            items = [
                ResourceUse(
                    run_id=r["run_id"],
                    resource_name=r["resource_name"],
                    agent_name=r["agent_name"],
                    caller_name=r["caller_name"],
                    environment=r["environment"],
                    purpose=r["purpose"],
                    used_at=r["created_at"],
                    deleted=not row or row["status"] == "DELETED",
                )
                for r in uses
            ]
        return ResourceUsePage(items=items, total=total)

    async def mutate(
        self,
        context: AuthContext,
        kind: ResourceKind,
        identifier: str,
        operation: Literal["publish", "unpublish", "delete"],
        body: ResourceMutation,
    ) -> None:
        scope = context.scope
        await self.authorization.boundary(context, self.permission(kind), kind, identifier)
        if operation != "delete":
            await self.authorization.boundary(context, "release:publish", kind, identifier)
        mapping_id = digest([scope.channel_id, scope.environment, kind, identifier])
        audit_id = new_id("audit")
        keys = [
            content_key(scope),
            policy_key(scope.channel_id),
            policy_key("system"),
            record_key(scope.channel_id, TABLES[kind], identifier),
            record_key(scope.channel_id, "resource_versions", identifier),
            record_key(scope.channel_id, "release_mappings", mapping_id),
            record_key(scope.channel_id, "audit_events", audit_id),
        ]
        async with transaction(self.engine, scope, keys) as uow:
            await locked_require(uow, context, self.permission(kind), kind, identifier)
            if operation != "delete":
                await locked_require(uow, context, "release:publish", kind, identifier)
            resources, configs, mappings = (
                repository(TABLES[kind], scope),
                repository("resource_versions", scope),
                repository("release_mappings", scope),
            )
            row = await resources.get(uow.connection, identifier)
            config = await configs.get(uow.connection, identifier)
            if row is None or row["status"] == "DELETED":
                raise ServiceError("NOT_FOUND", "资源不存在或已删除", 404)
            if (
                row["revision"] != body.revision
                or (config["revision"] if config else None) != body.configuration_revision
            ):
                raise ServiceError("REVISION_CONFLICT", "资源已修改，请刷新后重试", 409)
            await DeletionGuard(scope).check(uow, [ContentRef(kind, identifier)])
            if operation != "publish" and await uow.connection.scalar(
                select(reference_statement(scope, [identifier]).exists())
            ):
                raise ServiceError("RESOURCE_REFERENCED", "资源仍被引用，请先解除全部关联", 409)
            if operation == "publish":
                if config is None:
                    raise ServiceError("CONFIGURATION_REQUIRED", "请先保存资源配置", 422)
                await require_dependencies(uow, scope, config["dependencies"])
                validator = self.validators.get(kind)
                if validator is None:
                    raise unavailable("资源发布校验服务")
                await validator.validate(uow, context, version_view(config), "release")
                await configs.change(uow, identifier, config["revision"], {"state": "PUBLISHED"})
                previous = await mappings.get(uow.connection, mapping_id)
                values = {
                    "resource_type": kind,
                    "resource_id": identifier,
                    "version_id": identifier,
                    "published_by": context.principal_id,
                    "release_note": "发布资源",
                }
                if previous:
                    await mappings.change(uow, mapping_id, previous["revision"], values)
                else:
                    await mappings.add(uow, mapping_id, values)
            else:
                if operation == "delete":
                    uses = repository("resource_uses", scope).table
                    used = await uow.connection.scalar(
                        select(uses.c.id)
                        .where(
                            uses.c.channel_id == scope.channel_id,
                            uses.c.resource_type == kind,
                            uses.c.resource_id == identifier,
                        )
                        .limit(1)
                    )
                    if used and not body.confirm_used:
                        raise ServiceError(
                            "RESOURCE_DELETE_CONFIRM_REQUIRED",
                            "该资源有历史使用记录，需要二次确认",
                            409,
                        )
                    await resources.change(uow, identifier, row["revision"], {"status": "DELETED"})
                table = mappings.table
                conditions = [
                    table.c.channel_id == scope.channel_id,
                    table.c.resource_type == kind,
                    table.c.resource_id == identifier,
                ]
                if operation == "unpublish":
                    conditions.append(table.c.environment == scope.environment)
                # 内容图锁与所有依赖保存互斥；整资源删除需同时清理各环境的发布映射。
                await uow.connection.execute(delete(table).where(*conditions))
                remaining = await uow.connection.scalar(
                    select(table.c.id)
                    .where(
                        table.c.channel_id == scope.channel_id,
                        table.c.resource_type == kind,
                        table.c.resource_id == identifier,
                    )
                    .limit(1)
                )
                if config:
                    await configs.change(
                        uow,
                        identifier,
                        config["revision"],
                        {
                            "state": "RETIRED"
                            if operation == "delete"
                            else "PUBLISHED"
                            if remaining
                            else "DRAFT"
                        },
                    )
            await append_audit(uow, context, audit_id, f"resource.{operation}", kind, identifier)
