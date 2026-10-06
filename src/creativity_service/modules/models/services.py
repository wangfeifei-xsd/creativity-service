"""渠道模型配置服务；检查与写入共用短事务，网络及密钥读取在事务外。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pydantic import SecretBytes
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import AuthContext, ControlAuthContext, ControlScope
from creativity_service.core.contracts import ResourceVersion, VisibleAction
from creativity_service.core.database import UnitOfWork, transaction, validate_row
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.name_codes import name_code
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.core.security.credentials import CredentialService
from creativity_service.core.security.outbound import OutboundPolicy
from creativity_service.core.versioning import version_view
from creativity_service.modules.iam.accounts import current_actor
from creativity_service.modules.iam.audit import append_event
from creativity_service.modules.iam.authorization import (
    action_allowed,
    effective_actions,
    require_platform,
)
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.iam.schemas import GrantInput, GrantView
from creativity_service.modules.iam.services import IamServices
from creativity_service.modules.models.policy import (
    CAPABILITY_LABELS,
    CAPABILITY_NAMES,
    HEALTH_LABELS,
    PROTOCOLS,
    configuration_digest,
    validate_endpoint,
    validate_parameters,
)
from creativity_service.modules.models.ports import DebugExecutor, ModelPriceReader
from creativity_service.modules.models.repositories import model_key, repository, required
from creativity_service.modules.models.schemas import (
    CapabilityView,
    ConnectionInput,
    ConnectionList,
    ConnectionView,
    CredentialInput,
    FrozenModel,
    ModelGrantInput,
    ModelInput,
    ModelList,
    ModelView,
    PriceView,
    ProviderInput,
    ProviderView,
)
from creativity_service.modules.models.tables import metadata
from creativity_service.modules.models.versioning import freeze, version_keys


def action(key: str, label: str) -> VisibleAction:
    return VisibleAction(action_key=key, label=label)


class ModelService:
    def __init__(
        self,
        engine: AsyncEngine,
        iam: IamServices,
        credentials: CredentialService,
        outbound: OutboundPolicy,
        executor: DebugExecutor | None = None,
        prices: ModelPriceReader | None = None,
    ) -> None:
        self.engine, self.iam, self.credentials, self.outbound = engine, iam, credentials, outbound
        self.executor, self.prices = executor, prices

    async def context(self, session: AdminSession) -> AuthContext:
        if not isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "请先进入渠道工作区", 403)
        context = session.context
        await self.iam.authorization.boundary(
            context, "model:manage", "channel", context.scope.channel_id
        )
        return context

    async def locked_use(
        self, uow: UnitOfWork, session: AdminSession, model_ids: list[str]
    ) -> None:
        member, grants = await self.iam.access.locked_policy(uow, session, "model:manage")
        if not isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "请先进入渠道工作区", 403)
        scope = session.context.scope
        for model_id in model_ids:
            actions = effective_actions(
                member,
                grants,
                scope.environment,
                scope.data_scope_id or "",
                "model",
                model_id,
            )
            if not action_allowed(actions, "run:create"):
                raise ServiceError("FORBIDDEN", "缺少候选模型的使用授权", 403)

    @asynccontextmanager
    async def mutation(
        self,
        session: AdminSession,
        table: str,
        record_id: str,
        extra: list[ResourceKey] | None = None,
    ) -> AsyncIterator[tuple[UnitOfWork, AuthContext]]:
        context = await self.context(session)
        scope, audit_id = context.scope, new_id("audit")
        keys = [
            model_key(scope.channel_id),
            content_key(scope),
            policy_key("system"),
            policy_key(scope.channel_id),
            record_key(scope.channel_id, table, record_id),
            record_key(scope.channel_id, "audit_events", audit_id),
            *(extra or []),
        ]
        async with transaction(self.engine, scope, keys) as uow:
            await self.iam.access.locked_policy(uow, session, "model:manage")
            await DeletionGuard(scope).check(
                uow,
                [ContentRef("model" if table == "models" else table.removesuffix("s"), record_id)],
            )
            yield uow, context
            await append_audit(
                uow,
                context,
                audit_id,
                "model:manage",
                "model" if table == "models" else table.removesuffix("s"),
                record_id,
            )

    async def provider_rows(self) -> list[dict[str, Any]]:
        table = metadata.tables["provider_catalog"]
        async with self.engine.connect() as connection:
            return [
                dict(row)
                for row in (
                    await connection.execute(select(table).where(table.c.channel_id == "system"))
                ).mappings()
            ]

    async def providers(self, session: AdminSession) -> list[ProviderView]:
        if isinstance(session.context, AuthContext):
            await self.context(session)
        else:
            require_platform(session.account, "channel:govern")
        return [
            ProviderView(
                **{
                    k: row[k]
                    for k in ("id", "code", "name", "protocols", "template_content", "revision")
                }
            )
            for row in await self.provider_rows()
        ]

    async def save_provider(self, session: AdminSession, body: ProviderInput) -> ProviderView:
        require_platform(session.account, "channel:govern")
        if not isinstance(session.context, ControlAuthContext):
            raise ServiceError("FORBIDDEN", "请在平台工作区维护供应商字典", 403)
        if len(set(body.protocols)) != len(body.protocols) or set(body.template_content) - {
            "protocol",
            "endpoint",
            "timeout_seconds",
        }:
            raise ServiceError("MODEL_TEMPLATE_INVALID", "连接模板只能包含协议、地址和超时", 422)
        if body.template_content:
            protocol = body.template_content.get("protocol")
            if protocol not in body.protocols or not isinstance(
                body.template_content.get("endpoint"), str
            ):
                raise ServiceError("MODEL_TEMPLATE_INVALID", "模板协议和地址不完整", 422)
            validate_endpoint(protocol, body.template_content["endpoint"])
        name = body.name.strip()
        code = name_code(name, "供应商")
        table, now = metadata.tables["provider_catalog"], utcnow()
        audit_id = new_id("audit")
        # 编码仅在创建时生成；编辑按原标识定位，名称变更不破坏既有模型连接。
        identifier = body.id if body.revision is not None else "provider_" + code
        if body.revision is not None and identifier is None:
            identifier = "provider_" + (body.code or code)
        if identifier is None or (body.revision is None and body.id is not None):
            raise ServiceError("VALIDATION_ERROR", "供应商标识与保存操作不一致", 422)
        scope = ControlScope(purpose="catalog", actor_id=session.account.id)
        async with transaction(
            self.engine,
            scope,
            [
                policy_key("system"),
                record_key("system", table.name, identifier),
                record_key("system", "audit_events", audit_id),
            ],
        ) as uow:
            await current_actor(uow, session, "channel:govern")
            current = (
                (
                    await uow.connection.execute(
                        select(table).where(
                            table.c.channel_id == "system", table.c.id == identifier
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (current is None and body.revision is not None) or (
                current is not None and current["revision"] != body.revision
            ):
                raise ServiceError(
                    "REVISION_CONFLICT",
                    "名称首字母生成的供应商编码已存在，请调整名称"
                    if current is not None and body.revision is None
                    else "供应商已变更，请刷新后重试",
                    409,
                )
            row = {
                **body.model_dump(exclude={"revision", "id", "code"}),
                "name": name,
                "code": current["code"] if current else code,
                "id": identifier,
                "channel_id": "system",
                "created_at": current["created_at"] if current else now,
                "updated_at": now,
                "revision": current["revision"] + 1 if current else 1,
            }
            validate_row(table, row)
            if current:
                await uow.connection.execute(
                    update(table)
                    .where(table.c.channel_id == "system", table.c.id == identifier)
                    .values(**row)
                )
            else:
                await uow.connection.execute(insert(table).values(**row))
            await append_event(
                uow,
                audit_id,
                session.account.id,
                session.context.request_id,
                "model:manage",
                "provider",
                identifier,
                ["name"],
            )
        return ProviderView(
            **{
                k: row[k]
                for k in ("id", "code", "name", "protocols", "template_content", "revision")
            }
        )

    async def store_credential(self, session: AdminSession, body: CredentialInput) -> str:
        context = await self.context(session)
        return await self.credentials.store(
            context, "model", SecretBytes(body.secret.get_secret_value().encode())
        )

    async def save_connection(
        self, session: AdminSession, body: ConnectionInput, identifier: str | None = None
    ) -> ConnectionView:
        context = await self.context(session)
        endpoint = validate_endpoint(body.protocol, body.endpoint)
        await self.outbound.validate(context.scope, "model", endpoint)
        providers = {p["id"]: p for p in await self.provider_rows()}
        if (
            body.provider_id not in providers
            or body.protocol not in providers[body.provider_id]["protocols"]
        ):
            raise ServiceError("MODEL_PROVIDER_INVALID", "供应商未登记该协议", 422)
        identifier, version_id = identifier or new_id("connection"), new_id("version")
        keys = version_keys(
            context.scope.channel_id, version_id, "model_connection", identifier, []
        )
        async with self.mutation(session, "model_connections", identifier, keys) as (uow, context):
            credential = await required(
                uow.connection, context.scope, "credentials", body.credential_ref
            )
            if credential["purpose"] != "model" or credential["state"] != "ACTIVE":
                raise ServiceError("CREDENTIAL_UNAVAILABLE", "模型凭据不可用", 403)
            repo = repository(context.scope, "model_connections")
            existing = await repo.get(uow.connection, identifier)
            values = {
                **body.model_dump(exclude={"revision"}),
                "endpoint": endpoint,
                "current_version_id": version_id,
                "health_status": "UNKNOWN",
                "health_reason": None,
                "health_checked_at": None,
            }
            values["validation_revision"] = (
                existing["validation_revision"] if existing else 1
            ) + int(
                existing is not None
                and any(
                    existing[k] != values[k] for k in ("protocol", "endpoint", "timeout_seconds")
                )
            )
            if existing:
                if body.revision is None:
                    raise ServiceError("REVISION_CONFLICT", "修改连接必须提交修订号", 409)
                row = await repo.change(uow, identifier, body.revision, values)
            else:
                if body.revision is not None:
                    raise ServiceError("NOT_FOUND", "连接不存在", 404)
                row = await repo.add(uow, identifier, values)
            await freeze(
                uow,
                context,
                version_id,
                "model_connection",
                identifier,
                str(row["revision"]),
                values,
                [],
            )
        return await self.connection_view(row)

    async def connection_view(
        self, row: dict[str, Any], providers: list[dict[str, Any]] | None = None
    ) -> ConnectionView:
        provider = next(
            (
                p
                for p in (providers if providers is not None else await self.provider_rows())
                if p["id"] == row["provider_id"]
            ),
            None,
        )
        return ConnectionView(
            **{k: row[k] for k in ConnectionInput.model_fields},
            id=row["id"],
            provider_name=provider["name"] if provider else None,
            protocol_name=PROTOCOLS[row["protocol"]].name,
            status_label="启用" if row["status"] == "ACTIVE" else "停用",
            health_status=row["health_status"],
            health_label=HEALTH_LABELS[row["health_status"]],
            health_reason=row["health_reason"],
            health_checked_at=row["health_checked_at"],
            current_version_id=row["current_version_id"],
            actions=[action("edit", "编辑")],
        )

    async def connections(self, session: AdminSession) -> ConnectionList:
        context = await self.context(session)
        async with self.engine.connect() as connection:
            rows = await repository(context.scope, "model_connections").find(connection)
        providers = await self.provider_rows()
        return ConnectionList(
            items=[await self.connection_view(row, providers) for row in rows],
            actions=[action("create", "新增连接"), action("credential", "保存凭据")],
        )

    async def save_model(
        self, session: AdminSession, body: ModelInput, identifier: str | None = None
    ) -> ModelView:
        context = await self.context(session)
        identifier, version_id = identifier or new_id("model"), new_id("version")
        async with self.engine.connect() as database:
            initial = await required(
                database, context.scope, "model_connections", body.connection_id
            )
        dependencies = [initial["current_version_id"]]
        keys = version_keys(context.scope.channel_id, version_id, "model", identifier, dependencies)
        async with self.mutation(session, "models", identifier, keys) as (uow, context):
            connection = await required(
                uow.connection, context.scope, "model_connections", body.connection_id
            )
            if connection["revision"] != initial["revision"]:
                raise ServiceError("REVISION_CONFLICT", "连接已变更，请刷新后重试", 409)
            validate_parameters(connection["protocol"], body.parameter_allowlist, body.parameters)
            if body.provider_model_name.strip() != body.provider_model_name or any(
                ord(c) < 32 for c in body.provider_model_name
            ):
                raise ServiceError("MODEL_NAME_INVALID", "供应商模型名不正确", 422)
            repo = repository(context.scope, "models")
            existing = await repo.get(uow.connection, identifier)
            if any(
                r["id"] != identifier
                for r in await repo.find(uow.connection, model_code=body.model_code)
            ):
                raise ServiceError("CODE_EXISTS", "模型别名已存在", 409)
            if existing:
                await required(
                    uow.connection, context.scope, "model_connections", existing["connection_id"]
                )
            if existing and existing["model_code"] != body.model_code:
                raise ServiceError("IMMUTABLE_FIELD", "模型稳定别名不能修改", 422)
            values = {
                **body.model_dump(exclude={"revision"}),
                "current_version_id": version_id,
                "capabilities": {},
                "verified_at": None,
            }
            values["validation_revision"] = (
                existing["validation_revision"] if existing else 1
            ) + int(
                existing is not None
                and any(
                    existing[k] != values[k]
                    for k in (
                        "connection_id",
                        "provider_model_name",
                        "context_limit",
                        "parameters",
                        "parameter_allowlist",
                    )
                )
            )
            if existing:
                if body.revision is None:
                    raise ServiceError("REVISION_CONFLICT", "修改模型必须提交修订号", 409)
                if configuration_digest(values, connection) == configuration_digest(
                    existing, connection
                ):
                    values.update(
                        capabilities=existing["capabilities"], verified_at=existing["verified_at"]
                    )
                row = await repo.change(uow, identifier, body.revision, values)
            else:
                if body.revision is not None:
                    raise ServiceError("NOT_FOUND", "模型不存在", 404)
                row = await repo.add(uow, identifier, values)
            await freeze(
                uow,
                context,
                version_id,
                "model",
                identifier,
                str(row["revision"]),
                {
                    **body.model_dump(exclude={"revision"}),
                    "connection_version_id": connection["current_version_id"],
                    "validation_revision": row["validation_revision"],
                    "config_digest": configuration_digest(row, connection),
                },
                dependencies,
            )
        return await self.model_view(context, row)

    async def model_view(
        self,
        context: AuthContext,
        row: dict[str, Any],
        *,
        connection: dict[str, Any] | None = None,
        providers: list[dict[str, Any]] | None = None,
        permissions: frozenset[str] | None = None,
    ) -> ModelView:
        if connection is None:
            async with self.engine.connect() as conn:
                connection = await required(
                    conn, context.scope, "model_connections", row["connection_id"]
                )
        digest_value = configuration_digest(row, connection)
        provider = next(
            (
                p
                for p in (providers if providers is not None else await self.provider_rows())
                if p["id"] == connection["provider_id"]
            ),
            None,
        )
        capabilities = []
        for key, name in CAPABILITY_NAMES.items():
            evidence = row["capabilities"].get(key, {})
            valid = (
                evidence.get("config_digest") == digest_value and evidence.get("evidence") == "live"
            )
            state = evidence.get("state", "UNVERIFIED") if valid else "UNVERIFIED"
            capabilities.append(
                CapabilityView(
                    capability=key,
                    name=name,
                    state=state,
                    label=CAPABILITY_LABELS[state],
                    verified_at=evidence.get("verified_at") if valid else None,
                    reason=evidence.get("reason") if valid else "当前配置尚未通过真实验证",
                )
            )
        actions = [action("edit", "编辑"), action("history", "历史版本")]
        try:
            if permissions is None:
                permissions = await self.iam.authorization.allowed_actions(
                    context, "model", row["id"]
                )
            if "run:create" in permissions:
                actions.insert(1, action("test", "能力验证"))
        except ServiceError as exc:
            if exc.status not in {403, 404}:
                raise
        return ModelView(
            protocol=connection["protocol"],
            usage_subsets={
                "cache_read": "input",
                **(
                    {"cache_write": "input"}
                    if connection["protocol"] == "anthropic_messages"
                    else {}
                ),
            },
            **{k: row[k] for k in ModelInput.model_fields},
            id=row["id"],
            connection_name=connection["name"],
            provider_name=provider["name"] if provider else None,
            protocol_name=PROTOCOLS[connection["protocol"]].name,
            status_label="启用" if row["status"] == "ACTIVE" else "停用",
            current_version_id=row["current_version_id"],
            config_digest=digest_value,
            capabilities=capabilities,
            verified_at=max((c.verified_at for c in capabilities if c.verified_at), default=None),
            parameter_reasons={
                name: "该模型未开放此参数"
                for name in PROTOCOLS[connection["protocol"]].parameters
                if name not in row["parameter_allowlist"]
            },
            actions=actions,
        )

    async def models(self, session: AdminSession) -> ModelList:
        from creativity_service.modules.iam.reading import require_action, resource_state

        if not isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "请先进入渠道工作区", 403)
        context = session.context
        policy = await self.iam.authorization.read_policy(context)
        require_action(policy.actions("channel", context.scope.channel_id), "model:manage")
        async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
            rows = await repository(context.scope, "models").find(uow.connection)
            connections = await repository(context.scope, "model_connections").get_many(
                uow.connection, [row["connection_id"] for row in rows]
            )
            blocked = await DeletionGuard(context.scope).blocked_refs(
                uow, [ContentRef("model", row["id"]) for row in rows]
            )
            visible = [
                row
                for row in rows
                if row["connection_id"] in connections
                and ContentRef("model", row["id"]) not in blocked
            ]
        providers = await self.provider_rows()
        return ModelList(
            items=[
                await self.model_view(
                    context,
                    row,
                    connection=connections[row["connection_id"]],
                    providers=providers,
                    permissions=policy.actions(
                        "model", row["id"], resource_state(context, "model", row)
                    )
                    if row["status"] == "ACTIVE"
                    else frozenset(),
                )
                for row in visible
            ],
            actions=[action("create", "新增模型")],
        )

    async def detail(self, session: AdminSession, identifier: str) -> ModelView:
        context = await self.context(session)
        async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("model", identifier)])
            row = await required(uow.connection, context.scope, "models", identifier)
        return await self.model_view(context, row)

    async def history(
        self, session: AdminSession, resource_type: str, identifier: str
    ) -> list[ResourceVersion]:
        context = await self.context(session)
        async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef(resource_type, identifier)])
            table = {
                "model": "models",
                "model_connection": "model_connections",
                "model_route": "model_routes",
            }.get(resource_type)
            if table is None:
                raise ServiceError("NOT_FOUND", "模型资源不存在", 404)
            current = await required(uow.connection, context.scope, table, identifier)
            # 公共版本表按渠道保存；连接所属环境仍须由当前资源显式核验。
            if resource_type == "model":
                await required(
                    uow.connection, context.scope, "model_connections", current["connection_id"]
                )
            rows = await repository(context.scope, "resource_versions").find(
                uow.connection, resource_type=resource_type, resource_id=identifier
            )
            visible = [
                row
                for row in rows
                if resource_type != "model_route"
                or not any(
                    m["scope"]["environment"] != context.scope.environment
                    for m in row["content"].get("models", [])
                )
            ]
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("version", r["id"]) for r in visible]
            )
            result = [version_view(row) for row in visible]
            return result

    def snapshot(
        self, context: AuthContext, model: dict[str, Any], connection: dict[str, Any]
    ) -> FrozenModel:
        return FrozenModel(
            scope=context.scope,
            model_id=model["id"],
            model_version_id=model["current_version_id"],
            model_revision=model["revision"],
            model_name=model["name"],
            connection_id=connection["id"],
            connection_version_id=connection["current_version_id"],
            connection_revision=connection["revision"],
            provider_credential_id=connection["credential_ref"],
            protocol=connection["protocol"],
            endpoint=connection["endpoint"],
            provider_model_name=model["provider_model_name"],
            timeout_seconds=connection["timeout_seconds"],
            parameters=model["parameters"],
            parameter_allowlist=model["parameter_allowlist"],
            config_digest=configuration_digest(model, connection),
        )

    async def price(self, session: AdminSession, model_id: str) -> PriceView:
        context = await self.context(session)
        await self.detail(session, model_id)
        if self.prices is None:
            return PriceView(
                model_id=model_id,
                available=False,
                source=None,
                currency=None,
                price_items=[],
                reason="价格服务尚未接入",
            )
        return await self.prices.read(context, model_id)

    async def grant(
        self, session: AdminSession, channel_id: str, model_id: str, body: ModelGrantInput
    ) -> GrantView:
        context = await self.context(session)
        if channel_id != context.scope.channel_id:
            raise ServiceError("NOT_FOUND", "模型不存在", 404)
        await self.detail(session, model_id)
        grant_id = digest(["model", model_id, body.grantee_type, body.grantee_id])
        return await self.iam.access.put_grant(
            session,
            channel_id,
            grant_id,
            GrantInput(
                grantee_type=body.grantee_type,
                grantee_id=body.grantee_id,
                resource_type="model",
                resource_id=model_id,
                allowed_actions=["run:create"],
                environments=[context.scope.environment],
                data_scopes=[context.scope.data_scope_id or ""],
                revision=body.revision,
            ),
        )

    async def grants(self, session: AdminSession, model_id: str) -> list[GrantView]:
        context = await self.context(session)
        await self.detail(session, model_id)
        items = await self.iam.access.list_grants(session, context.scope.channel_id)
        return [
            g
            for g in items
            if "run:create" in g.allowed_actions
            and (
                (g.resource_type == "model" and g.resource_id in {model_id, "*"})
                or (g.resource_type == "channel" and g.resource_id == context.scope.channel_id)
                or (
                    g.resource_type == "data_scope" and g.resource_id == context.scope.data_scope_id
                )
            )
        ]
