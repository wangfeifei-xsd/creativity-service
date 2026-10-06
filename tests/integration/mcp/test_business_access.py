"""20 的通用 MCP 配置闭环；真实 TCP 与存储，模型响应明确为测试替身。"""

import asyncio
import base64
import json
from contextlib import ExitStack
from datetime import timedelta
from types import SimpleNamespace

import pytest

from creativity_service.core.auth.types import IdentitySource
from creativity_service.core.context import TaskEnvelope
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.integrations.business.delegation import DelegationClaims, bind_request, sign
from creativity_service.integrations.tools import AdapterRequest
from creativity_service.modules.agents.schemas import AgentBindings
from creativity_service.modules.channels.schemas import ClientCreate, KeyCreate, TokenExchange
from creativity_service.modules.integrations.assembly import build_integration_services
from creativity_service.modules.integrations.schemas import DelegationKeyCreate
from creativity_service.modules.integrations.subject_contracts import SubjectReviewSave
from creativity_service.modules.mcp.assembly import build_mcp_service
from creativity_service.modules.mcp.schemas import (
    McpCreate,
    McpCredential,
    McpDiscovery,
    McpImportInput,
)
from creativity_service.modules.tools.schemas import ToolExecution, ToolRelease
from creativity_service.workers.executor import execute_message
from examples.mcp.server import serve
from tests.integration.agents.conftest import agent_env as agent_env
from tests.integration.agents.test_agents import publish
from tests.integration.integrations.conftest import TestKeys
from tests.integration.runtime.conftest import runtime_env as runtime_env
from tests.test_tools import MemoryRuns

pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize(
        "agent_env",
        [
            {
                "environment": "test",
                "independent_actions": ["release:publish", "data:read_sensitive", "data:export"],
            }
        ],
        indirect=True,
    ),
]


@pytest.fixture
async def business_env(runtime_env, request):
    env = runtime_env
    actions = ["run:create", "run:read", "run:content", "data:read_sensitive"]
    actions += getattr(request, "param", [])
    client = await env.services.channels.create_client(
        env.tenant.manager,
        env.context.scope.channel_id,
        ClientCreate(
            name="通用接入后端",
            environment="test",
            scopes=actions,
        ),
    )
    key = await env.services.keys.create(
        env.tenant.manager,
        env.context.scope.channel_id,
        KeyCreate(
            name="通用接入 Key",
            client_id=client.client_id,
            environment="test",
            scopes=actions,
            expires_at=utcnow() + timedelta(minutes=20),
        ),
    )
    token = await env.services.keys.exchange(TokenExchange(api_key=key.api_key), "mcp-test-token")
    env.identity = SimpleNamespace(
        client=client,
        key=key,
        token=token,
        context=await env.iam.authentication.authenticate(token.access_token, "service"),
    )
    with ExitStack() as stack:
        env.sources = [stack.enter_context(serve(profile)) for profile in ("archive", "matrix")]
        outbound = OutboundPolicy(
            tuple(
                Destination(
                    env.context.scope.channel_id,
                    "test",
                    "mcp",
                    "127.0.0.1",
                    port=s.port,
                    scheme="http",
                    allowed_networks=("127.0.0.1/32",),
                )
                for s in env.sources
            )
        )
        env.mcp = build_mcp_service(
            env.engine, env.iam.authorization, env.tools, key_provider=TestKeys(), outbound=outbound
        )
        env.bundle = build_integration_services(
            env.engine, env.iam.authorization, provider=TestKeys(), mcp=env.mcp
        )
        app = env.client._transport.app
        app.state.mcp, app.state.integrations, app.state.delegation = (
            env.mcp,
            env.bundle,
            env.bundle.delegation,
        )
        env.key = await env.bundle.keys.create(
            env.context,
            DelegationKeyCreate(
                client_id=env.identity.client.client_id,
                issuer="source.example",
                audience="creativity-api",
                expires_at=utcnow() + timedelta(days=1),
                clock_skew_seconds=1,
            ),
        )
        now = int(utcnow().timestamp())
        env.claims = DelegationClaims(
            subject_type="member",
            subject_id="reader-a",
            actions=actions,
            resources={
                kind: ["*"]
                for kind in (
                    "agent",
                    "model",
                    "model_route",
                    "prompt",
                    "tool",
                    "run",
                    "skill",
                    "conversation",
                    "artifact",
                    "content",
                )
            },
            issuer="source.example",
            audience="creativity-api",
            issued_at=now,
            expires_at=now + 300,
            nonce="controlled_mcp_20_nonce",
            request=bind_request("POST", "/api/v1/runs", b"{}", "mcp-20"),
        )
        env.connections, env.snapshots, env.imports, env.versions = [], [], [], []
        for source in env.sources:
            source.scope = env.context.scope.model_copy(
                update={"subject_type": "member", "subject_id": "reader-a"}
            ).model_dump()
            source.permissions["reader-a"] = {
                "actions": env.claims.actions,
                "resources": env.claims.resources,
            }
            conn = await env.mcp.create(
                env.context, McpCreate(name=source.title, endpoint=source.endpoint)
            )
            await env.mcp.rotate(
                env.context,
                conn.connection_id,
                McpCredential(revision=conn.revision, token=source.token),
            )
            snapshot = await env.mcp.probe(env.context, conn.connection_id, True)
            assert isinstance(snapshot, McpDiscovery), snapshot
            current = await env.mcp.detail(env.context, conn.connection_id)
            conn = await env.mcp.set_enabled(
                env.context, conn.connection_id, current.connection.revision, True
            )
            imported = await env.mcp.import_tool(
                env.context,
                conn.connection_id,
                McpImportInput(
                    discovery_id=snapshot.discovery_id,
                    remote_tool_name=source.tool_name,
                    name=source.title,
                    description="授权查询及确定性计算",
                    owner="测试负责人",
                    version_label="配置初版",
                    effect_type="READ_ONLY",
                    required_scopes=("run:create",),
                    timeout_seconds=10,
                    max_result_size=65536,
                    output_schema=source.output_schema,
                ),
            )
            frozen = await env.tools.management.freeze(env.context, imported.imported_version, 1)
            await env.tools.management.release(
                env.context,
                imported.local_tool_id,
                ToolRelease(version_id=frozen.version.version_id),
            )
            env.connections.append(conn)
            env.snapshots.append(snapshot)
            env.imports.append(imported)
            env.versions.append(frozen)
        env.review = await env.bundle.subject_review.save(
            env.context,
            SubjectReviewSave(
                client_id=env.identity.client.client_id,
                connection_id=env.connections[0].connection_id,
                discovery_id=env.snapshots[0].discovery_id,
                remote_tool_name="access.review-current",
                timeout_seconds=1,
            ),
        )
        env.subject = await env.bundle.delegation.verify(
            env.identity.context,
            sign(env.claims, env.key.key.kid, base64.b64decode(env.key.signing_secret)),
            env.claims.request,
        )
        env.worker = IdentitySource(
            scope=env.subject.scope,
            source_type="service",
            principal_id=env.subject.principal_id,
            client_id=env.subject.client_id,
            key_id=env.subject.key_id,
            delegation_id=env.subject.delegation_id,
        ).worker_context("worker-mcp-20")
        yield env


async def adapter_call(env, index=0, arguments=None, context=None):
    definition = env.versions[index].definition
    adapter = env.tools.registry.resolve(env.worker.scope, definition.binding).adapter
    return await adapter.invoke(
        AdapterRequest(
            context or env.worker,
            env.sources[index].arguments if arguments is None else arguments,
            "attempt-business-20",
            definition,
            "run-business-20",
        )
    )


async def test_configured_reader_and_two_business_schemas(business_env):
    env = business_env
    authority = await env.bundle.delegation.read_current(env.worker)
    assert authority.scope == env.subject.scope
    for index, source in enumerate(env.sources):
        raw = await adapter_call(env, index)
        assert raw.data == source.data
        result = await env.tools.executor.validate_result(
            env.worker,
            ToolExecution(
                run_id="run-business-20",
                step_id="step-business-20",
                tool_version_id=env.imports[index].imported_version,
                arguments=source.arguments,
            ),
            env.versions[index].definition,
            raw,
            f"tool_call_{index}",
        )
        assert result.coverage["result_status"] == "complete"
        assert result.coverage["source_evidence"][0]["source_id"] == "source/document/note-a"
        assert len(result.evidence_refs) == 2 and result.source_version == "fixture-data-v1"
        sent = next(c for c in source.calls if c["name"] == source.tool_name)
        identity = sent["_meta"]["creativity.identity"]
        assert identity["subject_id"] == "reader-a" and identity["run_id"] == "run-business-20"
        assert sent["arguments"] == source.arguments and source.token not in json.dumps(identity)
        assert identity["resources"]["tool"] == [env.imports[index].local_tool_id]
    with pytest.raises(ServiceError, match="身份复核工具"):
        await env.mcp.import_tool(
            env.context,
            env.connections[0].connection_id,
            McpImportInput(
                discovery_id=env.snapshots[0].discovery_id,
                remote_tool_name="access.review-current",
                name="不能给模型",
                description="专用身份查询",
                owner="测试负责人",
                version_label="不能导入",
                effect_type="READ_ONLY",
                required_scopes=("run:create",),
                timeout_seconds=5,
                max_result_size=65536,
                output_schema={},
            ),
        )


@pytest.mark.parametrize(
    "mode,code",
    [
        ("revoked", "SUBJECT_REVIEW_DENIED"),
        ("widen", "DELEGATION_FORBIDDEN"),
        ("expired", "SUBJECT_REVIEW_DENIED"),
        ("wrong_scope", "SUBJECT_REVIEW_DENIED"),
        ("error", "SUBJECT_REVIEW_INVALID"),
    ],
)
async def test_current_subject_refusal_prevents_business_call(business_env, mode, code):
    env = business_env
    env.sources[0].review_mode = mode
    with pytest.raises(ServiceError) as error:
        await adapter_call(env)
    assert error.value.code == code
    assert not any(c["name"] == env.sources[0].tool_name for c in env.sources[0].calls)


async def test_timeout_missing_configuration_and_delegation_revocation(business_env):
    env = business_env
    env.sources[0].review_delay = 1.2
    with pytest.raises(ServiceError) as error:
        await adapter_call(env)
    assert error.value.code == "SUBJECT_REVIEW_UNAVAILABLE"
    env.sources[0].review_delay = 0
    body = SubjectReviewSave(**env.review.model_dump(include=set(SubjectReviewSave.model_fields)))
    disabled = await env.bundle.subject_review.save(
        env.context, body.model_copy(update={"enabled": False})
    )
    with pytest.raises(ServiceError, match="未配置"):
        await adapter_call(env)
    await env.bundle.subject_review.save(
        env.context, body.model_copy(update={"revision": disabled.revision})
    )
    await env.bundle.keys.revoke(env.context, env.key.key.kid, env.key.key.revision)
    with pytest.raises(ServiceError) as error:
        await adapter_call(env)
    assert error.value.code == "DELEGATION_INVALID"


async def test_result_status_scope_effect_and_entity_authorization(business_env):
    env = business_env
    for status in ("empty", "missing", "partial"):
        env.sources[0].mode = status
        result = await adapter_call(env)
        assert result.coverage["result_status"] == status
        assert result.has_more == (status == "partial")
    for mode, code in [
        ("forbidden", "MCP_REMOTE_FORBIDDEN"),
        ("timeout", "MCP_REMOTE_TIMEOUT"),
        ("wrong_scope", "TOOL_RESULT_INVALID"),
        ("bad_schema", "MCP_RESULT_INVALID"),
    ]:
        env.sources[0].mode = mode
        with pytest.raises(ServiceError) as error:
            await adapter_call(env)
        assert error.value.code == code
    env.sources[0].mode = "normal"
    with pytest.raises(ServiceError) as error:
        await adapter_call(env, arguments={"document": "another-domain-note"})
    assert error.value.code == "MCP_REMOTE_FORBIDDEN"
    env.sources[0].actual_effect = "EXTERNAL_WRITE"
    with pytest.raises(ServiceError) as error:
        await adapter_call(env)
    assert error.value.code == "TOOL_EFFECT_MISMATCH"


async def test_model_identity_override_is_blocked_before_business_dispatch(business_env):
    env = business_env
    runs = MemoryRuns()
    runs.allowed = {env.imports[0].imported_version}
    env.tools.executor.runs = runs
    for field in ("subject", "subject_id", "environment", "_meta"):
        with pytest.raises(ServiceError) as error:
            await env.tools.executor.execute(
                env.worker,
                ToolExecution(
                    run_id="run-model-override",
                    step_id="step-override",
                    tool_version_id=env.imports[0].imported_version,
                    arguments={**env.sources[0].arguments, field: "forged"},
                ),
            )
        assert error.value.code == "TOOL_INPUT_INVALID"
    assert not any(c["name"] == env.sources[0].tool_name for c in env.sources[0].calls)


async def test_connection_revision_never_upgrades_published_binding(business_env):
    env = business_env
    current = await env.mcp.detail(env.context, env.connections[1].connection_id)
    await env.mcp.rotate(
        env.context,
        current.connection.connection_id,
        McpCredential(revision=current.connection.revision, token="new-fixture-token"),
    )
    env.sources[1].token = "new-fixture-token"
    await env.mcp.probe(env.context, current.connection.connection_id, True)
    current = await env.mcp.detail(env.context, current.connection.connection_id)
    await env.mcp.set_enabled(
        env.context, current.connection.connection_id, current.connection.revision, True
    )
    with pytest.raises(ServiceError) as error:
        await adapter_call(env, 1)
    assert error.value.code == "MCP_TOOL_CHANGED"


@pytest.mark.parametrize("index,delivery", [(0, "sync"), (1, "stream")])
async def test_published_agent_uses_mcp_through_shared_worker_pipeline(
    business_env, index, delivery
):
    env = business_env
    definition = env.definition.model_copy(
        update={
            "entrypoint": "tool_loop.v1",
            "workflow_type": "tool_loop",
            "bindings": AgentBindings(
                prompt_version=env.definition.bindings.prompt_version,
                model_route_version=env.definition.bindings.model_route_version,
                tool_versions=(env.imports[index].imported_version,),
            ),
        }
    )
    detail = await env.agents.create(
        env.context, env.body.model_copy(update={"definition": definition})
    )
    await publish(env, detail)
    original = env.adapter.events
    env.adapter.tool_arguments = json.dumps(env.sources[index].arguments)

    async def model_events(context, config, request, *args, **kwargs):
        if env.adapter.calls:
            request = request.model_copy(update={"tools": []})
        async for event in original(context, config, request, *args, **kwargs):
            yield event

    env.adapter.events = model_events
    body = json.dumps(
        {
            "agent_code": env.body.agent_code,
            "input": {"request": "执行授权查询"},
            "delivery": delivery,
        }
    ).encode()
    expires_at = int(utcnow().timestamp()) + 10
    claim = env.claims.model_copy(
        update={
            "issued_at": expires_at - 10,
            "expires_at": expires_at,
            "request": bind_request("POST", "/api/v1/runs", body, "agent-mcp-20"),
            "nonce": "agent_mcp_20_unique_nonce",
        }
    )
    header = {
        "Authorization": "Bearer " + env.identity.token.access_token,
        "X-Business-Delegation": sign(
            claim, env.key.key.kid, base64.b64decode(env.key.signing_secret)
        ),
        "Idempotency-Key": "agent-mcp-20",
        "Content-Type": "application/json",
    }
    response = await env.client.post("/api/v1/runs", content=body, headers=header)
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    token_key = env.iam.authentication.tokens.token_key(env.identity.context.token_digest)
    assert await env.redis.expire(token_key, 1)
    await asyncio.sleep(max(0, expires_at + 1.1 - utcnow().timestamp()))
    with pytest.raises(ServiceError):
        await env.iam.authentication.authenticate(env.identity.token.access_token, "service")
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.worker.scope.channel_id, run_id=run_id),
        "worker-mcp",
        env.runtime,
    )
    value = await env.runs.get_run(env.worker, run_id)
    assert value.state == "SUCCEEDED", value.error
    assert sum(c["name"] == env.sources[index].tool_name for c in env.sources[index].calls) == 1
    records = await env.tools.management.repository.rows(
        env.worker, "tool_calls", tool_id=env.imports[index].local_tool_id
    )
    assert records[0]["state"] == "SUCCEEDED"
    assert records[0]["source_request_id"].startswith("fixture-source-")
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.worker.scope.channel_id, run_id=run_id),
        "worker-recovery",
        env.runtime,
    )
    assert sum(c["name"] == env.sources[index].tool_name for c in env.sources[index].calls) == 1
    refreshed = await env.services.keys.exchange(
        TokenExchange(api_key=env.identity.key.api_key), "reauthenticate"
    )
    header["Authorization"] = "Bearer " + refreshed.access_token
    header.pop("Idempotency-Key")
    path = f"/api/v1/runs/{run_id}/events"
    event_claim = env.claims.model_copy(
        update={"request": bind_request("GET", path, b""), "nonce": "agent_mcp_20_event_nonce"}
    )
    header["X-Business-Delegation"] = sign(
        event_claim, env.key.key.kid, base64.b64decode(env.key.signing_secret)
    )
    response = await env.client.get(path, headers=header)
    assert response.status_code == 200 and "event: result" in response.text, response.text


@pytest.mark.parametrize("revoked", ["key", "channel"])
async def test_platform_revocation_blocks_later_mcp_calls(business_env, revoked):
    env = business_env
    await adapter_call(env)
    count = len(env.sources[0].calls)
    if revoked == "key":
        await env.services.keys.revoke(
            env.tenant.manager,
            env.context.scope.channel_id,
            env.identity.key.key.key_id,
            env.identity.key.key.revision,
        )
    else:
        await env.services.lifecycle.change(
            env.tenant.manager, env.context.scope.channel_id, "suspend", env.tenant.channel.revision
        )
    with pytest.raises(ServiceError):
        await adapter_call(env)
    assert len(env.sources[0].calls) == count


async def test_schema_change_missing_reader_and_narrowed_permissions(business_env):
    env = business_env
    env.sources[1].schema_revision = 2
    with pytest.raises(ServiceError) as error:
        await adapter_call(env, 1)
    assert error.value.code == "MCP_TOOL_CHANGED"
    env.sources[1].schema_revision = 1
    with pytest.raises(ServiceError):
        await adapter_call(env, 1)
    env.sources[0].permissions["reader-a"] = {"actions": ["run:read"], "resources": {"run": ["*"]}}
    narrowed = await env.bundle.delegation.read_current(env.worker)
    assert narrowed.actions == {"run:read"}
    with pytest.raises(ServiceError):
        await adapter_call(env)
    env.bundle.delegation.current_subjects = None
    with pytest.raises(ServiceError) as error:
        await env.bundle.delegation.read_current(env.worker)
    assert error.value.code == "DEPENDENCY_UNAVAILABLE"


async def test_subject_binding_updates_are_serialized_and_scope_is_server_owned(business_env):
    env = business_env
    payload = env.review.model_dump(include=set(SubjectReviewSave.model_fields))
    results = await asyncio.gather(
        *(env.client.post("/admin/v1/subject-review-bindings", json=payload) for _ in range(4))
    )
    assert sorted(r.status_code for r in results) == [200, 409, 409, 409]
    response = await env.client.post(
        "/admin/v1/subject-review-bindings", json={**payload, "channel_id": "forged"}
    )
    assert response.status_code == 422
    other = env.worker.model_copy(
        update={"scope": env.worker.scope.model_copy(update={"environment": "prod"})}
    )
    with pytest.raises(ServiceError):
        await env.bundle.subject_review.binding(other)
