"""连接配置、显式能力调用及脱敏契约测试的服务边界。"""

import asyncio
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.locking import record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, new_id, utcnow
from creativity_service.core.security.outbound import OutboundPolicy
from creativity_service.integrations.business.base import (
    CONTRACT_VERSION,
    ERRORS,
    OPERATION_NAMES,
    BusinessCall,
    BusinessResult,
    Operation,
    business_error,
)
from creativity_service.integrations.business.base.validation import validate_result
from creativity_service.integrations.business.registry import BusinessRegistry
from creativity_service.modules.channels.repositories import required, rows
from creativity_service.modules.iam.access import ENVIRONMENT_NAMES
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.integrations.authorization import require_management
from creativity_service.modules.integrations.delegation import DelegationService
from creativity_service.modules.integrations.repositories import configuration_key, repository
from creativity_service.modules.integrations.schemas import (
    AdapterOption,
    BusinessCapabilityView,
    ContractCaseResult,
    ContractTestInput,
    ContractTestView,
    IntegrationCreate,
    IntegrationEdit,
    IntegrationList,
    IntegrationOptions,
    IntegrationView,
    NamedOption,
)


class IntegrationService:
    def __init__(
        self,
        engine: AsyncEngine,
        authorization: IamAuthorization,
        registry: BusinessRegistry,
        outbound: OutboundPolicy,
        delegation: DelegationService,
    ) -> None:
        self.engine, self.authorization, self.registry = engine, authorization, registry
        self.outbound, self.delegation = outbound, delegation

    async def require(self, context: AuthContext) -> None:
        if context.principal_type != "management":
            raise ServiceError("FORBIDDEN", "此操作需要管理身份", 403)
        await self.authorization.boundary(
            context, "integration:manage", "channel", context.scope.channel_id
        )

    async def row(self, context: AuthContext, integration_id: str) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            value = await repository(context.scope, "integrations").get(connection, integration_id)
        if not value:
            raise ServiceError("NOT_FOUND", "业务连接不存在", 404)
        return value

    async def view(self, context: AuthContext, row: dict[str, Any]) -> IntegrationView:
        async with self.engine.connect() as connection:
            domain = await required(
                connection,
                "data_scopes",
                context.scope.channel_id,
                id=row["scope_mapping_ref"],
                environment=context.scope.environment,
            )
        registered = self.registry.items.get((row["adapter_code"], row["adapter_version"]))
        return IntegrationView(
            **{k: row[k] for k in IntegrationView.model_fields if k in row},
            integration_id=row["id"],
            environment_name=ENVIRONMENT_NAMES[context.scope.environment],
            data_scope_name=domain["name"],
            adapter_name=registered.name if registered else "适配器未安装",
            health_name={"UNKNOWN": "待检测", "HEALTHY": "正常", "DEGRADED": "异常"}[row["health"]],
            status_name={"ACTIVE": "启用", "DISABLED": "停用"}[row["status"]],
            actions=[
                VisibleAction(action_key=k, label=v)
                for k, v in (
                    ("integration:edit", "编辑"),
                    ("integration:test", "契约测试"),
                )
            ],
        )

    async def list_integrations(self, context: AuthContext) -> IntegrationList:
        await self.require(context)
        async with self.engine.connect() as connection:
            values = await repository(context.scope, "integrations").find(connection)
        return IntegrationList(
            items=[await self.view(context, v) for v in values],
            actions=[VisibleAction(action_key="integration:create", label="新建旧 HTTP 连接")],
        )

    async def detail(self, context: AuthContext, integration_id: str) -> IntegrationView:
        await self.require(context)
        return await self.view(context, await self.row(context, integration_id))

    async def options(self, context: AuthContext) -> IntegrationOptions:
        await self.require(context)
        member = await self.authorization.authentication.active_member(context)
        async with self.engine.connect() as connection:
            clients = await rows(
                connection,
                "service_clients",
                context.scope.channel_id,
                environment=context.scope.environment,
                status="ACTIVE",
            )
        return IntegrationOptions(
            adapters=[
                AdapterOption(
                    code=i.code, name=i.name, version=i.version, capabilities=list(i.capabilities)
                )
                for i in self.registry.items.values()
            ],
            clients=[
                NamedOption(value=c["id"], label=c["name"])
                for c in clients
                if set(c["data_scopes"]) <= set(member.data_scopes)
            ],
            operations=[NamedOption(value=k, label=v) for k, v in OPERATION_NAMES.items()],
        )

    async def validate(self, context: AuthContext, body: IntegrationCreate) -> None:
        item = self.registry.resolve(body.adapter_code, body.adapter_version)
        operations = set(body.allowed_operations)
        if (
            len(operations) != len(body.allowed_operations)
            or not body.name.strip()
            or operations != set(body.operation_paths)
            or not operations <= {c.operation for c in item.capabilities}
        ):
            raise ServiceError("VALIDATION_ERROR", "连接能力和接口路径不匹配", 422)
        parts = urlsplit(body.business_endpoint)
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ServiceError("VALIDATION_ERROR", "业务地址不能包含查询、片段或凭据", 422)
        for path in body.operation_paths.values():
            if (
                not path.startswith("/")
                or path.startswith("//")
                or "?" in path
                or "#" in path
                or "\\" in path
                or any(p in {".", ".."} for p in unquote(path).split("/"))
            ):
                raise ServiceError("VALIDATION_ERROR", "能力路径必须为固定的源服务路径", 422)
            await self.outbound.validate(
                context.scope, "http_tool", body.business_endpoint.rstrip("/") + path
            )

    async def save(
        self, context: AuthContext, body: IntegrationCreate, integration_id: str | None = None
    ) -> IntegrationView:
        await self.require(context)
        await self.validate(context, body)
        scope, audit_id = context.scope, new_id("audit")
        integration_id = integration_id or new_id("integration")
        async with transaction(
            self.engine,
            scope,
            [
                configuration_key(scope),
                policy_key(scope.channel_id),
                policy_key("system"),
                record_key(scope.channel_id, "integrations", integration_id),
                record_key(scope.channel_id, "audit_events", audit_id),
            ],
        ) as uow:
            await require_management(uow, context, "integration:manage")
            credential = await Repository(core_metadata.tables["credentials"], scope).get(
                uow.connection, body.credential_ref
            )
            if (
                not credential
                or credential["purpose"] != "http_tool"
                or credential["state"] != "ACTIVE"
            ):
                raise ServiceError(
                    "CREDENTIAL_UNAVAILABLE", "请选择当前环境有效的业务服务凭据", 422
                )
            repo = repository(scope, "integrations")
            if any(
                v["id"] != integration_id
                for v in await repo.find(uow.connection, name=body.name.strip())
            ):
                raise ServiceError("INTEGRATION_EXISTS", "业务连接名称已存在", 409)
            values = {
                **body.model_dump(exclude={"revision", "status"}),
                "name": body.name.strip(),
                "health": "UNKNOWN",
                "contract_version": CONTRACT_VERSION,
                "scope_mapping_ref": scope.data_scope_id,
                "status": body.status if isinstance(body, IntegrationEdit) else "ACTIVE",
            }
            row = (
                await repo.change(uow, integration_id, body.revision, values)
                if isinstance(body, IntegrationEdit)
                else await repo.add(uow, integration_id, values)
            )
            await append_audit(
                uow,
                context,
                audit_id,
                "integration.save",
                "integration",
                integration_id,
                {"revision": row["revision"]},
            )
        return await self.view(context, row)

    async def capabilities(
        self, context: AuthContext, integration_id: str
    ) -> list[BusinessCapabilityView]:
        await self.require(context)
        row = await self.row(context, integration_id)
        registered = self.registry.resolve(row["adapter_code"], row["adapter_version"])
        tests = await self.tests(context, integration_id)
        latest_results: dict[str, bool] = {}
        for test in tests:
            if test.config_revision == row["revision"]:
                for result in test.results:
                    latest_results.setdefault(result.operation, result.passed)
        verified = {operation for operation, passed in latest_results.items() if passed}
        declared = {c.operation: c for c in registered.capabilities}
        return [
            BusinessCapabilityView(
                operation=op,
                name=name,
                public=declared[op].public if op in declared else False,
                required_actions=declared[op].required_actions if op in declared else [],
                supported=op in declared,
                allowed=op in row["allowed_operations"],
                verified=op in verified,
            )
            for op, name in OPERATION_NAMES.items()
        ]

    async def invoke(
        self,
        context: AuthContext,
        integration_id: str,
        operation: Operation,
        arguments: dict[str, JsonValue],
        run_id: str,
    ) -> BusinessResult:
        await self.authorization.authentication.revalidate(context)
        row = await self.row(context, integration_id)
        if row["status"] != "ACTIVE":
            raise business_error("BUSINESS_UNAVAILABLE")
        registered = self.registry.resolve(row["adapter_code"], row["adapter_version"])
        capability = next((c for c in registered.capabilities if c.operation == operation), None)
        if capability is None or operation not in row["allowed_operations"]:
            raise business_error("BUSINESS_UNSUPPORTED")
        if set(arguments) & {
            "channel_id",
            "environment",
            "data_scope_id",
            "subject_id",
            "subject_type",
            "identity",
            "url",
            "endpoint",
            "headers",
            "credential_ref",
            "sql",
        }:
            raise business_error("BUSINESS_FORBIDDEN")
        if context.actor_id:
            await self.require(context)
            for action in capability.required_actions:
                await self.authorization.boundary(
                    context, action, "channel", context.scope.channel_id
                )
            context = context.model_copy(
                update={"granted_actions": frozenset(capability.required_actions)}
            )
        else:
            authority = await self.delegation.read_current(context)
            identity = await self.authorization.authentication.service_identity(context)
            effective = (
                authority.actions
                & authority.agent_actions
                & identity.client_actions
                & identity.key_actions
            )
            if not set(capability.required_actions) <= effective:
                raise business_error("BUSINESS_FORBIDDEN")
            if context.scope.subject_type == "anonymous" and not capability.public:
                raise business_error("BUSINESS_FORBIDDEN")
            context = context.model_copy(update={"granted_actions": effective})
        async with self.engine.connect() as connection:
            domain = await required(
                connection,
                "data_scopes",
                context.scope.channel_id,
                id=row["scope_mapping_ref"],
                environment=context.scope.environment,
            )
        if domain["status"] != "ACTIVE" or domain["id"] != context.scope.data_scope_id:
            raise business_error("BUSINESS_FORBIDDEN")
        call = BusinessCall(
            context,
            run_id,
            operation,
            arguments,
            {
                **row,
                "source_scope": {
                    "type": domain["external_scope_type"],
                    "id": domain["external_scope_id"],
                },
            },
        )
        try:
            async with asyncio.timeout(20):
                result = await getattr(registered.adapter, operation)(call)
        except TimeoutError:
            raise business_error("BUSINESS_TIMEOUT") from None
        current = await self.row(context, integration_id)
        if current["revision"] != row["revision"] or current["status"] != "ACTIVE":
            raise ServiceError("REVISION_CONFLICT", "业务连接已变更，请重新请求", 409)
        await self.authorization.authentication.revalidate(context)
        if not context.actor_id:
            await self.delegation.read_current(context)
        return validate_result(result, operation, context.scope)

    @staticmethod
    def test_view(row: dict[str, Any]) -> ContractTestView:
        return ContractTestView(
            **{k: row[k] for k in ContractTestView.model_fields if k in row},
            test_id=row["id"],
            state_name={"PASSED": "通过", "FAILED": "未通过", "STALE": "配置已变更"}[row["state"]],
        )

    async def tests(self, context: AuthContext, integration_id: str) -> list[ContractTestView]:
        await self.require(context)
        await self.row(context, integration_id)
        async with self.engine.connect() as connection:
            values = await repository(context.scope, "integration_tests").find(
                connection, integration_id=integration_id
            )
        return [
            self.test_view(row)
            for row in sorted(values, key=lambda r: r["created_at"], reverse=True)
        ]

    async def test(
        self, context: AuthContext, integration_id: str, body: ContractTestInput
    ) -> ContractTestView:
        await self.require(context)
        row = await self.row(context, integration_id)
        if row["revision"] != body.revision:
            raise ServiceError("REVISION_CONFLICT", "连接配置已变更，请刷新后测试", 409)
        if len(body.cases) != len({c.operation for c in body.cases}):
            raise ServiceError("VALIDATION_ERROR", "测试能力不能重复", 422)
        results = []
        test_id, audit_id = new_id("int_test"), new_id("audit")
        for case in body.cases:
            try:
                value = await self.invoke(
                    context, integration_id, case.operation, dict(case.arguments), test_id
                )
                results.append(
                    ContractCaseResult(
                        operation=case.operation,
                        name=OPERATION_NAMES[case.operation],
                        passed=True,
                        message="契约验证通过",
                        item_count=len(value.items),
                    )
                )
            except ServiceError as exc:
                if exc.code not in ERRORS:
                    raise
                results.append(
                    ContractCaseResult(
                        operation=case.operation,
                        name=OPERATION_NAMES[case.operation],
                        passed=False,
                        code=exc.code,
                        message=ERRORS[exc.code][0],
                    )
                )
        scope = context.scope
        async with transaction(
            self.engine,
            scope,
            [
                configuration_key(scope),
                policy_key(scope.channel_id),
                policy_key("system"),
                record_key(scope.channel_id, "integrations", integration_id),
                record_key(scope.channel_id, "integration_tests", test_id),
                record_key(scope.channel_id, "audit_events", audit_id),
            ],
        ) as uow:
            await require_management(uow, context, "integration:manage")
            repo = repository(scope, "integrations")
            latest = await repo.get(uow.connection, integration_id)
            if not latest or latest["revision"] != body.revision:
                raise ServiceError("REVISION_CONFLICT", "测试期间连接配置已变更，请重新测试", 409)
            passed = all(r.passed for r in results)
            # 健康信息不改动配置修订；同一配置的验证快照才能作为能力证据。
            from sqlalchemy import update

            await uow.connection.execute(
                update(repo.table)
                .where(repo.predicate(), repo.table.c.id == integration_id)
                .values(health="HEALTHY" if passed else "DEGRADED", updated_at=utcnow())
            )
            test_row = await repository(scope, "integration_tests").add(
                uow,
                test_id,
                {
                    "integration_id": integration_id,
                    "config_revision": body.revision,
                    "cases": [c.operation for c in body.cases],
                    "results": [r.model_dump(mode="json") for r in results],
                    "capabilities": [r.operation for r in results if r.passed],
                    "state": "PASSED" if passed else "FAILED",
                },
            )
            await append_audit(
                uow,
                context,
                audit_id,
                "integration.test",
                "integration",
                integration_id,
                {"state": test_row["state"], "revision": body.revision},
            )
        return self.test_view(test_row)
