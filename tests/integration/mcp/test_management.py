"""MCP-A01—A06：导入、并发、权限、契约变化、结果与引用边界。"""

import asyncio

import pytest

from creativity_service.core.context import Scope
from creativity_service.core.database import Repository
from creativity_service.core.database.tables import metadata
from creativity_service.core.primitives import ServiceError
from creativity_service.integrations.tools import AdapterRequest
from creativity_service.modules.mcp.schemas import (
    McpCreate,
    McpCredential,
    McpDiscovery,
    McpImportInput,
)
from creativity_service.modules.tools.schemas import ToolExecution

pytestmark = pytest.mark.integration


def import_body(snapshot):
    return McpImportInput(
        discovery_id=snapshot.discovery_id,
        remote_tool_name="lookup",
        name="目录查询",
        description="查询授权范围内目录",
        owner="平台管理员",
        version_label="初始版本",
        effect_type="READ_ONLY",
        required_scopes=("run:create",),
        timeout_seconds=5,
        max_result_size=2048,
        subject_required=False,
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )


async def ready(env):
    conn = await env.mcp.create(
        env.context, McpCreate(name="远端目录", endpoint=env.source.endpoint)
    )
    with pytest.raises(ServiceError) as error:
        await env.mcp.set_enabled(env.context, conn.connection_id, conn.revision, True)
    assert error.value.code == "MCP_TEST_REQUIRED"
    snapshot = await env.mcp.probe(env.context, conn.connection_id, True)
    assert isinstance(snapshot, McpDiscovery)
    detail = await env.mcp.detail(env.context, conn.connection_id)
    await env.mcp.set_enabled(env.context, conn.connection_id, detail.connection.revision, True)
    return conn, snapshot


async def test_discovery_does_not_grant_and_atomic_repeat_import(mcp_env):
    env = mcp_env
    conn, snapshot = await ready(env)
    async with env.engine.connect() as connection:
        assert len(await env.tools.management.repository.rows(env.context, "tools")) == 1
        versions = await Repository(metadata.tables["resource_versions"], env.context.scope).find(
            connection, resource_type="agent"
        )
        assert not versions
    body = import_body(snapshot)
    results = await asyncio.gather(
        *(env.mcp.import_tool(env.context, conn.connection_id, body) for _ in range(4))
    )
    assert len({r.import_id for r in results}) == 1
    result = results[0]
    detail = await env.tools.management.detail(env.context, result.local_tool_id)
    assert (
        len(detail.versions) == 1
        and detail.versions[0].version.state == "DRAFT"
        and detail.release_version_id is None
    )
    assert len(await env.mcp.rows(env.context, "mcp_imports")) == 1
    env.source.schema_revision = 2
    changed = await env.mcp.probe(env.context, conn.connection_id, True)
    diff = await env.mcp.diff(env.context, conn.connection_id, changed.discovery_id)
    assert diff.items[0].breaking and "schema" in diff.items[0].changes
    original = await env.mcp.get(env.context, "mcp_discoveries", snapshot.discovery_id)
    assert original["tool_definitions"][0]["schema_hash"] == snapshot.tools[0].schema_hash
    view = await env.tools.management.version_detail(env.context, result.imported_version)
    assert not view.execution_enabled and view.definition.input_schema.get("properties") == {
        "query": {"type": "string"}
    }
    with pytest.raises(ServiceError):
        await env.mcp.check_binding(env.context, view.definition)


async def test_cross_scope_same_name_credential_rotation_and_revocation(mcp_env, caplog):
    env = mcp_env
    conn, snapshot = await ready(env)
    another = await env.mcp.create(
        env.context, McpCreate(name="另一来源", endpoint=env.source.endpoint)
    )
    second = await env.mcp.probe(env.context, another.connection_id, True)
    a = await env.mcp.import_tool(env.context, conn.connection_id, import_body(snapshot))
    b = await env.mcp.import_tool(env.context, another.connection_id, import_body(second))
    assert a.local_tool_id != b.local_tool_id
    denied = env.context.model_copy(
        update={
            "scope": Scope(
                channel_id="another_channel", environment="test", data_scope_id="other_domain"
            )
        }
    )
    with pytest.raises(ServiceError):
        await env.mcp.get(denied, "mcp_connections", conn.connection_id)
    detail = await env.mcp.detail(env.context, conn.connection_id)
    changed = await env.mcp.rotate(
        env.context,
        conn.connection_id,
        McpCredential(revision=detail.connection.revision, token="secret-first"),
    )
    assert changed.status.value == "DISABLED" and changed.credential_mask == "••••••••"
    env.source.token = "secret-first"
    checked = await env.mcp.probe(env.context, conn.connection_id, False)
    assert checked.health.value == "HEALTHY"
    env.source.mode = "auth"
    failure = await env.mcp.probe(env.context, conn.connection_id, False)
    assert failure.error_category == "MCP_AUTH_FAILED"
    count = len(env.source.calls)
    with pytest.raises(ServiceError):
        await env.mcp.probe(env.context, conn.connection_id, False)
    assert len(env.source.calls) == count and "secret-first" not in caplog.text
    assert (
        "secret-first"
        not in (await env.mcp.detail(env.context, conn.connection_id)).model_dump_json()
    )


async def test_adapter_and_tool_result_validation(mcp_env):
    env = mcp_env
    conn, snapshot = await ready(env)
    mapping = await env.mcp.import_tool(env.context, conn.connection_id, import_body(snapshot))
    version = await env.tools.management.version_detail(env.context, mapping.imported_version)
    adapter = env.tools.registry.resolve(env.context.scope, version.definition.binding).adapter
    request = AdapterRequest(env.context, {"query": "目录"}, "attempt_mcp", version.definition)
    result = await adapter.invoke(request)
    assert result.data == {"value": "可用"}
    call = ToolExecution(
        run_id="run_mcp",
        step_id="step_mcp",
        tool_version_id=mapping.imported_version,
        arguments=request.arguments,
    )
    validated = await env.tools.executor.validate_result(
        env.context, call, version.definition, result, "call_mcp"
    )
    assert validated.data == result.data
    env.source.mode = "file"
    with pytest.raises(ServiceError, match="引用"):
        await adapter.invoke(request)
    env.source.mode = "large"
    with pytest.raises(ServiceError):
        await adapter.invoke(request)
    detail = await env.mcp.detail(env.context, conn.connection_id)
    await env.mcp.set_enabled(env.context, conn.connection_id, detail.connection.revision, False)
    before = len(env.source.calls)
    with pytest.raises(ServiceError):
        await adapter.invoke(request)
    assert len(env.source.calls) == before


async def test_http_contract_refuses_scope_override_and_unsupported_transport(mcp_env):
    env = mcp_env
    body = {"name": "远端", "endpoint": env.source.endpoint, "channel_id": "foreign"}
    response = await env.client.post("/admin/v1/mcp-connections", json=body)
    assert response.status_code == 422
    body.pop("channel_id")
    body["transport"] = "stdio"
    response = await env.client.post("/admin/v1/mcp-connections", json=body)
    assert response.status_code == 422


async def test_unified_executor_records_attempt_and_preserves_inflight_result(mcp_env):
    from tests.test_tools import MemoryRuns

    env = mcp_env
    conn, snapshot = await ready(env)
    mapping = await env.mcp.import_tool(env.context, conn.connection_id, import_body(snapshot))
    frozen = await env.tools.management.freeze(env.context, mapping.imported_version, 1)
    runs = MemoryRuns()
    runs.allowed = {frozen.version.version_id}
    env.tools.executor.runs = runs
    call = ToolExecution(
        run_id="run_mcp_actual",
        step_id="step_mcp",
        tool_version_id=frozen.version.version_id,
        arguments={"query": "目录"},
    )
    actual_call = env.mcp.transport.call

    async def finish_then_disable(*args, **kwargs):
        result = await actual_call(*args, **kwargs)
        current = await env.mcp.detail(env.context, conn.connection_id)
        await env.mcp.set_enabled(
            env.context, conn.connection_id, current.connection.revision, False
        )
        return result

    env.mcp.transport.call = finish_then_disable
    result = await env.tools.executor.execute(env.context, call)
    assert (
        result.data == {"value": "可用"}
        and len(runs.starts) == 1
        and runs.finishes[0].state == "SUCCEEDED"
    )
    records = await env.tools.management.calls(env.context, mapping.local_tool_id)
    assert records[0].state.value == "SUCCEEDED" and records[0].source_request_id
    with pytest.raises(ServiceError):
        await env.tools.executor.execute(env.context, call)
    assert len(runs.starts) == 1


async def test_health_schedule_claim_threshold_and_manual_status_are_independent(mcp_env):
    from datetime import timedelta

    from creativity_service.core.database import transaction
    from creativity_service.core.locking import record_key
    from creativity_service.core.primitives import utcnow
    from creativity_service.modules.mcp.repositories import repository
    from creativity_service.modules.mcp.tasks import check_due

    env = mcp_env
    conn, _ = await ready(env)
    scope = env.context.scope
    repo = repository(scope, "mcp_connections")
    async with transaction(
        env.engine, scope, [record_key(scope.channel_id, "mcp_connections", conn.connection_id)]
    ) as uow:
        row = await repo.get(uow.connection, conn.connection_id)
        await repo.change(
            uow,
            conn.connection_id,
            row["revision"],
            {"next_check_at": utcnow() - timedelta(seconds=1)},
        )
    assert (
        sum(
            await asyncio.gather(
                *(check_due(env.mcp, env.context, conn.connection_id) for _ in range(3))
            )
        )
        == 1
    )
    env.source.mode = "protocol"
    for _ in range(3):
        await env.mcp.probe(env.context, conn.connection_id, False)
    detail = await env.mcp.detail(env.context, conn.connection_id)
    assert (
        detail.connection.health.value == "UNAVAILABLE"
        and detail.connection.status.value == "ENABLED"
    )


async def test_import_failure_rolls_back_mapping_and_tool(mcp_env, monkeypatch):
    env = mcp_env
    conn, snapshot = await ready(env)
    real = env.tools.management.import_draft

    async def fail(*args, **kwargs):
        await real(*args, **kwargs)
        raise ServiceError("TEST_INTERRUPTED", "模拟草稿写入后事务中断", 503)

    monkeypatch.setattr(env.tools.management, "import_draft", fail)
    with pytest.raises(ServiceError):
        await env.mcp.import_tool(env.context, conn.connection_id, import_body(snapshot))
    assert not await env.mcp.rows(env.context, "mcp_imports")
    assert len(await env.tools.management.repository.rows(env.context, "tools")) == 1


async def test_remote_drift_blocks_old_binding_and_imports_new_target_version(mcp_env):
    env = mcp_env
    conn, snapshot = await ready(env)
    first = await env.mcp.import_tool(env.context, conn.connection_id, import_body(snapshot))
    version = await env.tools.management.version_detail(env.context, first.imported_version)
    adapter = env.tools.registry.resolve(env.context.scope, version.definition.binding).adapter
    env.source.schema_revision = 2
    with pytest.raises(ServiceError) as error:
        await adapter.invoke(
            AdapterRequest(env.context, {"query": "目录"}, "attempt_changed", version.definition)
        )
    assert error.value.code == "MCP_TOOL_CHANGED"
    assert not (
        await env.tools.management.version_detail(env.context, first.imported_version)
    ).execution_enabled
    second = await env.mcp.probe(env.context, conn.connection_id, True)
    new = await env.mcp.import_tool(
        env.context,
        conn.connection_id,
        import_body(second).model_copy(
            update={"target_tool_id": first.local_tool_id, "version_label": "修订版本"}
        ),
    )
    assert (
        first.local_tool_id == new.local_tool_id and first.imported_version != new.imported_version
    )
    detail = await env.tools.management.detail(env.context, new.local_tool_id)
    assert len(detail.versions) == 2 and detail.release_version_id is None
    options = await env.tools.management.bindings(env.context, new.local_tool_id)
    assert any(o.name == "目录查询" and o.execution_enabled for o in options)


async def test_mcp_structured_file_reference_is_rejected_by_tool_layer(mcp_env):
    env = mcp_env
    conn, snapshot = await ready(env)
    mapping = await env.mcp.import_tool(env.context, conn.connection_id, import_body(snapshot))
    version = await env.tools.management.version_detail(env.context, mapping.imported_version)
    adapter = env.tools.registry.resolve(env.context.scope, version.definition.binding).adapter
    raw = await adapter.invoke(
        AdapterRequest(env.context, {"query": "目录"}, "attempt_file", version.definition)
    )
    raw = raw.model_copy(update={"data": {"value": "可用", "artifact_id": "other_channel_file"}})
    definition = version.definition.model_copy(update={"output_schema": {"type": "object"}})
    call = ToolExecution(
        run_id="run_file",
        step_id="step_file",
        tool_version_id=mapping.imported_version,
        arguments={"query": "目录"},
    )
    with pytest.raises(ServiceError, match="文件引用"):
        await env.tools.executor.validate_result(env.context, call, definition, raw, "call_file")


async def test_execution_uses_only_bound_credential_and_injects_server_identity(
    mcp_env, monkeypatch
):
    env = mcp_env
    conn, _ = await ready(env)
    current = await env.mcp.detail(env.context, conn.connection_id)
    await env.mcp.rotate(
        env.context,
        conn.connection_id,
        McpCredential(revision=current.connection.revision, token="bound-service-token"),
    )
    env.source.token = "bound-service-token"
    snapshot = await env.mcp.probe(env.context, conn.connection_id, True)
    current = await env.mcp.detail(env.context, conn.connection_id)
    await env.mcp.set_enabled(env.context, conn.connection_id, current.connection.revision, True)
    mapping = await env.mcp.import_tool(env.context, conn.connection_id, import_body(snapshot))
    version = await env.tools.management.version_detail(env.context, mapping.imported_version)
    adapter = env.tools.registry.resolve(env.context.scope, version.definition.binding).adapter
    original = env.iam.authorization.boundary

    async def authorization(context, action, resource_type, resource_id):
        if action.startswith("credential:"):
            raise ServiceError("FORBIDDEN", "业务调用者没有凭据管理权限", 403)
        await original(context, action, resource_type, resource_id)

    monkeypatch.setattr(env.iam.authorization, "boundary", authorization)
    result = await adapter.invoke(
        AdapterRequest(env.context, {"query": "目录"}, "attempt_bound", version.definition)
    )
    assert result.data == {"value": "可用"}
    message, token, _ = next(item for item in env.source.calls if item[0]["method"] == "tools/call")
    assert message["params"]["arguments"] == {"query": "目录"}
    assert (
        message["params"]["_meta"]["creativity.identity"]["channel_id"]
        == env.context.scope.channel_id
    )
    assert token == "Bearer bound-service-token"
