"""不可变路由、正式发布门禁和每次尝试前的当前状态核验。"""

from typing import Any, Literal, cast

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import ResourceVersion
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, digest, new_id, unavailable
from creativity_service.core.versioning import version_view
from creativity_service.modules.iam.reading import require_action
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.iam.schemas import AccessAction
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
    RouteVersionView,
    RouteView,
)
from creativity_service.modules.models.services import ModelService, action
from creativity_service.modules.models.versioning import history_rows, version_keys
from creativity_service.modules.resources.configuration import configuration_values


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
            status="ACTIVE",
            status_label="已发布" if version_id else "未发布",
            released_version_id=version_id,
            actions=[action("edit", "修改")],
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
                if row["status"] != "DELETED"
                and ContentRef("model_route", row["id"]) not in blocked
            ]
        return RouteList(items=result, actions=[action("create", "新增路由")])

    async def versions(self, session: AdminSession, identifier: str) -> list[RouteVersionView]:
        service = self.service
        if not isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "请先进入渠道环境", 403)
        context = session.context
        scope = context.scope
        policy = await service.iam.authorization.read_policy(context)
        permissions = policy.actions("channel", scope.channel_id)
        require_action(permissions, "model:manage")
        permission_reason = None
        if "release:publish" not in permissions:
            permission_reason = "当前角色未获此环境的发布权限，请联系有授权权限的管理员"
        async with transaction(service.engine, scope, [content_key(scope)]) as uow:
            route, rows = await history_rows(uow, context, "model_route", identifier)
            rows = [row for row in rows if row["id"] == identifier]
            if route["status"] == "DELETED":
                raise ServiceError("NOT_FOUND", "路由已删除", 404)
            releases = await repository(scope, "release_mappings").find(
                uow.connection, resource_type="model_route", resource_id=identifier
            )
            released_ids = {row["version_id"] for row in releases}
            snapshots = {
                row["id"]: [FrozenModel.model_validate(s) for s in row["content"]["models"]]
                for row in rows
            }
            # 本次列表只读取候选引用，不逐版本查库或复用完整发布流程。
            candidates = [s for items in snapshots.values() for s in items]
            models = await repository(scope, "models").get_many(
                uow.connection,
                [s.model_id for s in candidates] if permission_reason is None else [],
            )
            connections = await repository(scope, "model_connections").get_many(
                uow.connection,
                [s.connection_id for s in candidates] if permission_reason is None else [],
            )
            result = []
            for row in rows:
                reason = permission_reason
                if row["id"] in released_ids:
                    reason = "该版本已是当前发布版本"
                elif row["state"] != "PUBLISHED" or route["status"] != "ACTIVE":
                    reason = "路由或版本已停用"
                elif reason is None:
                    for snapshot in snapshots[row["id"]]:
                        model = models.get(snapshot.model_id)
                        connection = connections.get(snapshot.connection_id)
                        try:
                            if model is None or connection is None:
                                raise ServiceError("NOT_FOUND", "模型或连接不存在", 404)
                            self.validate_release_candidate(
                                context,
                                snapshot,
                                model,
                                connection,
                                row["content"]["required_capabilities"],
                            )
                        except ServiceError as exc:
                            reason = f"{snapshot.model_name}：{exc.message}"
                            break
                result.append(
                    RouteVersionView(
                        **version_view(row).model_dump(),
                        actions=[
                            AccessAction(
                                action_key="release",
                                label="发布",
                                enabled=reason is None,
                                disabled_reason=reason,
                            )
                        ],
                    )
                )
            return result

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
        version_id = identifier
        keys = version_keys(
            context.scope.channel_id, version_id, "model_route", identifier, dependencies
        )
        async with service.mutation(session, "model_routes", identifier, keys) as (uow, context):
            from creativity_service.modules.resources.configuration import require_edit

            await require_edit(uow, context, "model_route", identifier)
            if body.name is not None:
                if not body.name.strip() or body.resource_revision is None:
                    raise ServiceError("ROUTE_INVALID", "请填写名称并刷新资源信息", 422)
                await repository(context.scope, "model_routes").change(
                    uow, identifier, body.resource_revision, {"name": body.name.strip()}
                )
            config_repo = repository(context.scope, "resource_versions")
            previous = await config_repo.get(uow.connection, identifier)
            if (previous["revision"] if previous else None) != body.revision:
                raise ServiceError("REVISION_CONFLICT", "路由配置已修改，请刷新", 409)
            if previous and previous["state"] == "PUBLISHED":
                await service.iam.access.locked_policy(uow, session, "release:publish")
            # 保存未发布配置只需模型管理权限；运行授权和能力证据在发布、执行边界校验。
            current_models = await repository(context.scope, "models").get_many(uow.connection, ids)
            current_connections = await repository(context.scope, "model_connections").get_many(
                uow.connection, [s.connection_id for s in snapshots]
            )
            for snapshot in snapshots:
                model = current_models.get(snapshot.model_id)
                conn = current_connections.get(snapshot.connection_id)
                if model is None or conn is None:
                    raise ServiceError("NOT_FOUND", "模型或连接不存在", 404)
                current = service.snapshot(context, model, conn)
                if current != snapshot:
                    raise ServiceError("REVISION_CONFLICT", "模型配置已变更，请重新创建路由", 409)
                self.validate_current(model, conn, current, [])
                validate_parameters(
                    current.protocol,
                    current.parameter_allowlist,
                    {**current.parameters, **body.parameters},
                )
            content = {
                **body.model_dump(
                    mode="json", exclude={"label", "revision", "name", "resource_revision"}
                ),
                "models": [
                    s.model_copy(
                        update={"parameters": {**s.parameters, **body.parameters}}
                    ).model_dump(mode="json")
                    for s in snapshots
                ],
                "attempt_order": list(attempt_order(ids, body.retry_policy)),
            }
            values = configuration_values(
                "model_route", identifier, content, dependencies, {}, context.principal_id
            )
            if previous:
                values["state"] = previous["state"]
                if previous["state"] == "PUBLISHED":
                    candidate = version_view({**previous, **values})
                    await self.validate(uow, context, candidate, "release")
                row = await config_repo.change(uow, identifier, previous["revision"], values)
            else:
                row = await config_repo.add(uow, identifier, values)
        return version_view(row)

    async def validate(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        version: ResourceVersion,
        operation: Literal["freeze", "release"],
    ) -> None:
        content = cast(dict[str, Any], version.content)
        snapshots = [FrozenModel.model_validate(value) for value in content["models"]]
        models = await repository(context.scope, "models").get_many(
            uow.connection, [s.model_id for s in snapshots]
        )
        connections = await repository(context.scope, "model_connections").get_many(
            uow.connection, [s.connection_id for s in snapshots]
        )
        for snapshot in snapshots:
            model, connection = (
                models.get(snapshot.model_id),
                connections.get(snapshot.connection_id),
            )
            if model is None or connection is None:
                raise ServiceError("NOT_FOUND", "模型或连接不存在", 404)
            self.validate_release_candidate(
                context, snapshot, model, connection, content["required_capabilities"]
            )
        if version.content["hard_amount_budget"]:
            if self.service.prices is None:
                raise unavailable("硬金额预算所需的模型价格服务")
            await self.service.prices.require_priced(uow, context, snapshots)

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

    def validate_release_candidate(
        self,
        context: AuthContext,
        snapshot: FrozenModel,
        model: dict[str, Any],
        connection: dict[str, Any],
        capabilities: list[str],
    ) -> None:
        """展示发布状态和实际发布共用无查询校验，提交时使用事务内最新配置。"""
        if (
            snapshot.scope.channel_id != context.scope.channel_id
            or snapshot.scope.environment != context.scope.environment
        ):
            raise ServiceError("SCOPE_MISMATCH", "路由连接不属于当前渠道环境", 403)
        current = self.service.snapshot(context, model, connection)
        if current.config_digest != snapshot.config_digest:
            raise ServiceError("MODEL_CONFIGURATION_STALE", "路由引用的模型配置已变更", 409)
        self.validate_current(model, connection, current, capabilities)

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
            for s in snapshots:
                model = await required(uow.connection, context.scope, "models", s.model_id)
                conn = await required(
                    uow.connection, context.scope, "model_connections", s.connection_id
                )
                self.validate_release_candidate(
                    context, s, model, conn, row["content"]["required_capabilities"]
                )
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
                # 目的地只能取已加载并校验的当前连接，不能由调用快照覆盖。
                "endpoint": current.endpoint,
                "allowed_networks": current.allowed_networks,
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
