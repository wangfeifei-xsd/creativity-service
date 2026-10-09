"""本地契约验证和受信字段边界，不解析任何外部 schema 引用。"""

import re
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from creativity_service.core.primitives import ServiceError, canonical_json
from creativity_service.core.schema_validation import check_schema
from creativity_service.modules.tools.schemas import ToolDefinition


def artifact_references(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "artifact_id":
                if not isinstance(nested, str):
                    raise ServiceError("TOOL_RESULT_INVALID", "文件引用格式不正确", 502)
                found.add(nested)
            elif key == "artifact_ids":
                if not isinstance(nested, list) or any(not isinstance(i, str) for i in nested):
                    raise ServiceError("TOOL_RESULT_INVALID", "文件引用格式不正确", 502)
                found.update(nested)
            else:
                found.update(artifact_references(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(artifact_references(nested))
    return found


TRUSTED_FIELDS = frozenset(
    {
        "channelid",
        "tenantid",
        "clubid",
        "userid",
        "tenantuserid",
        "subjectid",
        "subjecttype",
        "datascopeid",
        "datascope",
        "subject",
        "creativityidentity",
        "meta",
        "datadomain",
        "datadomainid",
        "environment",
        "accesstoken",
        "authorization",
        "apikey",
        "keyid",
        "clientid",
        "principalid",
        "actorid",
        "identity",
        "trustedcontext",
        "scope",
        "scopes",
        "permissions",
        "token",
        "headers",
        "url",
        "sql",
        "command",
        "idempotencykey",
        "confirmationdigest",
    }
)


def trusted_field(name: str) -> bool:
    return re.sub(r"[^a-z0-9]", "", name.lower()) in TRUSTED_FIELDS


class ToolValidationError(ServiceError):
    def __init__(self, code: str, message: str, fields: list[dict[str, Any]]) -> None:
        super().__init__(code, message, 422 if code == "TOOL_INPUT_INVALID" else 502)
        self.fields = fields


def validate_schema(schema: dict[str, Any]) -> None:
    """支持本地定义引用；拒绝外部解析，避免 schema 触发额外网络访问。"""
    if len(canonical_json(schema)) > 65536:
        raise ServiceError("TOOL_INPUT_INVALID", "结构定义不能超过 64 KiB", 422)
    try:
        check_schema(schema)
    except SchemaError:
        raise ServiceError("TOOL_INPUT_INVALID", "结构定义不符合 JSON Schema 规范", 422) from None

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 32:
            raise ServiceError("TOOL_INPUT_INVALID", "结构嵌套过深", 422)
        if isinstance(value, dict):
            if "$id" in value or "$dynamicRef" in value:
                raise ServiceError(
                    "TOOL_INPUT_INVALID", "结构不能重定义引用地址或使用动态引用", 422
                )
            if "$ref" in value:
                ref = value["$ref"]
                if not isinstance(ref, str) or not ref.startswith("#/"):
                    raise ServiceError("TOOL_INPUT_INVALID", "结构引用仅支持本地 JSON Pointer", 422)
                target: Any = schema
                try:
                    for part in ref[2:].split("/"):
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                except (KeyError, TypeError):
                    raise ServiceError(
                        "TOOL_INPUT_INVALID", "结构引用的本地定义不存在", 422
                    ) from None
            for child in value.values():
                walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth + 1)

    walk(schema)


def validate_definition(definition: ToolDefinition) -> None:
    policy = definition.write_policy
    if policy and policy.authorization_mode == "preauthorized":
        if (
            definition.effect_type != "IDEMPOTENT_WRITE"
            or not policy.allowed_agent_codes
            or not policy.allowed_principal_ids
            or not policy.argument_constraints
            or policy.argument_constraints.get("type") != "object"
            or policy.argument_constraints.get("additionalProperties") is not False
            or any(
                not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", code)
                for code in policy.allowed_agent_codes
            )
        ):
            raise ServiceError(
                "TOOL_WRITE_INVALID", "预授权仅支持指定 Agent、执行身份和参数边界的幂等写入", 422
            )
        validate_schema(policy.argument_constraints)
    if definition.write_policy and (
        definition.effect_type == "READ_ONLY" or definition.idempotency_policy != "source_key"
    ):
        raise ServiceError("TOOL_WRITE_INVALID", "写入策略需要源幂等键和写入影响类型", 422)
    if len(canonical_json(definition.model_dump(mode="json"))) > 65536:
        raise ServiceError("TOOL_INPUT_INVALID", "工具契约不能超过 64 KiB", 422)
    for schema in (definition.input_schema, definition.output_schema):
        validate_schema(schema)
    schema = definition.input_schema
    allowed = definition.model_fields_allowed
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or set(schema.get("properties", {})) != set(allowed)
        or len(allowed) != len(set(allowed))
        or any(trusted_field(k) for k in allowed)
    ):
        raise ServiceError(
            "TOOL_INPUT_INVALID", "输入须为封闭对象，字段与模型白名单一致且不含受信字段", 422
        )
    if not definition.environments or not definition.required_scopes:
        raise ServiceError("TOOL_INPUT_INVALID", "须明确环境与必要授权", 422)
    if definition.cache_policy.ttl_seconds > definition.cache_policy.freshness_seconds:
        raise ServiceError("TOOL_INPUT_INVALID", "缓存时长不能超过数据新鲜度", 422)
    if definition.cache_policy.volatile and definition.cache_policy.ttl_seconds > 5:
        raise ServiceError("TOOL_INPUT_INVALID", "价格和库存缓存不能超过 5 秒", 422)
    if definition.effect_type != "READ_ONLY" and (
        definition.retry_policy.max_attempts > 1 or definition.cache_policy.ttl_seconds > 0
    ):
        raise ServiceError("TOOL_INPUT_INVALID", "写入工具尚未启用重试或缓存", 422)


def validate_json(value: Any, schema: dict[str, Any], code: str) -> None:
    errors = Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value)
    fields: list[dict[str, Any]] = []
    for error in errors:
        path = list(error.absolute_path)
        if error.validator == "required" and isinstance(error.instance, dict):
            fields.extend(
                {"path": [*path, name], "message": "缺少必填字段"}
                for name in error.validator_value
                if name not in error.instance
            )
        else:
            fields.append(
                {
                    "path": path,
                    "message": {
                        "type": "字段类型不正确",
                        "additionalProperties": "含未获授权的字段",
                        "enum": "字段取值不在允许范围",
                    }.get(str(error.validator), "字段值不符合约定"),
                }
            )
        if len(fields) >= 20:
            break
    if fields:
        # 不回显原始消息，避免其中包含密码、业务输入或外部文本。
        raise ToolValidationError(
            code,
            "参数不符合契约" if code == "TOOL_INPUT_INVALID" else "结果不符合契约",
            fields[:20],
        )


def validate_arguments(arguments: dict[str, Any], definition: ToolDefinition) -> None:
    try:
        if len(canonical_json(arguments)) > 65536:
            raise ValueError()
    except (ValueError, TypeError, RecursionError):
        raise ServiceError("TOOL_INPUT_INVALID", "参数须为有效 JSON 且不超过 64 KiB", 422) from None

    def check(value: Any, path: list[str | int], depth: int) -> None:
        if depth > 24:
            raise ServiceError("TOOL_INPUT_INVALID", "参数嵌套过深", 422)
        if isinstance(value, dict):
            for name, nested in value.items():
                if trusted_field(name):
                    raise ToolValidationError(
                        "TOOL_INPUT_INVALID",
                        "不能覆盖受信身份或执行目标",
                        [{"path": [*path, name], "message": "此字段由服务端注入"}],
                    )
                check(nested, [*path, name], depth + 1)
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                check(nested, [*path, index], depth + 1)

    check(arguments, [], 0)
    if set(arguments) - set(definition.model_fields_allowed):
        raise ServiceError("TOOL_INPUT_INVALID", "参数包含模型白名单以外的字段", 422)
    validate_json(arguments, definition.input_schema, "TOOL_INPUT_INVALID")
