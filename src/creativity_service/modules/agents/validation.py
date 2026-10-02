"""封闭表达式、字段来源、可达性、必经节点和循环终止的静态检查。"""

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from creativity_service.core.primitives import canonical_json
from creativity_service.modules.agents.registry import ENTRYPOINTS
from creativity_service.modules.agents.schemas import AgentDefinition, AgentIssue


def schema_field(schema: dict[str, Any], path: str) -> dict[str, Any] | None:
    current = schema
    for part in path.split(".") if path else []:
        current = current.get("properties", {}).get(part)
        if not isinstance(current, dict):
            return None
    return current


def compatible(source: dict[str, Any], target: dict[str, Any]) -> bool:
    """首版只证明内联结构可赋值；无法证明的复杂组合要求保持相同契约。"""
    if source == target or not target:
        return True
    if source.get("type") != target.get("type"):
        return source.get("type") == "integer" and target == {"type": "number"}
    if any(
        k in target and target[k] != source.get(k)
        for k in (
            "enum",
            "const",
            "oneOf",
            "anyOf",
            "allOf",
            "not",
            "pattern",
            "format",
            "minimum",
            "maximum",
            "minLength",
            "maxLength",
            "minItems",
            "maxItems",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "multipleOf",
            "uniqueItems",
        )
    ):
        return False
    if target.get("type") == "object":
        if not set(target.get("required", [])) <= set(source.get("required", [])):
            return False
        left, right = source.get("properties", {}), target.get("properties", {})
        if target.get("additionalProperties") is False and (
            source.get("additionalProperties") is not False or not left.keys() <= right.keys()
        ):
            return False
        return all(k not in left or compatible(left[k], v) for k, v in right.items())
    if target.get("type") == "array" and "items" in target:
        return compatible(source.get("items", {}), target["items"])
    return True


def static_issues(definition: AgentDefinition) -> list[AgentIssue]:
    issues: list[AgentIssue] = []

    def fail(message: str, path: str | None = None) -> None:
        issues.append(AgentIssue(code="FLOW_INVALID", message=message, path=path))

    if len(canonical_json(definition.model_dump(mode="json"))) > 262144:
        fail("流程配置不能超过 256 KiB")
        return issues
    schemas = [
        ("input_schema", definition.input_schema),
        ("output_schema", definition.output_schema),
    ]
    schemas += [
        (f"steps.{s.key}.{p}", getattr(s, p))
        for s in definition.steps
        for p in ("input_schema", "output_schema")
    ]

    def inline(value: Any, depth: int = 0) -> bool:
        if depth > 24:
            return False
        if isinstance(value, dict):
            return not {"$ref", "$dynamicRef", "$id"} & value.keys() and all(
                inline(v, depth + 1) for v in value.values()
            )
        return all(inline(v, depth + 1) for v in value) if isinstance(value, list) else True

    for path, schema in schemas:
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            fail("结构定义不符合 JSON Schema 规范", path)
            continue
        if not inline(schema) or schema.get("type") != "object":
            fail("结构须为内联对象，不能包含引用或超过嵌套上限", path)
    if issues:
        return issues
    required_output = {"business_status", "schema_version", "data", "warnings", "evidence_refs"}
    if not required_output <= set(definition.output_schema.get("required", [])):
        fail("业务输出须声明业务状态、结构版本、数据、警告和证据引用", "output_schema")
    if ENTRYPOINTS.get(definition.entrypoint, (None,))[0] != definition.workflow_type:
        fail("流程类型与已登记入口不符", "entrypoint")
    nodes = {s.key: s for s in definition.steps}
    if len(nodes) != len(definition.steps) or "END" in nodes:
        fail("步骤标识重复或使用了保留终点名称", "steps")
    if definition.start_step not in nodes:
        fail("起始步骤不存在", "start_step")
    outgoing: dict[str, list[str]] = {k: [] for k in nodes}
    for edge in definition.edges:
        if edge.source not in nodes or edge.target not in {*nodes, "END"}:
            fail("流转边引用不存在的步骤", "edges")
            continue
        outgoing[edge.source].append(edge.target)
        if edge.condition:
            condition_schema = schema_field(nodes[edge.source].output_schema, edge.condition.path)
            if condition_schema is None:
                fail("分支条件引用不存在的输出字段", f"edges.{edge.source}")
            elif edge.condition.operator != "exists" and not Draft202012Validator(schema).is_valid(
                edge.condition.value
            ):
                fail("分支条件值与输出字段类型不符", f"edges.{edge.source}")
        if edge.otherwise and edge.condition:
            fail("兜底分支不能同时设置条件", "edges")
    for key in nodes:
        edges = [e for e in definition.edges if e.source == key]
        if not edges:
            fail("步骤缺少终止或后续流转", f"steps.{key}")
        elif len(edges) == 1:
            if edges[0].condition:
                fail("条件分支必须包含兜底出口", f"steps.{key}")
        elif sum(e.otherwise for e in edges) != 1 or any(
            not e.condition and not e.otherwise for e in edges
        ):
            fail("多路分支须设置条件并恰有一个兜底出口", f"steps.{key}")

    def reachable(start: str, omit: str | None = None) -> set[str]:
        seen: set[str] = set()
        pending = [start]
        while pending:
            key = pending.pop()
            if key in seen or key == omit:
                continue
            seen.add(key)
            pending.extend(outgoing.get(key, []))
        return seen

    reached = reachable(definition.start_step)
    if set(nodes) - reached:
        fail("流程包含无法到达的步骤", "steps")
    for key, step in nodes.items():
        if "END" not in reachable(key):
            fail("步骤无法到达终止出口", f"steps.{key}")
        props = step.input_schema.get("properties", {})
        if (
            set(step.inputs) - props.keys()
            or not set(step.input_schema.get("required", [])) <= step.inputs.keys()
        ):
            fail("步骤输入须覆盖必填字段且不能引用未声明字段", f"steps.{key}.inputs")
        for field, source in step.inputs.items():
            target = props.get(field, {})
            if source.source == "constant":
                if (
                    source.step
                    or source.path
                    or not Draft202012Validator(target).is_valid(source.value)
                ):
                    fail("常量来源配置或类型不正确", f"steps.{key}.inputs.{field}")
                continue
            schema = definition.input_schema
            if source.source == "step":
                predecessor = nodes.get(source.step or "")
                if (
                    not predecessor
                    or source.step == key
                    or key in reachable(definition.start_step, source.step)
                ):
                    fail(
                        "输入来源步骤必须在所有路径上先于当前步骤执行",
                        f"steps.{key}.inputs.{field}",
                    )
                    continue
                schema = predecessor.output_schema
            elif source.step:
                fail("运行输入来源不能指定步骤", f"steps.{key}.inputs.{field}")
            origin = schema_field(schema, source.path)
            if origin is None or not compatible(origin, target):
                fail("输入来源字段不存在或结构不兼容", f"steps.{key}.inputs.{field}")
        if step.timeout_seconds > definition.limits.deadline_seconds:
            fail("步骤超时不能超过运行限时", f"steps.{key}.timeout_seconds")
        if (step.failure_policy == "retry") != (step.max_retries > 0):
            fail("重试策略与次数不一致", f"steps.{key}.failure_policy")
        if step.kind == "model" and step.dependency not in {
            None,
            definition.bindings.model_route_version,
        }:
            fail("模型步骤只能引用已选择的模型路由", f"steps.{key}.dependency")
        if step.kind == "tool" and step.dependency not in definition.bindings.tool_versions:
            fail("工具步骤必须引用智能体工具白名单中的版本", f"steps.{key}.dependency")
        if step.kind == "compute" and step.dependency:
            fail("计算步骤只能由已登记的流程代码实现", f"steps.{key}.dependency")
        if "END" in outgoing[key] and not compatible(step.output_schema, definition.output_schema):
            fail("终止步骤输出不满足智能体业务输出结构", f"steps.{key}.output_schema")
    cyclic = any(k in reachable(n) for k, targets in outgoing.items() for n in targets)
    if cyclic and definition.workflow_type not in {"tool_loop", "stateful"}:
        fail("此流程类型不允许循环", "edges")
    if definition.workflow_type == "structured" and (
        len(nodes) != 1 or definition.steps[0].kind != "model"
    ):
        fail("单步结构化任务只能包含一个模型步骤", "steps")
    limits = definition.limits
    if limits.loop_timeout_seconds > limits.deadline_seconds:
        fail("循环时间上限不能超过运行限时", "limits.loop_timeout_seconds")
    if definition.context.context_limit >= limits.token_limit:
        fail("Token 上限须为输出保留空间", "limits.token_limit")
    if limits.cost_limit and limits.cost_limit.amount <= 0:
        fail("费用上限必须大于零", "limits.cost_limit")
    if definition.workflow_type == "tool_loop" and (
        not definition.bindings.tool_versions or limits.max_tool_calls < 1
    ):
        fail("工具循环必须选择工具并配置调用次数", "bindings.tool_versions")
    if not definition.context.conversation_enabled and definition.context.summary_policy != "none":
        fail("未启用会话时不能读取会话摘要", "context.summary_policy")
    if len(definition.bindings.ids()) != len(set(definition.bindings.ids())):
        fail("依赖版本不能重复", "bindings")
    return issues
