"""幂等、预算、会话消息与可靠投递意图在同一受理事务内提交。"""

from datetime import timedelta
from typing import Any

from jsonschema import Draft202012Validator

from creativity_service.core.context import AuthContext
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import (
    RunInput,
    ServiceError,
    digest,
    new_id,
    unavailable,
    utcnow,
)
from creativity_service.core.versioning import validate_schema
from creativity_service.modules.runs.base import RunKernel
from creativity_service.modules.runs.repositories import (
    idempotency_key,
    one,
    rows,
    save,
    verify_scope,
)
from creativity_service.modules.runs.schemas import TERMINAL, AdmissionReceipt


class AdmissionService(RunKernel):
    @staticmethod
    def identity_scope(context: AuthContext, request: RunInput) -> tuple[str, str, str]:
        if context.principal_type == "management" and context.actor_id:
            kind, identity_id = "management", context.actor_id
        elif context.principal_type == "service" and context.client_id:
            kind, identity_id = "service", context.client_id
        else:
            raise ServiceError("UNAUTHENTICATED", "受理必须使用已认证调用身份", 401)
        return (
            kind,
            identity_id,
            digest([context.scope.model_dump(), kind, identity_id, request.agent_code]),
        )

    async def replay(
        self, context: AuthContext, run_id: str, request_digest: str, stored_digest: str
    ) -> AdmissionReceipt:
        if request_digest != stored_digest:
            raise ServiceError("IDEMPOTENCY_CONFLICT", "相同幂等键的请求内容不同", 409)
        await self.authorization.require(context, "run:read", run_id)
        async with transaction(self.engine, context.scope, self.keys(context, run_id)) as uow:
            row = await self.locked_run(uow, run_id)
            if context.client_id and row["client_id"] != context.client_id:
                raise ServiceError("NOT_FOUND", "运行记录不存在", 404)
            await self.guard(uow, row)
            return self.receipt(row)

    async def admit_run(
        self, context: AuthContext, request: RunInput, key: str, *, parent_run_id: str | None = None
    ) -> AdmissionReceipt:
        if not key or len(key) > 128:
            raise ServiceError("IDEMPOTENCY_KEY_REQUIRED", "请提供有效幂等键", 422)
        kind, identity_id, scope_digest = self.identity_scope(context, request)
        await self.authorization.require(context, "run:create", "new")
        # 会话消息幂等先于发布解析，断网重发不受新版本、归档或新的 HTTP 幂等键影响。
        turn_replay = getattr(self.turns, "replay", None)
        if request.conversation_id and turn_replay:
            assert self.turns is not None
            await self.authorization.require(context, "conversation:read", request.conversation_id)
            await self.authorization.require(context, "conversation:write", request.conversation_id)
            async with transaction(
                self.engine, context.scope, self.turns.keys(context, request.conversation_id)
            ) as uow:
                existing_run = await turn_replay(uow, context, request)
            if existing_run:
                return await self.replay(context, existing_run, "", "")
        request_digest = digest([request.semantic_digest(), parent_run_id])
        async with self.engine.connect() as connection:
            previous = await one(
                connection,
                "run_idempotency",
                context.scope.channel_id,
                scope_digest=scope_digest,
                key=key,
            )
        if previous:
            return await self.replay(
                context, previous["run_id"], request_digest, previous["request_digest"]
            )
        if self.resolver is None:
            raise unavailable("冻结执行定义解析器")
        definition = await self.resolver.resolve(context, request)
        validate_schema(definition.input_schema)
        validate_schema(definition.output_schema)
        if any(
            s.target_version_id not in definition.version_ids or not s.read_only
            for s in definition.policy.steps
        ):
            raise ServiceError("EXECUTION_POLICY_INVALID", "步骤依赖或只读策略无效", 422)
        if request.conversation_id and self.turns is None:
            raise unavailable("会话受理事务钩子")
        run_id = new_id("run")
        plan = definition.admission_plan
        if plan:
            if plan.agent_id != definition.agent_id or plan.purpose != definition.purpose:
                raise ServiceError("EXECUTION_POLICY_INVALID", "预算候选与执行定义不符", 422)
            plan = plan.model_copy(update={"run_id": run_id})
        keys = [
            *self.keys(context, run_id, request.conversation_id),
            idempotency_key(context.scope, scope_digest, key),
            *self.versions.snapshot_keys(context.scope, run_id, list(definition.version_ids)),
        ]
        resolver_keys = getattr(self.resolver, "keys", None)
        if resolver_keys:
            keys.extend(resolver_keys(context, definition))
        source_link = digest(["run", run_id, request.conversation_id])
        input_links = [
            (digest([run_id, kind, identifier]), ContentRef(kind, identifier))
            for kind, identifier in definition.source_refs
        ]
        keys.extend(
            record_key(context.scope.channel_id, "source_links", identifier)
            for identifier, _ in input_links
        )
        if request.conversation_id:
            keys.append(record_key(context.scope.channel_id, "source_links", source_link))
        duplicate = None
        existing_run = None
        async with transaction(self.engine, context.scope, keys) as uow:
            if request.conversation_id and turn_replay:
                existing_run = await turn_replay(uow, context, request)
            duplicate = await one(
                uow.connection,
                "run_idempotency",
                context.scope.channel_id,
                scope_digest=scope_digest,
                key=key,
            )
            if not duplicate and not existing_run:
                validate_definition = getattr(self.resolver, "validate_in", None)
                if validate_definition:
                    await validate_definition(uow, context, definition)
                prepare = getattr(self.turns, "prepare", None)
                if request.conversation_id and prepare:
                    await prepare(uow, context, definition, request)
                if not Draft202012Validator(definition.input_schema).is_valid(request.input):
                    raise ServiceError("INPUT_SCHEMA_INVALID", "输入不符合当前智能体要求", 422)
                if request.conversation_id:
                    occupied = await rows(
                        uow.connection,
                        "run_occupancies",
                        context.scope.channel_id,
                        environment=context.scope.environment,
                        conversation_id=request.conversation_id,
                        state="HELD",
                    )
                    if occupied:
                        raise ServiceError("SESSION_BUSY", "会话正在处理上一条消息", 409)
                snapshot = await self.versions.snapshot_in(
                    uow,
                    context,
                    run_id,
                    list(definition.version_ids),
                    definition.purpose,
                    definition.output_schema,
                    frozen_versions=definition.frozen_spec.versions
                    if definition.frozen_spec
                    else None,
                )
                if snapshot.versions[0].resource_id != definition.agent_id:
                    raise ServiceError("SNAPSHOT_INVALID", "入口版本与智能体不一致", 422)
                await self.budgets.admit(uow, context, run_id, plan)
                now = utcnow()
                identity = context.model_copy(
                    update={
                        "principal_type": "worker",
                        "session_id": None,
                        "token_digest": None,
                        "granted_actions": frozenset(),
                    }
                )
                row = await save(
                    uow,
                    "runs",
                    run_id,
                    {
                        "source_type": context.principal_type,
                        "client_id": context.client_id,
                        "key_id": context.key_id,
                        "actor_id": context.actor_id,
                        "identity": identity.model_dump(mode="json"),
                        "agent_id": definition.agent_id,
                        "agent_code": request.agent_code,
                        "agent_name": definition.agent_name,
                        "agent_version_id": definition.version_ids[0],
                        "release_snapshot_id": snapshot.snapshot_id,
                        "purpose": definition.purpose,
                        "state": "QUEUED",
                        "deadline": now + timedelta(seconds=definition.policy.deadline_seconds),
                        "timeout_seconds": definition.policy.deadline_seconds,
                        "timeout_source": definition.policy.timeout_source,
                        "execution_policy": definition.policy.model_dump(mode="json"),
                        "conversation_id": request.conversation_id,
                        "parent_run_id": parent_run_id,
                        "input_ref": new_id("content"),
                        "event_sequence": 0,
                        "resources_released": False,
                        "recovery_count": 0,
                    },
                )
                await self.guard(uow, row)
                for identifier, source in input_links:
                    await DeletionGuard(context.scope).link(
                        uow, identifier, source, ContentRef("run", run_id)
                    )
                await save(
                    uow,
                    "run_contents",
                    row["input_ref"],
                    {"run_id": run_id, "kind": "input", "payload": request.input},
                )
                if definition.frozen_spec:
                    await self.content(
                        uow, row, "execution_spec", definition.frozen_spec.model_dump(mode="json")
                    )
                if request.conversation_id:
                    assert self.turns is not None
                    await self.turns.admit(uow, context, row, request)
                    await save(
                        uow,
                        "run_occupancies",
                        new_id("occupancy"),
                        {
                            "run_id": run_id,
                            "conversation_id": request.conversation_id,
                            "state": "HELD",
                        },
                    )
                    await DeletionGuard(context.scope).link(
                        uow,
                        source_link,
                        ContentRef("conversation", request.conversation_id),
                        ContentRef("run", run_id),
                    )
                await save(
                    uow,
                    "run_idempotency",
                    new_id("idempotency"),
                    {
                        "run_id": run_id,
                        "client_id": context.client_id,
                        "agent_id": definition.agent_id,
                        "identity_type": kind,
                        "identity_id": identity_id,
                        "scope_digest": scope_digest,
                        "key": key,
                        "request_digest": request_digest,
                        "expires_at": now + timedelta(hours=24),
                    },
                )
                await save(
                    uow,
                    "dispatch_outbox",
                    new_id("dispatch"),
                    {
                        "run_id": run_id,
                        "state": "PENDING",
                        "dispatch_attempts": 0,
                        "delivery_version": 0,
                        "next_attempt_at": now,
                    },
                )
                await self.event(uow, row, "accepted", self.receipt(row).model_dump(mode="json"))
        if existing_run:
            return await self.replay(context, existing_run, "", "")
        if duplicate:
            return await self.replay(
                context, duplicate["run_id"], request_digest, duplicate["request_digest"]
            )
        self.fault("admission_committed_before_publish")
        return self.receipt(row)

    async def cancel(self, context: AuthContext, run_id: str) -> AdmissionReceipt:
        context = await self.access_context(context, run_id)
        await self.authorization.require(context, "run:create", run_id)
        async with transaction(self.engine, context.scope, self.keys(context, run_id)) as uow:
            row = await self.locked_run(uow, run_id)
            if context.client_id and row["client_id"] != context.client_id:
                raise ServiceError("NOT_FOUND", "运行记录不存在", 404)
            if row["state"] == "QUEUED":
                await self.transition(uow, row, "CANCELLED")
            elif row["state"] == "RUNNING":
                await self.transition(uow, row, "CANCEL_REQUESTED")
        await self.after_commit(row)
        return self.receipt(row)

    async def rerun(
        self, context: AuthContext, run_id: str, key: str, input_value: dict[str, Any] | None = None
    ) -> AdmissionReceipt:
        if context.principal_type != "management":
            raise ServiceError("FORBIDDEN", "重新执行需要管理身份", 403)
        context = await self.access_context(context, run_id)
        await self.authorization.require(context, "run:content", run_id)
        async with transaction(self.engine, context.scope, self.keys(context, run_id)) as uow:
            row = await self.locked_run(uow, run_id)
            verify_scope(row, context.scope)
            if row["state"] not in TERMINAL:
                raise ServiceError("RUN_NOT_FINISHED", "请等待原运行结束", 409)
            await self.guard(uow, row)
            content = await one(
                uow.connection,
                "run_contents",
                context.scope.channel_id,
                id=row["input_ref"],
                run_id=run_id,
            )
            if content is None or content["payload"] is None:
                raise ServiceError("CONTENT_DELETED", "运行输入已清理", 410)
            request = RunInput(
                agent_code=row["agent_code"],
                conversation_id=row["conversation_id"],
                input=input_value if input_value is not None else content["payload"],
            )
            rerun_request = getattr(self.turns, "rerun_request", None)
            if row["conversation_id"] and rerun_request:
                request = await rerun_request(uow, context, row, request, key)
        if row["purpose"] != "production" and self.rerun_handler:
            return await self.rerun_handler(context, row, request, key)
        return await self.admit_run(context, request, key, parent_run_id=run_id)
