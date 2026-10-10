"""调试输入失败返回可定位字段，不生成运行或暴露原输入。"""

import pytest

pytestmark = pytest.mark.integration


async def test_debug_input_errors_identify_fields_without_echoing_values(agent_env):
    env = agent_env
    schema = {
        "type": "object",
        "properties": {
            "request": {"type": "string", "title": "业务诉求", "minLength": 1, "maxLength": 5}
        },
        "required": ["request"],
        "additionalProperties": False,
    }
    detail = await env.agents.create(
        env.context,
        env.body.model_copy(
            update={"definition": env.definition.model_copy(update={"input_schema": schema})}
        ),
    )
    version = detail.versions[0]
    for value, message in (
        ({}, "缺少必填字段"),
        ({"request": "不要回显的敏感内容"}, "最多输入 5 个字符"),
    ):
        response = await env.client.post(
            f"/admin/v1/agent-versions/{version.version_id}/tests",
            json={"revision": version.revision, "input": value, "idempotency_key": "field-errors"},
        )
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "INPUT_SCHEMA_INVALID"
        assert error["fields"] == [{"path": ["request"], "message": message}]
        assert "不要回显" not in response.text
