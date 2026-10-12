"""重新绑定差异能区分缺失、空值、行为声明及历史快照不可用。"""

from mcp.types import Tool

from creativity_service.modules.mcp.differences import explain_rebind
from creativity_service.modules.mcp.transport import remote_tool
from creativity_service.modules.tools.rebinding import ToolRebindError, difference_fields
from creativity_service.modules.tools.schemas import ToolDefinition


def definition(remote):
    schema = {**remote.input_schema, "additionalProperties": False}
    return ToolDefinition(
        input_schema=schema,
        output_schema={"type": "object"},
        model_fields_allowed=tuple(schema.get("properties", {})),
        binding={"adapter_key": "mcp_test", "implementation_version": remote.schema_hash},
        effect_type="READ_ONLY",
        environments=("test",),
    )


def remote(**changes):
    return remote_tool(
        Tool.model_validate(
            {
                "name": "lookup",
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True},
                **changes,
            }
        )
    )


def test_difference_values_distinguish_missing_null_and_boolean_from_number():
    fields = difference_fields(
        {"input_schema": {"default": None, "const": True, "required": ["query"]}},
        {"input_schema": {"const": 1, "required": ["query", "limit"], "title": None}},
    )
    by_path = {tuple(field["path"]): field["message"] for field in fields}
    assert by_path["input_schema", "default"] == "原值：null\n新值：未声明"
    assert by_path["input_schema", "title"] == "原值：未声明\n新值：null"
    assert by_path["input_schema", "const"] == "原值：true\n新值：1"
    assert by_path["input_schema", "required"] == '原值：["query"]\n新值：["query", "limit"]'


def test_rebind_explains_output_and_annotations_instead_of_hash():
    previous = remote()
    current = remote(
        outputSchema={"type": "object"},
        annotations={"readOnlyHint": False, "title": "新名称"},
    )
    error = ToolRebindError(definition(previous), definition(current))
    explain_rebind(error, previous, current)
    by_path = {tuple(field["path"]): field["message"] for field in error.fields}
    assert by_path["annotations", "readOnlyHint"] == "原值：true\n新值：false"
    assert by_path["output_schema",] == '原值：null\n新值：{"type": "object"}'
    assert ("implementation_version",) not in by_path
    assert ("annotations", "title") not in by_path


def test_rebind_keeps_local_edits_separate_from_remote_contract():
    previous = remote(inputSchema={"type": "object", "properties": {"limit": {"type": "integer"}}})
    current = remote(inputSchema={"type": "object", "properties": {"limit": {"type": "string"}}})
    local = definition(previous).model_copy(
        update={"input_schema": {"type": "object", "properties": {"limit": {"type": "number"}}}}
    )
    error = ToolRebindError(local, definition(current))
    explain_rebind(error, previous, current)
    by_path = {tuple(field["path"]): field["message"] for field in error.fields}
    assert (
        by_path["input_schema", "properties", "limit", "type"] == '原值："number"\n新值："string"'
    )
    assert (
        by_path["remote_input_schema", "properties", "limit", "type"]
        == '原值："integer"\n新值："string"'
    )


def test_missing_or_unrelated_snapshot_does_not_invent_field_differences():
    previous = remote()
    current = remote(outputSchema={"type": "object"})
    for historical in (None, current):
        error = ToolRebindError(definition(previous), definition(current))
        explain_rebind(error, historical, current)
        assert error.fields[0]["path"] == ["implementation_version"]
        assert "原绑定的远端定义不可用" in error.fields[-1]["message"]


def test_large_differences_are_bounded_with_explicit_notice():
    fields = difference_fields({}, {f"field_{i:03}": "值" * 3000 for i in range(60)})
    assert len(fields) == 51
    assert "已截断" in fields[0]["message"]
    assert "仅显示前 50 项" in fields[-1]["message"]


def test_effect_change_uses_readable_values():
    previous = definition(remote())
    error = ToolRebindError(previous, previous.model_copy(update={"effect_type": "EXTERNAL_WRITE"}))
    assert error.fields == [{"path": ["effect_type"], "message": '原值："只读"\n新值："外部写入"'}]
