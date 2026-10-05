"""工具管理版本互斥、发布不可变、隔离和拒绝绕过调试预算。"""

import asyncio

import pytest
from sqlalchemy import create_engine

from creativity_service.core.config import Settings
from creativity_service.core.database.audit import audit_database
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.schemas import AuditFilter
from creativity_service.modules.tools.schemas import (
    ToolCreate,
    ToolExecution,
    ToolRelease,
    ToolVersionEdit,
)
from tests.test_tools import MemoryRuns

pytestmark = pytest.mark.integration


async def test_create_race_freeze_release_and_disabled_metadata(tools_env):
    env = tools_env
    service = env.tools.management
    body = ToolCreate(
        tool_code="duplicate",
        name="并发工具",
        description="验证渠道内编码互斥",
        owner="负责人",
        source_type="builtin",
    )
    results = await asyncio.gather(
        *(service.create(env.context, body) for _ in range(2)), return_exceptions=True
    )
    assert sum(isinstance(r, ServiceError) and r.code == "CODE_EXISTS" for r in results) == 1
    frozen = await service.freeze(env.context, env.version.version.version_id, env.version.revision)
    with pytest.raises(ServiceError) as error:
        await service.edit_version(
            env.context,
            frozen.version.version_id,
            ToolVersionEdit(revision=frozen.revision, definition=env.definition),
        )
    assert error.value.code == "VERSION_FROZEN"
    detail = await service.release(
        env.context, env.tool.tool_id, ToolRelease(version_id=frozen.version.version_id)
    )
    assert detail.release_version_id == frozen.version.version_id
    disabled = await service.disable(env.context, env.tool.tool_id, env.tool.revision)
    assert disabled.tool.status.label == "已停用"
    assert disabled.versions[0].unavailable_reason == "工具已停用"
    assert "enable" in {a.action_key for a in disabled.tool.actions}
    with pytest.raises(ServiceError) as stale:
        await service.enable(env.context, env.tool.tool_id, env.tool.revision)
    assert stale.value.code == "REVISION_CONFLICT"
    enabled = await service.enable(env.context, env.tool.tool_id, disabled.tool.revision)
    assert enabled.tool.status.value == "ACTIVE"
    assert enabled.release_version_id == detail.release_version_id
    assert enabled.versions[0].unavailable_reason is None
    events = await env.iam.audit.page(
        await env.iam.authentication.admin_session(
            env.client.headers["Authorization"].removeprefix("Bearer "), "tool-audit"
        ),
        AuditFilter(),
    )
    assert "启用工具" in {row.action_name for row in events.items}


async def test_management_api_schema_errors_and_no_arbitrary_execution_route(tools_env):
    env = tools_env
    response = await env.client.get("/admin/v1/tools")
    assert response.status_code == 200 and response.json()["items"][0]["name"] == "精确求和"
    path = f"/admin/v1/tool-versions/{env.version.version.version_id}/tests"
    response = await env.client.post(
        path, json={"revision": 1, "arguments": {"values": ["1.00"], "channel_id": "evil"}}
    )
    assert response.status_code == 422
    assert response.json()["error"]["fields"][0]["path"] == ["channel_id"]
    response = await env.client.post(path, json={"revision": 1, "arguments": {"values": ["1.00"]}})
    assert (
        response.status_code == 503 and response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    )
    for path in (
        "/api/v1/tools/execute",
        f"/api/v1/tool-versions/{env.version.version.version_id}/tests",
    ):
        assert (await env.client.post(path, json={})).status_code == 404


async def test_actual_tool_schema_has_only_permitted_storage_definitions(tools_env):
    engine = create_engine(Settings().database_url.get_secret_value())
    try:
        with engine.connect() as connection:
            assert audit_database(connection, tools_env.schema) == []
    finally:
        engine.dispose()


async def test_real_call_attempt_evidence_cache_and_fixed_dependency(tools_env):
    env = tools_env
    service = env.tools.management
    definition = env.definition.model_copy(
        update={"cache_policy": env.definition.cache_policy.model_copy(update={"ttl_seconds": 5})}
    )
    edited = await service.edit_version(
        env.context,
        env.version.version.version_id,
        ToolVersionEdit(revision=1, definition=definition),
    )
    frozen = await service.freeze(env.context, edited.version.version_id, edited.revision)
    assert (
        await service.check_dependency(env.context, frozen.version.version_id)
    ).state == "PUBLISHED"
    runs = MemoryRuns()
    runs.allowed = {frozen.version.version_id}
    env.tools.executor.runs = runs
    call = ToolExecution(
        run_id="run_sum",
        step_id="step_sum",
        tool_version_id=frozen.version.version_id,
        arguments={"values": ["0.1", "0.2"]},
    )
    first = await env.tools.executor.execute(env.context, call)
    cached = await env.tools.executor.execute(
        env.context, call.model_copy(update={"run_id": "run_cached"})
    )
    assert first == cached and first.data == {"sum": "0.3"}
    assert len(runs.starts) == 1
    calls = await service.calls(env.context, env.tool.tool_id)
    assert {c.state.value for c in calls} == {"SUCCEEDED", "CACHED"}
    assert all(c.redacted_arguments == {"values": "已脱敏"} for c in calls)
    assert all(c.evidence_ids == [first.evidence_refs[0].evidence_id] for c in calls)
    records = await service.repository.rows(env.context, "evidence_refs")
    assert len(records) == 1 and records[0]["source_id"] == first.evidence_refs[0].source_id
