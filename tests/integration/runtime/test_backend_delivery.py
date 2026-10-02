"""22：同一独立客户端经真实 HTTP、Redis、PostgreSQL 和 MCP 调用两套配置。"""

import asyncio
import base64
import copy
import json
import os
import secrets
import socket
from datetime import timedelta
from pathlib import Path

import pytest
import uvicorn

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database import transaction
from creativity_service.core.primitives import utcnow
from creativity_service.core.services import build_core_services
from creativity_service.integrations.business.delegation import split_envelope, verify_payload
from creativity_service.modules.agents.schemas import InputSource
from creativity_service.modules.channels.schemas import KeyRotate
from creativity_service.modules.models.schemas import RouteInput, RouteVersionInput
from creativity_service.modules.runs.repositories import rows, save
from creativity_service.workers.executor import execute_message
from examples.backend.client import BusinessBackendClient, PlatformError, Principal
from tests.integration.agents.test_agents import publish
from tests.integration.mcp.test_business_access import business_env as business_env
from tests.integration.runtime.test_configuration import (
    case,
    configure_prompt,
    configured,
    import_skill,
    validate_agent,
)
from tests.integration.runtime.test_execution import pytestmark as runtime_marks

pytestmark = [
    *runtime_marks,
    pytest.mark.parametrize(
        "business_env",
        [
            [
                "conversation:read",
                "conversation:write",
                "artifact:upload",
                "artifact:download",
                "data:export",
                "content:derive",
            ]
        ],
        indirect=True,
    ),
]


@pytest.fixture
async def delivery_env(business_env):
    env = business_env
    env.configurations = {}
    env.agent_ids = {}
    route = await env.models.routing.create(
        env.tenant.manager, RouteInput(code="delivery_stream", name="流式交付验证")
    )
    route_version = await env.models.routing.create_version(
        env.tenant.manager,
        route.id,
        RouteVersionInput(
            label="流式初版",
            primary_model=env.model.id,
            required_capabilities=["text", "structured_output", "tools", "streaming"],
        ),
    )
    env.definition = env.definition.model_copy(
        update={
            "bindings": env.definition.bindings.model_copy(
                update={"model_route_version": route_version.version_id}
            )
        }
    )
    for name in ("text-brief", "archive-answer"):
        await configure_prompt(env, name)
        tool_id = env.versions[0].version.version_id if name == "archive-answer" else None
        skill = await import_skill(env, name, {"archive.find-notes": tool_id} if tool_id else {})
        version = skill.versions[0]
        frozen = await env.skills.freeze(env.context, version.version_id, version.revision)
        body = configured(env, name, frozen.version_id, tool_id)
        if name == "text-brief":
            body = body.model_copy(
                update={
                    "definition": body.definition.model_copy(
                        update={
                            "context": body.definition.context.model_copy(
                                update={"conversation_enabled": True}
                            ),
                        }
                    )
                }
            )
        detail = await env.agents.create(env.context, body)
        await validate_agent(env, detail)
        await publish(env, detail)
        env.configurations[name] = body
        env.agent_ids[name] = detail.agent.agent_id
    app = env.client._transport.app
    app.state.core = build_core_services(
        env.engine, env.conversations.artifacts.store, env.iam.authorization
    )
    app.state.sync_wait_seconds = 0.01

    class Principals:
        async def current(self):
            return Principal(
                env.claims.subject_type,
                env.claims.subject_id,
                env.claims.data_scope.model_dump(),
                env.claims.actions,
                env.claims.resources,
            )

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            for _ in range(500):
                if server.started:
                    break
                await asyncio.sleep(0.01)
        async with BusinessBackendClient(
            f"http://127.0.0.1:{sock.getsockname()[1]}",
            env.identity.key.api_key,
            env.key.key.kid,
            base64.b64decode(env.key.signing_secret),
            env.claims.issuer,
            env.claims.audience,
            Principals(),
        ) as backend:
            env.backend = backend
            env.http_records = []

            async def record(response):
                env.http_records.append(
                    {
                        "method": response.request.method,
                        "path": response.request.url.path,
                        "status": response.status_code,
                        "request_id": response.headers.get("X-Request-ID"),
                    }
                )

            backend.http.event_hooks["response"] = [record]
            yield env
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


async def submit(env, name, key, delivery="async"):
    return await env.backend.submit(
        env.configurations[name].agent_code, case(name)["input"], key, delivery
    )


async def finish(env, receipt, name):
    env.adapter.responses = [copy.deepcopy(case(name)["fixture_output"])]

    async def evidence(context, attempt):
        if name == "archive-answer":
            messages = env.adapter.calls[-1][1].messages
            lookup = json.loads(messages[-1]["content"])["tool_results"]["lookup"]
            env.adapter.responses[0]["evidence_refs"] = lookup["evidence_refs"]

    env.adapter.before = evidence
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt["run_id"]),
        "delivery-worker",
        env.runtime,
    )
    env.adapter.before = None


async def evidence_record(env, name, receipt):
    value = await env.backend.query(receipt["run_id"])
    calls = await env.tools.management.repository.rows(
        env.subject, "tool_calls", run_id=receipt["run_id"]
    )
    record = {
        "example": name,
        "channel_id": value["channel_id"],
        "run_id": value["run_id"],
        "state": value["state"],
        "result": value["result"],
        "error": value["error"],
        "release_snapshot_id": value["release_snapshot_id"],
        "usage_summary": value["usage_summary"],
        "http": env.http_records,
        "source_calls": [
            {k: c[k] for k in ("id", "run_id", "source_request_id", "state")} for c in calls
        ],
        "verification": {
            "api": "local_tcp",
            "mcp": "local_tcp" if calls else "unused",
            "model": "controlled_fixture",
            "database": "PostgreSQL",
            "auth": "Redis",
            "worker": "execute_message_in_process",
            "object_store": "memory_fixture",
            "test_scope_retained": False,
            "production_released": False,
        },
    }
    if folder := os.environ.get("CREATIVITY_API_EVIDENCE_DIR"):
        path = Path(folder)
        await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
        (path / f"{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")


async def test_same_client_two_configurations_sync_async_stream_and_cancel(delivery_env):
    env = delivery_env
    for name in env.configurations:
        first = await submit(env, name, name + "-sync", "sync")
        assert env.http_records[-1]["status"] == 202
        assert first["state"] == "QUEUED"
        assert (await submit(env, name, name + "-sync", "sync"))["run_id"] == first["run_id"]
        await finish(env, first, name)
        result = await env.backend.wait(first["run_id"], wait_seconds=10)
        assert result["state"] == "SUCCEEDED", result["error"]
        assert result["result"]["data"] == case(name)["fixture_output"]["data"]
        again = await submit(env, name, name + "-sync", "sync")
        assert again["run_id"] == first["run_id"] and again["result"] == result["result"]
        assert env.http_records[-1]["status"] == 200
        assert (await env.backend.cancel(first["run_id"]))["state"] == "SUCCEEDED"
        await evidence_record(env, name, first)

        queued = await submit(env, name, name + "-async")
        assert (await env.backend.cancel(queued["run_id"]))["state"] == "CANCELLED"
        await finish(env, queued, name)
        assert (await env.backend.query(queued["run_id"]))["result"] is None

        stream = await submit(env, name, name + "-stream", "stream")
        subscription = env.backend.subscribe(stream["run_id"])
        accepted = await anext(subscription)
        assert accepted.type == "accepted" and accepted.sequence == 1
        await subscription.aclose()
        await finish(env, stream, name)
        events = [e async for e in env.backend.subscribe(stream["run_id"], accepted.sequence)]
        assert events[0].sequence == 2
        assert len([e for e in events if e.type == "result"]) == 1
        assert events[-1].type == "completed"
        text = [e for e in events if e.type == "text_delta"]
        assert text and all(e.data["payload"]["validated"] is False for e in text)
        assert [e.sequence for e in events] == sorted({e.sequence for e in events})
        async with env.engine.connect() as conn:
            records = await rows(
                conn, "runs", env.context.scope.channel_id, agent_id=env.agent_ids[name]
            )
        assert len([r for r in records if r["purpose"] == "production"]) == 3
    assert len([c for c in env.sources[0].calls if c["name"] == "archive.find-notes"]) == 2


async def test_reauthentication_key_rotation_and_current_revocation(delivery_env):
    env, name = delivery_env, "archive-answer"
    receipt = await submit(env, name, "rotation")
    old_token = env.backend.token
    context = await env.iam.authentication.authenticate(old_token, "service")
    await env.redis.delete(env.iam.authentication.tokens.token_key(context.token_digest))
    # 服务 Token 自然失效不取消已受理任务；Worker 使用保存身份重新复核。
    await finish(env, receipt, name)
    assert (await env.backend.query(receipt["run_id"]))["state"] == "SUCCEEDED"
    assert env.backend.token != old_token
    rotated = await env.services.keys.rotate(
        env.tenant.manager,
        env.context.scope.channel_id,
        env.identity.key.key.key_id,
        KeyRotate(
            revision=env.identity.key.key.revision,
            overlap_seconds=60,
            expires_at=utcnow() + timedelta(hours=1),
        ),
    )
    env.backend.api_key, env.backend.token = rotated.api_key, None
    assert (await submit(env, name, "rotation"))["run_id"] == receipt["run_id"]
    assert (await env.backend.query(receipt["run_id"]))["state"] == "SUCCEEDED"
    env.sources[0].review_mode = "revoked"
    for operation in (
        lambda: env.backend.query(receipt["run_id"]),
        lambda: submit(env, name, "rotation"),
    ):
        with pytest.raises(PlatformError) as failure:
            await operation()
        assert failure.value.status == 403
    with pytest.raises(PlatformError):
        _ = [e async for e in env.backend.subscribe(receipt["run_id"])]
    env.sources[0].review_mode = "normal"
    await env.services.keys.revoke(
        env.tenant.manager, env.context.scope.channel_id, rotated.key.key_id, rotated.key.revision
    )
    with pytest.raises(PlatformError):
        await env.backend.query(receipt["run_id"])


async def test_conversation_artifact_expired_events_and_scope_rejection(delivery_env):
    env = delivery_env
    backend = env.backend
    conversation = await backend.create_conversation(
        env.configurations["text-brief"].agent_code, "通用会话"
    )
    message = await backend.send_message(
        conversation["conversation_id"], "message-one", "整理资料", case("text-brief")["input"]
    )
    repeated = await backend.send_message(
        conversation["conversation_id"], "message-one", "整理资料", case("text-brief")["input"]
    )
    assert repeated["run"]["run_id"] == message["run"]["run_id"]
    await finish(env, message["run"], "text-brief")
    exported = await backend.request(
        "POST", f"/api/v1/conversations/{conversation['conversation_id']}/exports"
    )
    assert await backend.download(exported["artifact_id"])
    run_id = message["run"]["run_id"]
    target = f"/api/v1/runs/{run_id}/events"
    env.sources[0].review_mode = "revoked"
    with pytest.raises(PlatformError):
        await backend.download(exported["artifact_id"])
    env.sources[0].review_mode = "normal"
    headers = await backend.prepare("GET", target, b"", None)
    original = headers["X-Business-Delegation"]
    kid, payload, signature = split_envelope(original)
    claims = verify_payload(kid, payload, signature, backend.signing_secret)
    from examples.backend.client import sign_claims

    for changed in (
        {"channel_id": "forged"},
        {"environment": "prod"},
        {"subject_id": "another-user"},
        {"data_scope": {"type": "workspace", "id": "other"}},
    ):
        headers["X-Business-Delegation"] = sign_claims(
            {
                **claims.model_dump(mode="json", exclude_none=True),
                **changed,
                "nonce": secrets.token_urlsafe(24),
            },
            kid,
            backend.signing_secret,
        )
        response = await backend.http.get(target, headers=headers)
        assert response.status_code in {401, 403, 404, 409}
    headers["Authorization"] = "Bearer " + backend.api_key
    headers["X-Business-Delegation"] = original
    assert (await backend.http.get(target, headers=headers)).status_code == 401
    async with transaction(
        env.engine, env.subject.scope, env.runs.keys(env.subject, run_id)
    ) as uow:
        for event in await rows(
            uow.connection, "run_events", env.context.scope.channel_id, run_id=run_id
        ):
            await save(uow, "run_events", event["id"], {"expires_at": utcnow() - timedelta(days=1)})
    events = [e async for e in backend.subscribe(run_id)]
    assert len(events) == 1 and events[0].type == "snapshot"
    assert events[0].data["state"] == "SUCCEEDED"


async def test_sync_window_input_contract_and_live_stream_revocation(delivery_env, monkeypatch):
    env, name = delivery_env, "text-brief"
    # 用通用对象组装配置验证窗口内完成，不受模型或远端调用时长影响。
    base = env.configurations[name]
    step = base.definition.steps[0].model_copy(
        update={
            "kind": "compute",
            "operator": "object",
            "input_schema": base.definition.output_schema,
            "inputs": {
                k: InputSource(source="constant", value=v)
                for k, v in case(name)["fixture_output"].items()
            },
        }
    )
    body = base.model_copy(
        update={
            "agent_code": "window_contract",
            "definition": base.definition.model_copy(
                update={"steps": (step,), "workflow_type": "template", "entrypoint": "workflow.v1"}
            ),
        }
    )
    detail = await env.agents.create(env.context, body)
    await validate_agent(env, detail)
    await publish(env, detail)
    env.configurations[name] = body
    app = env.client._transport.app
    app.state.sync_wait_seconds = 15
    admissions = asyncio.Queue()
    original = env.runs.admit_run

    async def notify(context, body, key, **kwargs):
        receipt = await original(context, body, key, **kwargs)
        admissions.put_nowait(receipt.model_dump(mode="json"))
        return receipt

    async def worker():
        await finish(env, await admissions.get(), name)

    monkeypatch.setattr(env.runs, "admit_run", notify)
    async with asyncio.TaskGroup() as group:
        group.create_task(worker())
        response = await submit(env, name, "sync-window", "sync")
    monkeypatch.setattr(env.runs, "admit_run", original)
    assert env.http_records[-1]["status"] == 200
    assert response["state"] == "SUCCEEDED"
    for key, expected in [
        ("invalid", "INPUT_SCHEMA_INVALID"),
        ("sync-window", "IDEMPOTENCY_CONFLICT"),
    ]:
        with pytest.raises(PlatformError) as failure:
            await env.backend.submit(env.configurations[name].agent_code, {"unconfigured": 1}, key)
        assert failure.value.code == expected
    receipt = await submit(env, name, "live-revoke", "stream")
    subscription = env.backend.subscribe(receipt["run_id"])
    try:
        assert (await anext(subscription)).type == "accepted"
        env.sources[0].review_mode = "revoked"
        # 连接已建立后，空闲轮询仍须拒绝当前已撤销的主体。
        with pytest.raises(PlatformError) as failure:
            async with asyncio.timeout(10):
                await anext(subscription)
        assert failure.value.status == 403
    finally:
        await subscription.aclose()
        env.sources[0].review_mode = "normal"
    assert (await env.backend.cancel(receipt["run_id"]))["state"] == "CANCELLED"
