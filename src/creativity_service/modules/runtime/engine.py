"""固定步骤使用普通代码，有状态分支交给 LangGraph；两者共享持久化步骤事实。"""

import asyncio
import json
from typing import Any, Protocol, TypedDict, cast

from jsonschema import Draft202012Validator
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import BusinessResult
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.integrations.checkpoints import SQLAlchemySaver
from creativity_service.integrations.models.contracts import ModelRequest
from creativity_service.modules.agents.schemas import AgentStep, FrozenExecutionSpec
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.models.schemas import FrozenModel
from creativity_service.modules.runs.interruptions import RuntimeSuspended
from creativity_service.modules.runs.schemas import Lease
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.runtime.context import ContextBuilder
from creativity_service.modules.runtime.model import ModelRunner
from creativity_service.modules.runtime.registry import StepRegistry
from creativity_service.modules.runtime.storage import load_spec, read_input, record_inputs
from creativity_service.modules.runtime.tools import BoundToolPort
from creativity_service.modules.tools.execution import ToolExecutor
from creativity_service.modules.tools.schemas import ToolDefinition, ToolExecution
from creativity_service.modules.tools.services import ToolService


class FlowState(TypedDict):
    node: str
    visits: dict[str, int]
    outputs: dict[str, Any]
    result: dict[str, Any]


class DebugHandler(Protocol):
    async def execute(
        self,
        context: AuthContext,
        lease: Lease,
        spec: FrozenExecutionSpec,
        descriptor: dict[str, Any],
        values: dict[str, Any],
    ) -> None: ...


def field(value: Any, path: str) -> Any:
    for part in path.split(".") if path else []:
        if not isinstance(value, dict) or part not in value:
            raise ServiceError("INPUT_SOURCE_MISSING", "步骤输入引用不存在", 422)
        value = value[part]
    return value


def step_input(
    step: AgentStep, original: dict[str, Any], outputs: dict[str, Any]
) -> dict[str, Any]:
    values = {}
    for key, source in step.inputs.items():
        if source.source == "constant":
            values[key] = source.value
        else:
            origin = original if source.source == "input" else outputs.get(source.step or "")
            values[key] = field(origin, source.path)
    if not Draft202012Validator(step.input_schema).is_valid(values):
        raise ServiceError("STEP_INPUT_INVALID", "步骤输入未通过结构校验", 422)
    return values


class RuntimeExecutor:
    def __init__(
        self,
        runs: RunService,
        models: ModelRunner,
        contexts: ContextBuilder,
        tools: ToolService,
        authorization: IamAuthorization,
        registry: StepRegistry | None = None,
    ) -> None:
        self.runs, self.models, self.contexts, self.tools = runs, models, contexts, tools
        self.authorization, self.registry = authorization, registry or StepRegistry()
        self.debug: DebugHandler | None = None

    async def tool(
        self,
        context: AuthContext,
        lease: Lease,
        spec: FrozenExecutionSpec,
        node_key: str,
        version_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        saved = await self.runs.load_progress(lease, node_key)
        if saved and saved["output"] is not None:
            await self.runs.commit_step(lease, node_key, saved["output"])
            return dict(saved["output"])
        step = await self.runs.start_step(lease, node_key, values)
        if step is None:
            raise ServiceError("RUN_INACTIVE", "运行已停止", 409)
        port = BoundToolPort(self.runs, lease, spec, self.authorization)
        from creativity_service.modules.evaluations.fixtures import provider_for

        fixture = (
            await provider_for(self.runs.engine, context, lease.run_id)
            if spec.purpose == "evaluation"
            else None
        )
        executor = ToolExecutor(
            self.tools, port, fixture=fixture
        )  # 每次实际执行独立计数，缓存不冒充外部尝试。
        call = ToolExecution(
            run_id=lease.run_id,
            step_id=step["id"],
            tool_version_id=version_id,
            arguments=values,
        )
        definition = ToolDefinition.model_validate(
            next(v for v in spec.versions if v.version_id == version_id).content
        )
        if definition.effect_type != "READ_ONLY":
            from creativity_service.modules.runtime.writes import WriteExecution

            result = await WriteExecution(
                self, context, lease, spec, node_key, call, executor, port
            ).execute(definition)
        else:
            result = await executor.execute(context, call)
        output = result.model_dump(mode="json")
        await self.runs.commit_step(lease, node_key, output)
        return output

    async def model(
        self,
        context: AuthContext,
        lease: Lease,
        spec: FrozenExecutionSpec,
        step: AgentStep,
        node_key: str,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        route_id = step.dependency or spec.definition.bindings.model_route_version
        route = next(v for v in spec.versions if v.version_id == route_id)
        candidates = [
            FrozenModel.model_validate(v)
            for v in cast(list[dict[str, Any]], route.content["models"])
        ]
        order = cast(list[str], route.content["attempt_order"])
        native_output = "structured_output" in route.content.get("required_capabilities", [])
        if not native_output:
            # 普通文本模型也能完成业务结构输出；供应商能力与平台结果校验分别处理。
            instruction = (
                "最终回复必须是符合以下 JSON Schema 的单个 JSON 对象，不要添加 Markdown 或解释。"
                "如需调用已提供的工具，可先调用工具，再返回最终 JSON。\n"
                + json.dumps(step.output_schema, ensure_ascii=False, separators=(",", ":"))
            )
            messages = [dict(message) for message in messages]
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] = f"{messages[0].get('content') or ''}\n{instruction}"
            else:
                messages.insert(0, {"role": "system", "content": instruction})
            if (
                len(json.dumps(messages, ensure_ascii=False).encode())
                > spec.definition.context.context_limit * 4
            ):
                raise ServiceError("CONTEXT_LIMIT_EXCEEDED", "实际上下文超过配置上限", 422)
        tools = []
        if spec.definition.workflow_type == "tool_loop":
            for index, version_id in enumerate(spec.definition.bindings.tool_versions):
                version = next(v for v in spec.versions if v.version_id == version_id)
                definition = ToolDefinition.model_validate(version.content)
                tool_row, _ = await self.tools.repository.resolve(context, version_id)
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": f"tool_{index}",
                            "description": tool_row["description"],
                            "parameters": definition.input_schema,
                        },
                    }
                )
        return await self.models.invoke(
            context,
            lease,
            spec,
            node_key,
            ModelRequest(
                messages=messages,
                tools=tools,
                output_schema=step.output_schema,
                output_mode="native" if native_output else "prompt",
                stream="streaming" in cast(list[str], route.content["required_capabilities"]),
            ),
            candidates,
            order,
        )

    async def compute(
        self,
        context: AuthContext,
        lease: Lease,
        spec: FrozenExecutionSpec,
        step: AgentStep,
        key: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        saved = await self.runs.load_progress(lease, key)
        if saved and saved["output"] is not None:
            await self.runs.commit_step(lease, key, saved["output"])
            return dict(saved["output"])
        current = await self.runs.start_step(lease, key, values)
        if current is None:
            raise ServiceError("RUN_INACTIVE", "运行已停止", 409)
        if step.operator in {"input", "approval"}:
            output = await self.runs.suspend(
                lease,
                key,
                step.name,
                step.output_schema,
                values,
                approval=step.operator == "approval",
            )
            await self.runs.commit_step(lease, key, output)
            return output
        for number in range(current["attempt_count"], step.max_retries + 1):
            attempt = await self.runs.start_attempt(lease, key)
            if attempt is None:
                raise ServiceError("RUN_INACTIVE", "运行已停止", 409)
            try:
                async with asyncio.timeout(step.timeout_seconds):
                    if step.operator == "object":
                        # 字段选择与常量来自已校验的输入映射，不执行配置代码或领域计算。
                        output = dict(values)
                    else:
                        output = await self.registry.resolve(spec.definition.entrypoint, step.key)(
                            context, values
                        )
                if not isinstance(output, dict) or not Draft202012Validator(
                    step.output_schema
                ).is_valid(output):
                    raise ServiceError("OUTPUT_SCHEMA_INVALID", "步骤输出未通过结构校验", 422)
            except (ServiceError, TimeoutError) as exc:
                failure = (
                    exc
                    if isinstance(exc, ServiceError)
                    else ServiceError("STEP_TIMEOUT", "步骤执行超时", 504)
                )
                retryable = (
                    step.failure_policy == "retry"
                    and failure.status >= 500
                    and number < step.max_retries
                )
                row = await self.runs.before_progress(lease)
                await self.runs.finish_attempt(
                    lease,
                    attempt["id"],
                    "FAILED",
                    retryable=retryable,
                    error=self.runs.error(row, failure.code, failure.message),
                )
                if not retryable:
                    raise failure from exc
                continue
            await self.runs.finish_attempt(lease, attempt["id"], "SUCCEEDED", output=output)
            await self.runs.commit_step(lease, key, output)
            return output
        raise ServiceError("RETRY_LIMIT", "步骤重试次数已达上限", 429)

    async def execute(self, context: AuthContext, lease: Lease) -> None:
        try:
            row = await self.runs.before_progress(lease)
            spec = await load_spec(self.runs, row)
            original = await read_input(self.runs, lease)
            descriptor = json.loads(spec.payload_json).get("runtime")
            if descriptor and descriptor.get("kind") == "memory":
                from creativity_service.modules.memory.generation import execute_generation

                if self.runs.memory_consolidation is None:
                    raise ServiceError("MEMORY_UNAVAILABLE", "后台记忆整理未装配", 503)
                await execute_generation(
                    self.runs.memory_consolidation, self, context, lease, spec, descriptor["job_id"]
                )
                return
            if descriptor:
                if self.debug is None:
                    raise ServiceError("DEBUG_UNAVAILABLE", "调试执行器尚未装配", 503)
                await self.debug.execute(context, lease, spec, descriptor, original)
                return
            config = spec.definition
            initial: FlowState = {
                "node": config.start_step,
                "visits": {},
                "outputs": {},
                "result": {},
            }
            started = row["created_at"]
            tool_count = 0

            async def unsafe_advance(state: FlowState) -> FlowState:
                nonlocal tool_count
                if (utcnow() - started).total_seconds() > config.limits.loop_timeout_seconds:
                    raise ServiceError("LOOP_TIMEOUT", "流程循环已超过时间限制", 429)
                step = next(s for s in config.steps if s.key == state["node"])
                visit = state["visits"].get(step.key, 0)
                if visit >= config.limits.max_iterations:
                    raise ServiceError("LOOP_LIMIT", "流程循环次数已达上限", 429)
                key = step.key if visit == 0 else f"{step.key}.i{visit}"
                values = step_input(step, original, state["outputs"])
                if step.kind == "compute":
                    output = await self.compute(context, lease, spec, step, key, values)
                elif step.kind == "tool":
                    assert step.dependency is not None
                    value = await self.tool(context, lease, spec, key, step.dependency, values)
                    output = value["data"]
                    # 工具观测时间、数据版本与来源证据属于本次查询事实，独立于配置快照。
                    state = {
                        **state,
                        "outputs": {
                            **state["outputs"],
                            "_tool_results": {
                                **state["outputs"].get("_tool_results", {}),
                                step.key: value,
                            },
                        },
                    }
                else:
                    saved = await self.runs.load_progress(lease, key)
                    if saved and saved["output"] is not None:
                        value = saved["output"]
                        await self.runs.commit_step(lease, key, value)
                    else:
                        messages = await self.contexts.messages(
                            context, lease, spec, row, values, state["outputs"], key, original
                        )
                        async with asyncio.timeout(
                            min(step.timeout_seconds, config.limits.loop_timeout_seconds)
                        ):
                            value = await self.model(context, lease, spec, step, key, messages)
                    if value["tools"]:
                        if config.workflow_type != "tool_loop":
                            raise ServiceError("TOOL_FORBIDDEN", "此流程不允许模型选择工具", 403)
                        conversation = list(state["outputs"].get("_tool_messages", []))
                        assistant_calls = []
                        responses = []
                        for call in value["tools"]:
                            allowed = {
                                f"tool_{i}": (i, v)
                                for i, v in enumerate(config.bindings.tool_versions)
                            }
                            if call["name"] not in allowed or not call["id"]:
                                raise ServiceError("TOOL_FORBIDDEN", "模型请求了未授权工具", 403)
                            index, version = allowed[call["name"]]
                            if tool_count >= config.limits.max_tool_calls:
                                raise ServiceError("CALL_LIMIT", "工具调用次数已达上限", 429)
                            args = json.loads(call["arguments"])
                            result = await self.tool(
                                context,
                                lease,
                                spec,
                                f"runtime_tool_{index}_{tool_count}",
                                version,
                                args,
                            )
                            tool_count += 1
                            assistant_calls.append(
                                {
                                    "id": call["id"],
                                    "type": "function",
                                    "function": {
                                        "name": call["name"],
                                        "arguments": call["arguments"],
                                    },
                                }
                            )
                            responses.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": call["id"],
                                    "content": json.dumps(result, ensure_ascii=False),
                                }
                            )
                        conversation += [
                            {
                                "role": "assistant",
                                "content": value["text"] or None,
                                "tool_calls": assistant_calls,
                            },
                            *responses,
                        ]
                        return {
                            "node": step.key,
                            "visits": {**state["visits"], step.key: visit + 1},
                            "outputs": {**state["outputs"], "_tool_messages": conversation},
                            "result": {},
                        }
                    output = value["structured"]
                if not isinstance(output, dict) or not Draft202012Validator(
                    step.output_schema
                ).is_valid(output):
                    raise ServiceError("OUTPUT_SCHEMA_INVALID", "步骤输出未通过结构校验", 422)
                targets = [e for e in config.edges if e.source == step.key]
                target = None
                for edge in targets:
                    condition = edge.condition
                    if condition:
                        try:
                            actual = field(output, condition.path)
                            match = condition.operator == "exists" or (
                                actual == condition.value
                                if condition.operator == "eq"
                                else actual != condition.value
                            )
                        except ServiceError:
                            match = False
                        if match:
                            target = edge.target
                            break
                    elif not edge.otherwise:
                        target = edge.target
                        break
                target = target or next((e.target for e in targets if e.otherwise), None)
                if target is None:
                    raise ServiceError("FLOW_INVALID", "当前步骤没有可用出口", 422)
                return {
                    "node": target,
                    "visits": {**state["visits"], step.key: visit + 1},
                    "outputs": {**state["outputs"], step.key: output},
                    "result": output,
                }

            async def advance(state: FlowState) -> FlowState:
                try:
                    return await unsafe_advance(state)
                except ServiceError as exc:
                    step = next(s for s in config.steps if s.key == state["node"])
                    if step.failure_policy != "partial" or exc.status not in {
                        422,
                        429,
                        500,
                        502,
                        503,
                        504,
                    }:
                        raise
                    partial = BusinessResult(
                        schema_version="1.0",
                        business_status="PARTIAL",
                        data={
                            "steps": {
                                k: v for k, v in state["outputs"].items() if not k.startswith("_")
                            }
                        },
                        warnings=(f"步骤{step.name}未完成：{exc.message}",),
                        evidence_refs=(),
                    ).model_dump(mode="json")
                    if not Draft202012Validator(config.output_schema).is_valid(partial):
                        raise exc
                    return {**state, "node": "END", "result": partial}

            if config.workflow_type == "stateful":
                graph = StateGraph(FlowState)
                graph.add_node("advance", advance)
                graph.add_edge(START, "advance")
                graph.add_conditional_edges(
                    "advance", lambda state: END if state["node"] == "END" else "advance"
                )
                saver = SQLAlchemySaver(self.runs, lease)
                compiled = graph.compile(checkpointer=saver)
                options: RunnableConfig = {
                    "configurable": {"thread_id": lease.run_id},
                    "recursion_limit": len(config.steps) * config.limits.max_iterations + 2,
                }
                prior = await saver.aget_tuple(options)
                state = await compiled.ainvoke(None if prior else initial, options)
            else:
                state = initial
                while state["node"] != "END":
                    state = await advance(state)
            result = BusinessResult.model_validate(state["result"])
            result = result.model_copy(
                update={
                    "warnings": tuple(
                        dict.fromkeys((*result.warnings, *await self.contexts.warnings(lease)))
                    )
                }
            )
            await self.validate_evidence(context, lease, result)
            await self.runs.finish_run(lease, "SUCCEEDED", result)
        except RuntimeSuspended:
            return
        except ServiceError as exc:
            with suppress_inactive():
                await self.runs.finish_run(lease, "FAILED", failure=exc)
        except (ValidationError, ValueError, TypeError, KeyError):
            with suppress_inactive():
                await self.runs.finish_run(
                    lease,
                    "FAILED",
                    failure=ServiceError(
                        "OUTPUT_SCHEMA_INVALID", "执行结果或调用参数不符合冻结结构", 422
                    ),
                )
        except TimeoutError:
            with suppress_inactive():
                await self.runs.finish_run(
                    lease, "FAILED", failure=ServiceError("STEP_TIMEOUT", "步骤执行超时", 504)
                )

    async def validate_evidence(
        self, context: AuthContext, lease: Lease, result: BusinessResult
    ) -> None:
        from creativity_service.core.database import Repository, transaction
        from creativity_service.core.deletion import ContentRef, DeletionGuard
        from creativity_service.modules.tools.tables import metadata
        from creativity_service.modules.tools.validation import artifact_references

        refs = [
            ContentRef("artifact", identifier) for identifier in artifact_references(result.data)
        ]
        async with transaction(
            self.runs.engine, context.scope, self.runs.keys(context, lease.run_id)
        ) as uow:
            calls = await Repository(metadata.tables["tool_calls"], context.scope).find(
                uow.connection, run_id=lease.run_id
            )
            evidence_ids = {identifier for call in calls for identifier in call["evidence_ids"]}
            stored_refs = await Repository(
                metadata.tables["evidence_refs"], context.scope
            ).get_many(uow.connection, [r.evidence_id for r in result.evidence_refs])
            for ref in result.evidence_refs:
                if ref.scope != context.scope or ref.evidence_id not in evidence_ids:
                    raise ServiceError("EVIDENCE_INVALID", "结果证据不属于当前运行", 403)
                stored = stored_refs.get(ref.evidence_id)
                if (
                    stored is None
                    or any(
                        stored[k] != getattr(ref, k)
                        for k in (
                            "source_id",
                            "source_type",
                            "source_version",
                            "observed_at",
                            "title",
                        )
                    )
                    or stored["location"] != ref.location.model_dump(mode="json")
                ):
                    raise ServiceError("EVIDENCE_INVALID", "结果引用了未登记或不一致的证据", 422)
                refs.append(ContentRef("evidence", ref.evidence_id))
            await DeletionGuard(context.scope).check(uow, refs)
        for source in refs:
            if source.resource_type == "artifact":
                await self.runs.authorization.require(
                    context, "artifact:download", source.resource_id
                )
        await record_inputs(
            self.runs,
            lease,
            "result_sources",
            {"evidence_refs": [r.model_dump(mode="json") for r in result.evidence_refs]},
            refs,
        )


class suppress_inactive:
    """失败回调不能反转取消、过期租约或已清理内容；其他异常仍交给恢复服务。"""

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind: Any, value: Any, traceback: Any) -> bool:
        return isinstance(value, ServiceError) and value.code in {
            "RUN_STATE_CONFLICT",
            "LEASE_STALE",
            "CONTENT_DELETED",
            "RUN_INACTIVE",
            "FORBIDDEN",
            "AGENT_EMERGENCY_STOP",
            "ACCOUNT_DISABLED",
            "MEMBERSHIP_DISABLED",
        }
