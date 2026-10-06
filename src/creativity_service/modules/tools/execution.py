"""固定工具版本执行管线；失败结果与外部文本均不能改变执行权限。"""

import asyncio
import json
from time import monotonic
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import (
    Attempt,
    EvidenceLocation,
    EvidenceRef,
    RunError,
    ToolResult,
)
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.primitives import (
    ServiceError,
    canonical_json,
    digest,
    new_id,
    unavailable,
    utcnow,
)
from creativity_service.integrations.tools import AdapterRequest, AdapterResult, ToolAdapterError
from creativity_service.modules.tools.ports import ToolCache, ToolRunPort
from creativity_service.modules.tools.schemas import RunToolGrant, ToolDefinition, ToolExecution
from creativity_service.modules.tools.services import ToolService
from creativity_service.modules.tools.validation import (
    artifact_references,
    validate_arguments,
    validate_definition,
    validate_json,
)

if TYPE_CHECKING:
    from creativity_service.modules.evaluations.fixtures import FixtureProvider


class RedisToolCache:
    def __init__(self, redis: Redis, prefix: str, engine: AsyncEngine | None = None) -> None:
        self.redis, self.prefix, self.engine = redis, prefix, engine

    async def get(self, key: str) -> ToolResult | None:
        value = await self.get_with_source(key)
        return value[0] if value else None

    async def get_with_source(self, key: str) -> tuple[ToolResult, str] | None:
        try:
            value = await self.redis.get(f"{self.prefix}:tools:{key}")
            if not value or self.engine is None:
                return None
            envelope = json.loads(value)
            result = ToolResult.model_validate(envelope["result"])
            if not isinstance(envelope["run_id"], str) or not envelope["run_id"]:
                raise ValueError("缓存缺少来源运行")
            async with transaction(self.engine, result.scope, [content_key(result.scope)]) as uow:
                await DeletionGuard(result.scope).check(
                    uow, [ContentRef("run", envelope["run_id"])]
                )
            return result, envelope["run_id"]
        except (RedisError, ValidationError, KeyError, ValueError, TypeError, ServiceError):
            await self.delete(key)
            return None

    async def delete(self, key: str) -> None:
        try:
            await self.redis.delete(f"{self.prefix}:tools:{key}")
        except RedisError:
            return

    async def put(self, key: str, result: ToolResult, ttl_seconds: int) -> None:
        # 缺少来源运行时不缓存内容；执行层通过带来源的入口写入。
        return

    async def put_for_run(
        self, key: str, result: ToolResult, ttl_seconds: int, run_id: str
    ) -> None:
        if self.engine is None:
            return
        try:
            await self.redis.set(
                f"{self.prefix}:tools:{key}",
                json.dumps({"run_id": run_id, "result": result.model_dump(mode="json")}),
                ex=ttl_seconds,
            )
            async with transaction(self.engine, result.scope, [content_key(result.scope)]) as uow:
                await DeletionGuard(result.scope).check(uow, [ContentRef("run", run_id)])
        except ServiceError:
            await self.delete(key)
            raise
        except RedisError:
            # 缓存失败不改变已验证业务结果，也不绕过下一次实时授权。
            return


class ToolExecutor:
    def __init__(
        self,
        service: ToolService,
        runs: ToolRunPort | None = None,
        cache: ToolCache | None = None,
        *,
        fixture: "FixtureProvider | None" = None,
    ) -> None:
        self.service, self.runs, self.cache = service, runs, cache
        self.fixture = fixture

    async def authorize(
        self, context: AuthContext, call: ToolExecution, *, check_binding: bool = True
    ) -> tuple[dict[str, Any], ToolDefinition, RunToolGrant, frozenset[str]]:
        if self.runs is None:
            raise unavailable("运行限额与工具白名单服务")
        tool, version = await self.service.repository.resolve(context, call.tool_version_id)
        if tool["status"] != "ACTIVE":
            raise ServiceError("TOOL_UNAVAILABLE", "工具已停用", 403)
        definition = ToolDefinition.model_validate(version["content"])
        validate_definition(definition)
        grant = await self.runs.authorize_call(context, call)
        if self.fixture and grant.purpose != "evaluation":
            raise ServiceError("TOOL_FORBIDDEN", "固定工具数据仅用于受控评测", 403)
        if grant.purpose == "evaluation" and definition.effect_type != "READ_ONLY":
            raise ServiceError("TOOL_FORBIDDEN", "评测禁止执行真实写工具", 403)
        if (
            grant.scope != context.scope
            or grant.run_id != call.run_id
            or grant.step_id != call.step_id
            or call.tool_version_id not in grant.tool_version_ids
        ):
            raise ServiceError("TOOL_FORBIDDEN", "工具不在当前运行步骤的 Agent 白名单中", 403)
        if version["state"] != "PUBLISHED" and not (
            grant.purpose in {"debug", "evaluation"}
            and version["state"] == "DRAFT"
            and grant.draft_revisions.get(call.tool_version_id) == version["revision"]
        ):
            raise ServiceError("TOOL_FORBIDDEN", "运行必须绑定已发布版本或固定草稿修订", 403)
        decision = await self.service.authorization.check(context, "run:create", "tool", tool["id"])
        actions = frozenset(decision.actions) & grant.allowed_actions
        if not decision.allowed or not set(definition.required_scopes) <= actions:
            raise ServiceError("TOOL_FORBIDDEN", "当前身份、Agent 与工具权限不匹配", 403)
        scope = context.scope
        if scope.environment not in definition.environments:
            raise ServiceError("TOOL_FORBIDDEN", "当前环境无权调用工具", 403)
        requirements = definition.subject_requirements
        if (requirements.required and not scope.subject_id) or (
            requirements.allowed_types and scope.subject_type not in requirements.allowed_types
        ):
            raise ServiceError("TOOL_FORBIDDEN", "工具缺少符合约定的受信业务主体", 403)
        self.service.registry.validate(scope, definition, tool["source_type"], executable=True)
        if check_binding:
            await self.service.registry.check_binding(context, definition)
        return tool, definition, grant, actions

    @staticmethod
    def cache_key(
        context: AuthContext,
        call: ToolExecution,
        definition: ToolDefinition,
        grant: RunToolGrant,
        actions: frozenset[str],
    ) -> str:
        # 不共享成员、服务 Key、Agent、主体或授权修订；参数摘要保留全部业务字段。
        return (
            context.scope.channel_id
            + ":"
            + digest(
                {
                    "scope": context.scope.model_dump(),
                    "principal_id": context.principal_id,
                    "principal_type": context.principal_type,
                    "actor_id": context.actor_id,
                    "client_id": context.client_id,
                    "key_id": context.key_id,
                    "tool_version": call.tool_version_id,
                    "definition": definition.model_dump(mode="json"),
                    "agent_version": grant.agent_version_id,
                    "authorization_revision": grant.authorization_revision,
                    "actions": sorted(actions),
                    "arguments": call.arguments,
                }
            )
        )

    async def validate_result(
        self,
        context: AuthContext,
        call: ToolExecution,
        definition: ToolDefinition,
        raw: AdapterResult,
        call_id: str,
    ) -> ToolResult:
        try:
            if len(canonical_json(raw.model_dump(mode="json"))) > definition.max_result_size:
                raise ValueError()
        except (ValueError, TypeError, RecursionError):
            raise ServiceError(
                "TOOL_RESULT_INVALID", "工具结果体积或 JSON 内容不符合约定", 502
            ) from None
        validate_json(raw.data, definition.output_schema, "TOOL_RESULT_INVALID")
        if definition.analysis_policy:
            records = raw.data
            for field in definition.analysis_policy.rows_path:
                records = records.get(field) if isinstance(records, dict) else None
            if not isinstance(records, list) or len(records) > definition.analysis_policy.max_rows:
                raise ServiceError("ANALYSIS_ROWS_INVALID", "分析源缺少行数据或超过行数上限", 502)
            if raw.coverage.get("returned_count", len(records)) != len(records):
                raise ServiceError("TOOL_RESULT_INVALID", "分析源完整性声明与实际行数不符", 502)
        age = (utcnow() - raw.observed_at).total_seconds()
        if age < -5 or (self.fixture is None and age > definition.cache_policy.freshness_seconds):
            raise ServiceError("TOOL_RESULT_INVALID", "工具观测时间过期或晚于当前时间", 502)
        if raw.has_more and not raw.cursor or (raw.truncated or raw.has_more) and not raw.coverage:
            raise ServiceError("TOOL_RESULT_INVALID", "分页或截断结果须提供游标及原始范围", 502)
        if raw.truncated and not {"original_count", "returned_count"} <= raw.coverage.keys():
            raise ServiceError("TOOL_RESULT_INVALID", "截断结果缺少原始数量和返回数量", 502)
        if raw.truncated:
            original, returned = raw.coverage["original_count"], raw.coverage["returned_count"]
            if (
                type(original) is not int
                or type(returned) is not int
                or not 0 <= returned < original
            ):
                raise ServiceError("TOOL_RESULT_INVALID", "截断范围不正确", 502)
        if isinstance(raw.data, dict):
            for name, value in (
                ("has_more", raw.has_more),
                ("cursor", raw.cursor),
                ("truncated", raw.truncated),
            ):
                if name in raw.data and raw.data[name] != value:
                    raise ServiceError("TOOL_RESULT_INVALID", "结果分页与外层范围不一致", 502)
        if any(e.scope != context.scope for e in raw.evidence_refs):
            raise ServiceError("TOOL_RESULT_INVALID", "工具证据超出当前授权范围", 502)
        for evidence in raw.source_evidence:
            if evidence.scope != context.scope or evidence.observed_at > utcnow():
                raise ServiceError("TOOL_RESULT_INVALID", "源证据范围或观测时间不正确", 502)
        await self.service.repository.check_sources(context, raw)
        files = artifact_references(raw.data)
        if set(raw.artifact_ids) - files:
            raise ServiceError("TOOL_RESULT_INVALID", "文件引用必须在结构化结果中明确列出", 502)
        for file_id in files:
            await self.service.authorization.require(context, "artifact:download", file_id)
        generated = EvidenceRef(
            evidence_id=new_id("evidence"),
            scope=context.scope,
            source_type="tool_call",
            source_id=call_id,
            source_version=raw.source_version,
            observed_at=raw.observed_at,
            location=EvidenceLocation(field_path=("data",), text_start=None, text_end=None),
            title="工具调用结果",
            authorized_actions=("run:read",),
        )
        coverage = dict(raw.coverage)
        sources = []
        if raw.source_evidence:
            coverage["source_evidence"] = [e.model_dump(mode="json") for e in raw.source_evidence]
            sources = [
                EvidenceRef(
                    evidence_id=new_id("evidence"),
                    scope=context.scope,
                    source_type="tool_call",
                    source_id=call_id,
                    source_version=e.source_version,
                    observed_at=e.observed_at,
                    location=EvidenceLocation(
                        field_path=("coverage", "source_evidence", index),
                        text_start=None,
                        text_end=None,
                    ),
                    title=e.title,
                    authorized_actions=("run:read",),
                )
                for index, e in enumerate(raw.source_evidence)
            ]
        result = ToolResult(
            scope=context.scope,
            tool_version_id=call.tool_version_id,
            source_request_id=raw.source_request_id,
            source_version=raw.source_version,
            observed_at=raw.observed_at,
            data=raw.data,
            evidence_refs=(*raw.evidence_refs, *sources, generated),
            warnings=raw.warnings,
            cursor=raw.cursor,
            has_more=raw.has_more,
            truncated=raw.truncated,
            coverage=coverage,
        )
        if len(canonical_json(result.model_dump(mode="json"))) > definition.max_result_size:
            raise ServiceError("TOOL_RESULT_INVALID", "包含证据的工具结果超过体积上限", 502)
        return result

    async def execute(self, context: AuthContext, call: ToolExecution) -> ToolResult:
        # 深拷贝隔离调用者与适配器，阻止等待授权期间修改已校验参数。
        call = ToolExecution.model_validate_json(call.model_dump_json())
        call_id, tool_id = new_id("tool_call"), "unresolved"
        try:
            tool, definition, grant, actions = await self.authorize(context, call)
            tool_id = tool["id"]
            validate_arguments(call.arguments, definition)
        except ServiceError as exc:
            # 已定位的固定版本保留拒绝记录；未知或跨渠道版本不产生伪造关联。
            try:
                resolved, _ = await self.service.repository.resolve(context, call.tool_version_id)
            except ServiceError:
                raise exc from None
            await self.service.repository.record(
                context,
                call,
                call_id,
                resolved["id"],
                "DENIED",
                None,
                None,
                {"code": exc.code, "message": exc.message},
                None,
                {},
            )
            raise
        assert self.runs is not None
        operation = getattr(self.runs, "operation", None)
        if definition.effect_type != "READ_ONLY" and not operation:
            raise ServiceError("TOOL_CONFIRMATION_REQUIRED", "写入须经受控确认步骤执行", 409)
        key = self.cache_key(context, call, definition, grant, actions)
        auth_scope = {
            "scope": context.scope.model_dump(),
            "agent_version_id": grant.agent_version_id,
            "principal_id": context.principal_id,
            "actions": sorted(actions),
            "authorization_revision": grant.authorization_revision,
        }
        if self.cache and definition.cache_policy.ttl_seconds:
            source_run_id = None
            if isinstance(self.cache, RedisToolCache):
                entry = await self.cache.get_with_source(key)
                cached, source_run_id = entry if entry else (None, None)
            else:
                cached = await self.cache.get(key)
            if (
                cached
                and cached.scope == context.scope
                and cached.tool_version_id == call.tool_version_id
            ):
                age = (utcnow() - cached.observed_at).total_seconds()
                if (
                    0
                    <= age
                    <= min(
                        definition.cache_policy.ttl_seconds,
                        definition.cache_policy.freshness_seconds,
                    )
                ):
                    validate_json(cached.data, definition.output_schema, "TOOL_RESULT_INVALID")
                    await self.service.repository.check_evidence(context, cached)
                    for file_id in artifact_references(cached.data):
                        await self.service.authorization.require(
                            context, "artifact:download", file_id
                        )
                    _, _, current_grant, current_actions = await self.authorize(context, call)
                    if (
                        self.cache_key(context, call, definition, current_grant, current_actions)
                        != key
                    ):
                        raise ServiceError("TOOL_FORBIDDEN", "调用期间授权已变更", 403)
                    await self.service.repository.record(
                        context,
                        call,
                        call_id,
                        tool_id,
                        "CACHED",
                        None,
                        cached,
                        None,
                        0,
                        auth_scope,
                        source_run_id=source_run_id,
                    )
                    return cached
        for number in range(definition.retry_policy.max_attempts):
            if number:
                await asyncio.sleep(definition.retry_policy.delay_ms / 1000)
                call_id = new_id("tool_call")
            _, current_definition, current_grant, current_actions = await self.authorize(
                context, call
            )
            if (
                self.cache_key(context, call, current_definition, current_grant, current_actions)
                != key
            ):
                raise ServiceError("TOOL_FORBIDDEN", "调用期间授权或版本已变更", 403)
            attempt = Attempt(
                scope=context.scope,
                attempt_id=new_id("attempt"),
                run_id=call.run_id,
                step_id=call.step_id,
                kind="tool",
                target_version_id=call.tool_version_id,
                source_request_id=None,
                state="STARTED",
                started_at=utcnow(),
                finished_at=None,
                error=None,
            )
            await self.runs.start_attempt(context, attempt)
            await self.service.repository.record(
                context, call, call_id, tool_id, "STARTED", attempt, None, None, None, auth_scope
            )
            started = monotonic()
            result: ToolResult | None = None
            failure: ServiceError | None = None
            try:
                registration = self.service.registry.resolve(context.scope, definition.binding)
                async with asyncio.timeout(definition.timeout_seconds):
                    raw = (
                        await self.fixture.invoke(context, call)
                        if self.fixture
                        else await registration.adapter.invoke(
                            AdapterRequest(
                                context=context,
                                arguments=call.model_copy(deep=True).arguments,
                                attempt_id=attempt.attempt_id,
                                definition=definition.model_copy(deep=True),
                                run_id=call.run_id,
                                operation=operation,
                            )
                        )
                    )
                attempt = attempt.model_copy(update={"source_request_id": raw.source_request_id})
                result = await self.validate_result(context, call, definition, raw, call_id)
                _, after_definition, after_grant, after_actions = await self.authorize(
                    context, call, check_binding=False
                )
                if (
                    self.cache_key(context, call, after_definition, after_grant, after_actions)
                    != key
                ):
                    raise ServiceError("TOOL_FORBIDDEN", "结果交付前授权或版本已变更", 403)
            except TimeoutError:
                failure = ToolAdapterError("TOOL_TIMEOUT", "工具调用超时", retryable=True)
            except ServiceError as exc:
                failure = exc
            except (ValueError, TypeError, ValidationError):
                failure = ToolAdapterError("TOOL_RESULT_INVALID", "工具结果格式不正确")
            except asyncio.CancelledError:
                failure = ToolAdapterError("TOOL_UNAVAILABLE", "工具调用已取消")
                raise
            except Exception:
                failure = ToolAdapterError("TOOL_UNAVAILABLE", "工具适配器执行失败")
            finally:
                unknown_write = bool(failure and definition.effect_type != "READ_ONLY")
                retryable = (
                    isinstance(failure, ToolAdapterError)
                    and failure.retryable
                    and not unknown_write
                )
                error = (
                    RunError(
                        code=failure.code,
                        message=failure.message,
                        stage="tool",
                        retryable=retryable,
                        request_id=context.request_id,
                    )
                    if failure
                    else None
                )
                attempt = attempt.model_copy(
                    update={
                        "state": "UNKNOWN"
                        if unknown_write
                        else "FAILED"
                        if failure
                        else "SUCCEEDED",
                        "finished_at": utcnow(),
                        "error": error,
                        "source_request_id": (
                            failure.source_request_id
                            if isinstance(failure, ToolAdapterError) and failure.source_request_id
                            else attempt.source_request_id
                        ),
                    }
                )
                await self.service.repository.record(
                    context,
                    call,
                    call_id,
                    tool_id,
                    attempt.state,
                    attempt,
                    None if failure else result,
                    error.model_dump(mode="json") if error else None,
                    int((monotonic() - started) * 1000),
                    auth_scope,
                )
                finish_result = getattr(self.runs, "finish_result", None)
                if finish_result:
                    await finish_result(context, attempt, None if failure else result)
                else:
                    await self.runs.finish_attempt(context, attempt)
            if failure:
                if retryable and number + 1 < definition.retry_policy.max_attempts:
                    continue
                raise failure
            assert result is not None
            if self.cache and definition.cache_policy.ttl_seconds:
                if isinstance(self.cache, RedisToolCache):
                    await self.cache.put_for_run(
                        key, result, definition.cache_policy.ttl_seconds, call.run_id
                    )
                else:
                    await self.cache.put(key, result, definition.cache_policy.ttl_seconds)
            return result
        raise ServiceError("TOOL_UNAVAILABLE", "工具未能返回有效结果", 503)
