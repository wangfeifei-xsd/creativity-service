"""不可变路由、正式发布门禁和每次尝试前的当前状态核验。"""

from typing import Any

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import ResourceVersion
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, digest, new_id, unavailable
from creativity_service.core.versioning import version_view
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.models.policy import (
    PROTOCOLS,
    attempt_order,
    require_capabilities,
    validate_parameters,
)
from creativity_service.modules.models.repositories import model_key, repository, required
from creativity_service.modules.models.schemas import (
    FrozenModel,
    ReleaseInput,
    RouteInput,
    RouteList,
    RouteVersionInput,
    RouteView,
)
from creativity_service.modules.models.services import ModelService, action
from creativity_service.modules.models.versioning import freeze, version_keys


class ModelRouting:
    def __init__(self, service: ModelService) -> None:
        self.service = service

    async def create(self, session: AdminSession, body: RouteInput) -> RouteView:
        identifier = new_id("route")
        async with self.service.mutation(session, "model_routes", identifier) as (uow, context):
            repo = repository(context.scope, "model_routes")
            if await repo.find(uow.connection, code=body.code):
                raise ServiceError("CODE_EXISTS", "路由编码已存在", 409)
            row = await repo.add(uow, identifier, {**body.model_dump(), "status": "ACTIVE"})
        return self.view(row, None)

    def view(self, row: dict[str, Any], version_id: str | None) -> RouteView:
        return RouteView(
            id=row["id"],
            code=row["code"],
            name=row["name"],
            revision=row["revision"],
            status=row["status"],
            status_label="启用" if row["status"] == "ACTIVE" else "停用",
            released_version_id=version_id,
            actions=[action("version", "新增版本"), action("release", "发布")],
        )

    async def list_items(self, session: AdminSession) -> RouteList:
        context = await self.service.context(session)
        async with transaction(
            self.service.engine, context.scope, [content_key(context.scope)]
        ) as uow:
            rows = await repository(context.scope, "model_routes").find(uow.connection)
            releases = await repository(context.scope, "release_mappings").find(
                uow.connection, resource_type="model_route"
            )
            blocked = await DeletionGuard(context.scope).blocked_refs(
                uow, [ContentRef("model_route", r["id"]) for r in rows]
            )
            mappings = {r["resource_id"]: r["version_id"] for r in releases}
            result = [
                self.view(row, mappings.get(row["id"]))
                for row in rows
                if ContentRef("model_route", row["id"]) not in blocked
            ]
        return RouteList(items=result, actions=[action("create", "新增路由")])

    async def create_version(
        self, session: AdminSession, identifier: str, body: RouteVersionInput
    ) -> ResourceVersion:
        service = self.service
        context = await service.context(session)
        ids = [body.primary_model, *body.fallback_models]
        attempt_order(ids, body.retry_policy)
        if not body.required_capabilities or len(set(body.required_capabilities)) != len(
            body.required_capabilities
        ):
            raise ServiceError("MODEL_ROUTE_INVALID", "路由能力不能为空或重复", 422)
        async with service.engine.connect() as database:
            model_rows = await repository(context.scope, "models").get_many(database, ids)
            connection_rows = await repository(context.scope, "model_connections").get_many(
                database, [m["connection_id"] for m in model_rows.values()]
            )
            if set(ids) - model_rows.keys() or any(
                m["connection_id"] not in connection_rows for m in model_rows.values()
            ):
                raise ServiceError("NOT_FOUND", "模型或连接不存在", 404)
            models = [model_rows[i] for i in ids]
            connections = [connection_rows[m["connection_id"]] for m in models]
        snapshots = [
            service.snapshot(context, m, c) for m, c in zip(models, connections, strict=True)
        ]
        dependencies = list(
            dict.fromkeys(
                [v for s in snapshots for v in (s.model_version_id, s.connection_version_id)]
            )
        )
        version_id = new_id("version")
        keys = version_keys(
            context.scope.channel_id, version_id, "model_route", identifier, dependencies
        )
        async with service.mutation(session, "model_routes", identifier, keys) as (uow, context):
            await required(uow.connection, context.scope, "model_routes", identifier)
            if await repository(context.scope, "resource_versions").find(
                uow.connection,
                resource_type="model_route",
                resource_id=identifier,
                version_label=body.label,
            ):
                raise ServiceError("VERSION_LABEL_CONFLICT", "版本名称已存在", 409)
            await service.locked_use(uow, session, ids)
            for snapshot in snapshots:
                model = await required(uow.connection, context.scope, "models", snapshot.model_id)
                conn = await required(
                    uow.connection, context.scope, "model_connections", snapshot.connection_id
                )
                current = service.snapshot(context, model, conn)
                if current != snapshot:
                    raise ServiceError("REVISION_CONFLICT", "模型配置已变更，请重新创建路由", 409)
                self.validate_current(model, conn, current, list(body.required_capabilities))
                validate_parameters(
                    current.protocol,
                    current.parameter_allowlist,
                    {**current.parameters, **body.parameters},
                )
            content = {
                **body.model_dump(mode="json"),
                "models": [
                    s.model_copy(
                        update={"parameters": {**s.parameters, **body.parameters}}
                    ).model_dump(mode="json")
                    for s in snapshots
                ],
                "attempt_order": list(attempt_order(ids, body.retry_policy)),
            }
            row = await freeze(
                uow,
                context,
                version_id,
                "model_route",
                identifier,
                body.label,
                content,
                dependencies,
            )
        return version_view(row)

    def validate_current(
        self,
        model: dict[str, Any],
        connection: dict[str, Any],
        snapshot: FrozenModel,
        capabilities: list[str],
    ) -> None:
        if model["status"] != "ACTIVE" or connection["status"] != "ACTIVE":
            raise ServiceError("MODEL_DISABLED", "模型或连接已停用", 403)
        if not PROTOCOLS[snapshot.protocol].enabled:
            raise ServiceError("MODEL_PROTOCOL_DISABLED", "该模型协议尚未启用", 422)
        require_capabilities(model["capabilities"], snapshot.config_digest, capabilities)

    async def release(
        self, session: AdminSession, identifier: str, body: ReleaseInput
    ) -> ResourceVersion:
        service = self.service
        context = await service.context(session)
        await service.iam.authorization.boundary(
            context, "release:publish", "channel", context.scope.channel_id
        )
        mapping_id = digest(
            [context.scope.channel_id, context.scope.environment, "model_route", identifier]
        )
        async with service.mutation(
            session,
            "model_routes",
            identifier,
            [record_key(context.scope.channel_id, "release_mappings", mapping_id)],
        ) as (uow, context):
            await service.iam.access.locked_policy(uow, session, "release:publish")
            route = await required(uow.connection, context.scope, "model_routes", identifier)
            row = await required(
                uow.connection, context.scope, "resource_versions", body.version_id
            )
            if (
                row["resource_type"] != "model_route"
                or row["resource_id"] != identifier
                or row["state"] != "PUBLISHED"
                or route["status"] != "ACTIVE"
            ):
                raise ServiceError("MODEL_ROUTE_INVALID", "路由版本不可发布", 422)
            await DeletionGuard(context.scope).check(uow, [ContentRef("version", body.version_id)])
            snapshots = [FrozenModel.model_validate(s) for s in row["content"]["models"]]
            await service.locked_use(uow, session, [s.model_id for s in snapshots])
            for s in snapshots:
                if (
                    s.scope.channel_id != context.scope.channel_id
                    or s.scope.environment != context.scope.environment
                ):
                    raise ServiceError("SCOPE_MISMATCH", "路由连接不属于当前渠道环境", 403)
                model = await required(uow.connection, context.scope, "models", s.model_id)
                conn = await required(
                    uow.connection, context.scope, "model_connections", s.connection_id
                )
                current = service.snapshot(context, model, conn)
                if current.config_digest != s.config_digest:
                    raise ServiceError("MODEL_CONFIGURATION_STALE", "路由引用的模型配置已变更", 409)
                self.validate_current(model, conn, current, row["content"]["required_capabilities"])
                await DeletionGuard(context.scope).check(
                    uow,
                    [
                        ContentRef("model", s.model_id),
                        ContentRef("version", s.model_version_id),
                        ContentRef("version", s.connection_version_id),
                    ],
                )
            if row["content"]["hard_amount_budget"]:
                if service.prices is None:
                    raise unavailable("硬金额预算所需的模型价格服务")
                await service.prices.require_priced(uow, context, snapshots)
            repo = repository(context.scope, "release_mappings")
            previous = await repo.get(uow.connection, mapping_id)
            if (previous["version_id"] if previous else None) != body.expected_version_id:
                raise ServiceError("REVISION_CONFLICT", "路由发布版本已变更", 409)
            values = {
                "resource_type": "model_route",
                "resource_id": identifier,
                "version_id": body.version_id,
                "published_by": context.principal_id,
                "release_note": "模型路由发布",
            }
            if previous:
                await repo.change(uow, mapping_id, previous["revision"], values)
            else:
                await repo.add(uow, mapping_id, values)
        return version_view(row)

    async def prepare_attempt(
        self,
        context: AuthContext,
        frozen: FrozenModel,
        capabilities: list[str],
        *,
        debug: bool = False,
    ) -> FrozenModel:
        """17 登记尝试前读取实际配置；适配器发送前再复核，不执行重试。"""
        service = self.service
        if (
            frozen.scope.channel_id != context.scope.channel_id
            or frozen.scope.environment != context.scope.environment
        ):
            raise ServiceError("SCOPE_MISMATCH", "模型调用范围不符", 403)
        await service.iam.authorization.boundary(context, "run:create", "model", frozen.model_id)
        async with transaction(
            service.engine,
            context.scope,
            [
                model_key(context.scope.channel_id),
                content_key(context.scope),
                policy_key(context.scope.channel_id),
            ],
        ) as uow:
            await DeletionGuard(context.scope).check(
                uow,
                [
                    ContentRef("model", frozen.model_id),
                    ContentRef("version", frozen.model_version_id),
                    ContentRef("version", frozen.connection_version_id),
                ],
            )
            model = await required(uow.connection, context.scope, "models", frozen.model_id)
            conn = await required(
                uow.connection, context.scope, "model_connections", frozen.connection_id
            )
            current = service.snapshot(context, model, conn)
            if current.config_digest != frozen.config_digest:
                raise ServiceError("MODEL_CONFIGURATION_STALE", "模型配置已变化，需要重新冻结", 409)
            self.validate_current(model, conn, current, [] if debug else capabilities)
        # 当前授权在所有可能等待的数据库读取后再次核验；凭据边界还会复核一次。
        await service.iam.authorization.boundary(context, "run:create", "model", frozen.model_id)
        return frozen.model_copy(
            update={
                "scope": context.scope,
                "connection_version_id": current.connection_version_id,
                "connection_revision": current.connection_revision,
                "provider_credential_id": current.provider_credential_id,
            }
        )

    async def check_dependency(
        self,
        context: AuthContext,
        model_id: str,
        capabilities: list[str],
        parameters: dict[str, Any] | None = None,
    ) -> FrozenModel:
        """16 冻结 Agent 依赖前取得具体模型和连接版本，并检查当前使用授权。"""
        await self.service.iam.authorization.boundary(context, "run:create", "model", model_id)
        async with transaction(
            self.service.engine,
            context.scope,
            [model_key(context.scope.channel_id), content_key(context.scope)],
        ) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("model", model_id)])
            model = await required(uow.connection, context.scope, "models", model_id)
            connection = await required(
                uow.connection, context.scope, "model_connections", model["connection_id"]
            )
            snapshot = self.service.snapshot(context, model, connection)
            self.validate_current(model, connection, snapshot, capabilities)
            merged = {**snapshot.parameters, **(parameters or {})}
            validate_parameters(snapshot.protocol, snapshot.parameter_allowlist, merged)
            return snapshot.model_copy(update={"parameters": merged})

    async def resolve_route(
        self, context: AuthContext, version_id: str
    ) -> tuple[list[FrozenModel], list[str]]:
        """17 按已冻结路由顺序解析候选；不创建尝试，也不执行网络调用。"""
        async with transaction(
            self.service.engine, context.scope, [content_key(context.scope)]
        ) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("version", version_id)])
            version = await required(uow.connection, context.scope, "resource_versions", version_id)
            if version["state"] != "PUBLISHED" or version["resource_type"] != "model_route":
                raise ServiceError("MODEL_ROUTE_INVALID", "模型路由版本不可用", 422)
            content = version["content"]
        candidates = []
        for data in content["models"]:
            frozen = FrozenModel.model_validate(data)
            candidates.append(
                await self.prepare_attempt(context, frozen, content["required_capabilities"])
            )
        return candidates, content["attempt_order"]
