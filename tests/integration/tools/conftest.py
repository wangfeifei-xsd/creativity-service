"""复用真实渠道、IAM、PostgreSQL 与 Redis 夹具。"""

import pytest

from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.tools.assembly import build_tool_services
from creativity_service.modules.tools.schemas import ToolCreate, ToolDefinition, ToolVersionCreate
from tests.integration.channels.conftest import channel_body, channel_env, login

__all__ = ["channel_env"]


@pytest.fixture
async def tools_env(channel_env):
    env = channel_env
    channel = await env.services.channels.create(
        env.admin, channel_body(env).model_copy(update={"independent_actions": ["release:publish"]})
    )
    domains = await env.services.channels.data_scopes(env.admin, channel.channel_id)
    _, session = await login(env)
    response = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel.channel_id,
            environment="test",
            data_scope_id=domains[0].data_scope_id,
        ),
    )
    context = await env.iam.authentication.authenticate(response.access_token, "management")
    bundle = build_tool_services(
        env.engine, env.iam.authorization, redis=env.redis, prefix=env.schema
    )
    env.client.headers["Authorization"] = f"Bearer {response.access_token}"
    env.client._transport.app.state.tools = bundle
    definition = ToolDefinition(
        input_schema={
            "type": "object",
            "properties": {
                "values": {"type": "array", "title": "数值列表", "items": {"type": "string"}}
            },
            "required": ["values"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"sum": {"type": "string"}},
            "required": ["sum"],
            "additionalProperties": False,
        },
        model_fields_allowed=("values",),
        binding={"adapter_key": "decimal_sum", "implementation_version": "1"},
        effect_type="READ_ONLY",
        allowed_data_domains=(domains[0].data_scope_id,),
        environments=("test",),
        subject_requirements={"required": False},
    )
    tool = await bundle.management.create(
        context,
        ToolCreate(
            tool_code="sum",
            name="精确求和",
            description="计算统计数据合计",
            owner="平台管理员",
            source_type="builtin",
        ),
    )
    version = await bundle.management.create_version(
        context, tool.tool_id, ToolVersionCreate(version_label="初始版本", definition=definition)
    )
    env.tools, env.context, env.tool, env.version, env.definition = (
        bundle,
        context,
        tool,
        version,
        definition,
    )
    yield env
