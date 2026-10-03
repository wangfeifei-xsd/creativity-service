"""管理员订阅真实业务 API 运行，验证通知、告警与发送前的权限边界。"""

import base64
import json
from uuid import uuid4

import pytest
from sqlalchemy import insert

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionService
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.integrations.business.delegation import bind_request, sign
from creativity_service.modules.channels.schemas import ClientCreate, ClientUpdate
from creativity_service.modules.iam.repositories import policy_key, rows, save
from creativity_service.modules.integrations.alerts import Alerts, AlertSave
from creativity_service.modules.integrations.automation_schemas import WebhookCreate, WebhookUpdate
from creativity_service.modules.runs.tables import metadata
from creativity_service.workers.executor import execute_message
from tests.integration.agents.test_agents import publish
from tests.integration.automation.test_webhooks import configured
from tests.integration.mcp.test_business_access import business_env as business_env
from tests.integration.runtime.test_execution import pytestmark as pytestmark


async def request(env, method, path, body=b"", key=None):
    now = int(utcnow().timestamp())
    claims = env.claims.model_copy(
        update={
            "issued_at": now,
            "expires_at": now + 300,
            "nonce": uuid4().hex,
            "request": bind_request(method, path, body, key),
        }
    )
    return await env.client.request(
        method,
        path,
        content=body,
        headers={
            "Authorization": "Bearer " + env.identity.token.access_token,
            "X-Business-Delegation": sign(
                claims, env.key.key.kid, base64.b64decode(env.key.signing_secret)
            ),
            "Content-Type": "application/json",
            **({"Idempotency-Key": key} if key else {}),
        },
    )


async def prepare(env):
    detail = await env.agents.create(env.context, env.body)
    await publish(env, detail)
    hooks, endpoint, receiver = await configured(
        env,
        client_ids=[env.identity.client.client_id],
        events=("run.terminal", "alert.triggered", "alert.resolved"),
    )
    env.runs.webhooks = hooks
    receiver.status = 200
    alerts = Alerts(env.runs.automation, hooks)
    rule = await alerts.save(
        env.context,
        AlertSave(
            name="业务失败",
            kind="run_failure",
            endpoint_id=endpoint["id"],
            client_ids=[env.identity.client.client_id],
        ),
    )
    return hooks, endpoint, receiver, alerts, rule


async def run(env, *, fail=False):
    body = json.dumps(
        {
            "agent_code": env.body.agent_code,
            "input": {"request": "业务 API 通知验证"},
            "delivery": "async",
        }
    ).encode()
    response = await request(env, "POST", "/api/v1/runs", body, uuid4().hex)
    assert response.status_code == 202, response.text
    message = TaskEnvelope(
        channel_id=env.context.scope.channel_id, run_id=response.json()["run_id"]
    )
    env.adapter.failures = ["MODEL_UNAVAILABLE"] * 10 if fail else []
    await execute_message(env.runs, message, "subscription-worker", env.runtime)
    row = await env.runs.load(message)
    assert row["state"] == ("FAILED" if fail else "SUCCEEDED"), row["error"]
    return row


async def test_business_api_terminal_notifications_and_failure_alerts(business_env):
    env = business_env
    hooks, endpoint, receiver, alerts, rule = await prepare(env)
    legacy = await hooks.create(
        env.context,
        WebhookCreate(
            name="本人通知",
            url=endpoint["url"],
            secret="s" * 32,
            events=("run.terminal", "alert.triggered", "alert.resolved"),
        ),
    )
    own = await alerts.save(
        env.context,
        AlertSave(
            name="本人失败",
            kind="run_failure",
            endpoint_id=legacy["id"],
        ),
    )
    success, failed = await run(env), await run(env, fail=True)
    # 边界夹具只登记运行元数据，验证其他渠道、环境、数据域和服务不能进入扫描范围。
    for key, value in [
        ("channel_id", "unrelated_channel"),
        ("environment", "prod"),
        ("data_scope_id", "unrelated_domain"),
        ("client_id", "unrelated_client"),
    ]:
        foreign = {**failed, key: value, "id": "foreign_" + key}
        async with transaction(
            env.engine, env.context.scope, env.runs.keys(env.context, foreign["id"])
        ) as uow:
            await uow.connection.execute(insert(metadata.tables["runs"]).values(**foreign))
    await alerts.sweep(env.context.scope.channel_id)
    await hooks.sweep(env.context.scope.channel_id)
    await alerts.sweep(env.context.scope.channel_id)
    await hooks.sweep(env.context.scope.channel_id)
    events = [json.loads(body) for body, _ in receiver.calls]
    terminal = [event for event in events if event["type"] == "run.terminal"]
    assert {event["run_id"] for event in terminal} == {success["id"], failed["id"]}
    assert len(terminal) == 2 and len(events) == 3
    for event in terminal:
        assert event["status_path"] == "/api/v1/runs/" + event["run_id"]
        response = await request(env, "GET", event["status_path"])
        assert response.status_code == 200, response.text
        assert response.json()["state"] == event["state"]
    alert = next(event for event in events if event["type"] == "alert.triggered")
    assert alert["alert_rule_id"] == rule["id"] and alert["value"] == 1
    assert alert["client_ids"] == [env.identity.client.client_id]
    rules = {value["id"]: value for value in await alerts.list(env.context)}
    assert rules[own["id"]]["last_value"] == 0
    assert legacy["client_ids"] == []


async def test_subscription_changes_reject_pending_events_and_validate_clients(business_env):
    env = business_env
    hooks, endpoint, receiver, alerts, rule = await prepare(env)
    client_id = env.identity.client.client_id
    for ids in [["other_channel_client"], [client_id, client_id]]:
        with pytest.raises(ServiceError) as error:
            await hooks.toggle(
                env.context,
                endpoint["id"],
                WebhookUpdate(
                    revision=endpoint["revision"],
                    active=True,
                    client_ids=ids,
                ),
            )
        assert error.value.code == "SUBSCRIPTION_INVALID"
    other = await env.services.channels.create_client(
        env.tenant.manager,
        env.context.scope.channel_id,
        ClientCreate(
            name="另一调用服务",
            environment="test",
            scopes=["run:create", "run:read"],
            data_scopes=[env.context.scope.data_scope_id],
        ),
    )
    failed = await run(env, fail=True)
    stored = await hooks.automation.get(env.context, "webhook_endpoints", endpoint["id"])
    await hooks.terminal_events(stored)
    await alerts.sweep(env.context.scope.channel_id)
    updated = await env.client.patch(
        "/admin/v1/webhooks/" + endpoint["id"],
        json={
            "revision": endpoint["revision"],
            "active": True,
            "client_ids": [other.client_id],
        },
    )
    assert updated.status_code == 200, updated.text
    latest = (await alerts.list(env.context))[0]
    await alerts.save(
        env.context,
        AlertSave(
            name=rule["name"],
            kind="run_failure",
            endpoint_id=endpoint["id"],
            revision=latest["revision"],
            client_ids=[other.client_id],
        ),
        rule["id"],
    )
    await hooks.sweep(env.context.scope.channel_id)
    assert receiver.calls == []
    deliveries = await hooks.deliveries(env.context)
    assert len(deliveries) == 2 and all(row["state"] == "CANCELLED" for row in deliveries)
    assert {row["error"]["code"] for row in deliveries} == {"FORBIDDEN", "ALERT_SCOPE_CHANGED"}
    options = await env.client.get("/admin/v1/run-subscription-options")
    assert options.status_code == 200
    assert {row["client_id"] for row in options.json()} == {client_id, other.client_id}
    assert failed["client_id"] == client_id


@pytest.mark.parametrize("block", ["permission", "deleted", "client"])
async def test_queued_business_notifications_recheck_access_and_deletion(business_env, block):
    env = business_env
    hooks, endpoint, receiver, alerts, rule = await prepare(env)
    failed = await run(env, fail=True)
    stored = await hooks.automation.get(env.context, "webhook_endpoints", endpoint["id"])
    await hooks.terminal_events(stored)
    if block in {"permission", "client"}:
        await alerts.sweep(env.context.scope.channel_id)
    if block == "permission":
        async with env.engine.connect() as connection:
            grants = await rows(connection, "resource_grants", env.context.scope.channel_id)
        locks = [
            policy_key(env.context.scope.channel_id),
            *[
                record_key(env.context.scope.channel_id, "resource_grants", grant["id"])
                for grant in grants
            ],
        ]
        async with transaction(env.engine, env.context.scope, locks) as uow:
            for grant in grants:
                await save(
                    uow,
                    "resource_grants",
                    grant["id"],
                    {
                        "allowed_actions": [a for a in grant["allowed_actions"] if a != "run:read"],
                    },
                    grant["revision"],
                )
    elif block == "deleted":
        context = env.context.model_copy(update={"scope": env.subject.scope})
        await DeletionService(env.engine, env.iam.authorization).mark(
            context,
            ContentRef("run", failed["id"]),
            "USER_REQUEST",
        )
        assert await alerts.count(env.context, rule) == 0
    else:
        await env.services.channels.update_client(
            env.tenant.manager,
            env.context.scope.channel_id,
            env.identity.client.client_id,
            ClientUpdate(revision=env.identity.client.revision, status="DISABLED"),
        )
        await alerts.sweep(env.context.scope.channel_id)
        assert (await alerts.list(env.context))[0]["last_value"] == 1
    await hooks.sweep(env.context.scope.channel_id)
    assert receiver.calls == []
