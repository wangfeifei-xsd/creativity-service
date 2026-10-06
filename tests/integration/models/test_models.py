"""模型配置的渠道隔离、并发、版本失效和统一测试端口集成验证。"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import SecretBytes

from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.deletion import CleanupRegistry, ContentRef, DeletionService
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.modules.channels.schemas import EnvironmentCreate
from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.models.assembly import build_model_services
from creativity_service.modules.models.repositories import repository
from creativity_service.modules.models.schemas import (
    CaseResult,
    ConnectionInput,
    CredentialInput,
    ModelInput,
    ProviderInput,
    ReleaseInput,
    RouteInput,
    RouteVersionInput,
)
from creativity_service.modules.models.schemas import (
    TestCompletion as Completion,
)
from creativity_service.modules.models.schemas import (
    TestInput as InputCases,
)
from tests.integration.channels.conftest import channel_body, login, provision

pytestmark = pytest.mark.integration


async def test_provider_code_is_server_generated_and_stable_when_renamed(channel_env):
    env = channel_env
    tenant, services, *_ = await setup(env)
    config = services.configuration
    provider = await config.save_provider(
        env.admin,
        ProviderInput(name="云", code="client-must-not-choose", protocols=["chat_completions"]),
    )
    assert provider.code == "YYYY" and provider.id == "provider_YYYY"
    renamed = await config.save_provider(
        env.admin,
        ProviderInput(
            id=provider.id,
            name="云端模型",
            protocols=["chat_completions"],
            revision=provider.revision,
        ),
    )
    assert renamed.id == provider.id and renamed.code == provider.code
    assert renamed.name == "云端模型" and renamed.revision == provider.revision + 1
    with pytest.raises(ServiceError) as collision:
        await config.save_provider(
            env.admin,
            ProviderInput(name="云", protocols=["chat_completions"]),
        )
    assert collision.value.status == 409
    with pytest.raises(ServiceError) as forbidden:
        await config.save_provider(
            tenant.manager,
            ProviderInput(name="不允许", protocols=["chat_completions"]),
        )
    assert forbidden.value.status == 403


async def test_provider_initial_collision_is_serialized(channel_env):
    env = channel_env
    _, services, *_ = await setup(env)
    body = ProviderInput(name="AI", protocols=["chat_completions"])
    results = await asyncio.gather(
        services.configuration.save_provider(env.admin, body),
        services.configuration.save_provider(env.admin, body),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert [result.status for result in results if isinstance(result, ServiceError)] == [409]


class Keys:
    async def current(self):
        return "test-v1", SecretBytes(b"m" * 32)

    async def resolve(self, version):
        return SecretBytes(b"m" * 32)


class Executor:
    def __init__(self):
        self.submissions = []

    async def submit(self, context, execution):
        assert_external_io_allowed()
        self.submissions.append(execution)
        return "run_" + execution.test_id


async def setup(env, publish=False, configuration_only=False):
    if configuration_only:
        from creativity_service.modules.channels.schemas import ChannelCreate

        channel = await env.services.channels.create(
            env.admin,
            ChannelCreate(
                name="模型配置渠道",
                owner="负责人",
                first_admin_user_id=env.user_id,
                independent_actions=["release:publish"] if publish else [],
            ),
        )
        await env.services.channels.create_environment(
            env.admin, channel.channel_id, EnvironmentCreate(environment="test", name="测试")
        )
        _, admin = await login(env)
        response = await env.iam.sessions.enter(
            admin,
            ChannelContextInput(
                channel_id=channel.channel_id,
                environment="test",
            ),
        )
        manager = await env.iam.authentication.admin_session(response.access_token, "models-config")
        tenant = SimpleNamespace(channel=channel, token=response, manager=manager)
    elif publish:
        channel = await env.services.channels.create(
            env.admin,
            channel_body(env).model_copy(update={"independent_actions": ["release:publish"]}),
        )
        _, admin = await login(env)
        response = await env.iam.sessions.enter(
            admin,
            ChannelContextInput(
                channel_id=channel.channel_id,
                environment="test",
            ),
        )
        manager = await env.iam.authentication.admin_session(
            response.access_token, "models-request", governance=True
        )
        tenant = SimpleNamespace(channel=channel, token=response, manager=manager)
    else:
        tenant = await provision(env)

    async def resolve(host, port):
        assert_external_io_allowed()
        return ["93.184.216.34"]

    policy = OutboundPolicy(
        (Destination(tenant.channel.channel_id, "test", "model", "models.example"),), resolve
    )
    env.model_cleanup = CleanupRegistry()
    services = build_model_services(
        env.engine, env.iam, key_provider=Keys(), outbound=policy, cleanup=env.model_cleanup
    )
    env.client._transport.app.state.models = services
    config = services.configuration
    provider = await config.save_provider(
        env.admin,
        ProviderInput(
            code="fixture", name="协议夹具", protocols=["chat_completions", "anthropic_messages"]
        ),
    )
    credential = await config.store_credential(
        tenant.manager, CredentialInput(secret="never-return-this-secret")
    )
    connection_input = ConnectionInput(
        name="测试连接",
        provider_id=provider.id,
        protocol="chat_completions",
        endpoint="https://models.example/v1",
        credential_ref=credential,
    )
    connection = await config.save_connection(tenant.manager, connection_input)
    model_input = ModelInput(
        model_code="stable-alias",
        name="测试模型",
        connection_id=connection.id,
        provider_model_name="provider-v1",
        parameters={"max_tokens": 100},
    )
    model = await config.save_model(tenant.manager, model_input)
    return tenant, services, connection_input, connection, model_input, model


async def complete(services, tenant, model, *, evidence="live"):
    executor = Executor()
    services.configuration.executor = executor
    test = await services.testing.create(
        tenant.manager, model.id, InputCases(cases=["text", "tools", "usage"])
    )
    completion = Completion(
        run_id=test.run_id,
        config_digest=test.config_digest,
        results=[
            CaseResult(case=c, passed=True, attempt_ids=["attempt_" + c])
            for c in ["text", "tools", "usage"]
        ],
        latency_ms=20,
        evidence=evidence,
    )
    result = await services.testing.complete(tenant.manager.context, test.id, completion)
    return result, executor


async def test_config_history_stale_capabilities_and_no_test_bypass(channel_env):
    tenant, services, connection_input, connection, model_input, model = await setup(channel_env)
    blocked = await services.testing.create(tenant.manager, model.id, InputCases())
    assert blocked.state == "BLOCKED" and blocked.attempt_ids == []
    assert not any(c.state == "SUPPORTED" for c in model.capabilities)
    fixture, _ = await complete(services, tenant, model, evidence="fixture")
    assert fixture.state == "FIXTURE"
    assert not any(
        c.state == "SUPPORTED"
        for c in (await services.configuration.detail(tenant.manager, model.id)).capabilities
    )
    live, executor = await complete(services, tenant, model)
    assert live.state == "PASSED" and len(live.attempt_ids) == 3
    assert executor.submissions[0].configuration.provider_model_name == "provider-v1"
    verified = await services.configuration.detail(tenant.manager, model.id)
    assert next(c for c in verified.capabilities if c.capability == "tools").state == "SUPPORTED"
    current_connection = (await services.configuration.connections(tenant.manager)).items[0]
    await services.configuration.save_connection(
        tenant.manager,
        connection_input.model_copy(
            update={
                "endpoint": "https://models.example/v2",
                "revision": current_connection.revision,
            }
        ),
        connection.id,
    )
    changed = await services.configuration.detail(tenant.manager, model.id)
    assert all(c.state == "UNVERIFIED" for c in changed.capabilities)
    route = await services.routing.create(tenant.manager, RouteInput(code="route", name="业务路由"))
    version = await services.routing.create_version(
        tenant.manager,
        route.id,
        RouteVersionInput(label="v1", primary_model=model.id, required_capabilities=["tools"]),
    )
    with pytest.raises(ServiceError, match="尚未通过"):
        await services.routing.resolve_route(tenant.manager.context, version.version_id)
    versions = await services.configuration.history(
        tenant.manager, "model_connection", connection.id
    )
    assert len(versions) == 2 and versions[0].content["endpoint"] == "https://models.example/v1"
    assert "never-return-this-secret" not in str(versions)
    old = await services.testing.get(tenant.manager, live.id)
    assert old.attempt_ids == live.attempt_ids


async def test_tenant_isolation_endpoint_policy_and_http_contract(channel_env):
    tenant, services, connection_input, connection, model_input, model = await setup(channel_env)
    other = await provision(channel_env, "other", "club")
    with pytest.raises(ServiceError) as exc:
        await services.configuration.detail(other.manager, model.id)
    assert exc.value.status == 404
    with pytest.raises(ServiceError, match="HTTPS"):
        await services.configuration.save_connection(
            tenant.manager, connection_input.model_copy(update={"endpoint": "http://127.0.0.1/v1"})
        )
    response = await channel_env.client.get(
        "/admin/v1/models", headers={"Authorization": "Bearer " + tenant.token.access_token}
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["name"] == "测试模型"
    response = await channel_env.client.post(
        "/admin/v1/models",
        json={**model_input.model_dump(), "channel_id": other.channel.channel_id},
        headers={"Authorization": "Bearer " + tenant.token.access_token},
    )
    assert response.status_code == 422


async def test_concurrent_model_alias_and_immutable_mapping(channel_env):
    tenant, services, _, _, model_input, model = await setup(channel_env)
    duplicate = model_input.model_copy(update={"model_code": "race-alias"})
    results = await asyncio.gather(
        *(services.configuration.save_model(tenant.manager, duplicate) for _ in range(2)),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ServiceError) and r.code == "CODE_EXISTS" for r in results) == 1
    updated = await services.configuration.save_model(
        tenant.manager,
        model_input.model_copy(
            update={"provider_model_name": "provider-v2", "revision": model.revision}
        ),
        model.id,
    )
    versions = await services.configuration.history(tenant.manager, "model", model.id)
    assert len(versions) == 2 and versions[0].content["provider_model_name"] == "provider-v1"
    assert updated.current_version_id != model.current_version_id
    with pytest.raises(ServiceError, match="稳定别名"):
        await services.configuration.save_model(
            tenant.manager,
            model_input.model_copy(update={"model_code": "changed", "revision": updated.revision}),
            model.id,
        )


async def test_configuration_history_isolated_by_environment(channel_env):
    tenant, services, _, connection, _, model = await setup(channel_env)
    test = await services.testing.create(tenant.manager, model.id, InputCases())
    await channel_env.services.channels.create_environment(
        channel_env.admin,
        tenant.channel.channel_id,
        EnvironmentCreate(environment="prod", name="生产环境"),
    )
    _, admin = await login(channel_env)
    token = await channel_env.iam.sessions.enter(
        admin,
        ChannelContextInput(
            channel_id=tenant.channel.channel_id,
            environment="prod",
        ),
    )
    headers = {"Authorization": "Bearer " + token.access_token}
    for path in (
        f"/models/{model.id}",
        f"/models/{model.id}/versions",
        f"/model-connections/{connection.id}/versions",
        f"/model-tests/{test.id}",
    ):
        response = await channel_env.client.get("/admin/v1" + path, headers=headers)
        assert response.status_code == 404, response.text
    response = await channel_env.client.get("/admin/v1/models", headers=headers)
    assert response.status_code == 200 and response.json()["items"] == []


async def test_revoked_connection_blocks_attempt_but_keeps_history(channel_env):
    tenant, services, connection_input, connection, _, model = await setup(channel_env)
    _, executor = await complete(services, tenant, model)
    frozen = executor.submissions[0].configuration
    prepared = await services.routing.prepare_attempt(tenant.manager.context, frozen, ["text"])
    assert prepared.provider_credential_id == connection.credential_ref
    forged = frozen.model_copy(
        update={"endpoint": "https://untrusted.example", "allowed_networks": ["10.0.0.0/8"]}
    )
    trusted = await services.routing.prepare_attempt(tenant.manager.context, forged, ["text"])
    assert trusted.endpoint == connection.endpoint and trusted.allowed_networks == []
    current = (await services.configuration.connections(tenant.manager)).items[0]
    await services.configuration.save_connection(
        tenant.manager,
        connection_input.model_copy(update={"revision": current.revision, "status": "DISABLED"}),
        connection.id,
    )
    with pytest.raises(ServiceError, match="停用"):
        await services.routing.prepare_attempt(tenant.manager.context, frozen, ["text"])
    assert await services.configuration.history(tenant.manager, "model", model.id)


async def test_route_release_hard_price_gate_and_stale_revision(channel_env):
    tenant, services, _, _, model_input, model = await setup(channel_env, publish=True)
    await complete(services, tenant, model)
    route = await services.routing.create(
        tenant.manager, RouteInput(code="release", name="发布路由")
    )
    version = await services.routing.create_version(
        tenant.manager,
        route.id,
        RouteVersionInput(
            label="v1",
            primary_model=model.id,
            required_capabilities=["tools"],
            parameters={"max_tokens": 80},
        ),
    )
    assert version.content["models"][0]["parameters"]["max_tokens"] == 80
    published = await services.routing.release(
        tenant.manager, route.id, ReleaseInput(version_id=version.version_id)
    )
    assert published.version_id == version.version_id
    with pytest.raises(ServiceError) as conflict:
        await services.routing.release(
            tenant.manager, route.id, ReleaseInput(version_id=version.version_id)
        )
    assert conflict.value.code == "REVISION_CONFLICT"
    hard = await services.routing.create_version(
        tenant.manager,
        route.id,
        RouteVersionInput(label="hard", primary_model=model.id, hard_amount_budget=True),
    )
    with pytest.raises(ServiceError) as missing:
        await services.routing.release(
            tenant.manager,
            route.id,
            ReleaseInput(version_id=hard.version_id, expected_version_id=version.version_id),
        )
    assert missing.value.code == "DEPENDENCY_UNAVAILABLE"
    current = await services.configuration.detail(tenant.manager, model.id)
    await services.configuration.save_model(
        tenant.manager,
        model_input.model_copy(
            update={"provider_model_name": "new-model", "revision": current.revision}
        ),
        model.id,
    )
    with pytest.raises(ServiceError) as stale:
        await services.routing.release(
            tenant.manager,
            route.id,
            ReleaseInput(version_id=version.version_id, expected_version_id=version.version_id),
        )
    assert stale.value.code == "MODEL_CONFIGURATION_STALE"


async def test_restoring_endpoint_does_not_revive_old_evidence(channel_env):
    tenant, services, original, connection, _, model = await setup(channel_env)
    await complete(services, tenant, model)
    current = (await services.configuration.connections(tenant.manager)).items[0]
    changed = await services.configuration.save_connection(
        tenant.manager,
        original.model_copy(
            update={"endpoint": "https://models.example/other", "revision": current.revision}
        ),
        connection.id,
    )
    await services.configuration.save_connection(
        tenant.manager, original.model_copy(update={"revision": changed.revision}), connection.id
    )
    actual = await services.configuration.detail(tenant.manager, model.id)
    assert all(c.state == "UNVERIFIED" for c in actual.capabilities)


async def test_model_use_grant_revocation_is_immediate(channel_env):
    from creativity_service.modules.iam.schemas import MembershipInput
    from creativity_service.modules.models.schemas import ModelGrantInput
    from tests.integration.iam.conftest import create_user, enter

    tenant, services, _, _, _, model = await setup(channel_env)
    _, executor = await complete(services, tenant, model)
    account, (_, user) = await create_user(channel_env.iam, channel_env.admin)
    await channel_env.iam.access.put_member(
        tenant.manager,
        tenant.channel.channel_id,
        account.user_id,
        MembershipInput(roles=["builder"], environments=["test"]),
    )
    grant = await services.configuration.grant(
        tenant.manager,
        tenant.channel.channel_id,
        model.id,
        ModelGrantInput(grantee_type="account", grantee_id=account.user_id),
    )
    _, user = await enter(channel_env.iam, user, tenant.channel.channel_id)
    frozen = executor.submissions[0].configuration
    await services.routing.prepare_attempt(user.context, frozen, ["text"])
    await channel_env.iam.access.revoke_grant(
        tenant.manager, tenant.channel.channel_id, grant.grant_id, grant.revision
    )
    with pytest.raises(ServiceError) as denied:
        await services.routing.prepare_attempt(user.context, frozen, ["text"])
    assert denied.value.status == 403


async def test_deletion_propagates_to_tests_versions_and_cleanup(channel_env):
    tenant, services, _, _, _, model = await setup(channel_env)
    test = await services.testing.create(tenant.manager, model.id, InputCases())

    class DeleteFixtureAuthorization:
        async def require(self, context, action, identifier):
            assert context.scope.channel_id == tenant.channel.channel_id

    await DeletionService(channel_env.engine, DeleteFixtureAuthorization()).mark(
        tenant.manager.context, ContentRef("model", model.id), "USER_REQUEST"
    )
    with pytest.raises(ServiceError) as deleted:
        await services.testing.get(tenant.manager, test.id)
    assert deleted.value.code == "CONTENT_DELETED"
    with pytest.raises(ServiceError):
        await services.configuration.history(tenant.manager, "model", model.id)
    assert (await services.configuration.models(tenant.manager)).items == []
    await channel_env.model_cleanup.clean(tenant.manager.context, ContentRef("model_test", test.id))
    async with channel_env.engine.connect() as connection:
        row = await repository(tenant.manager.context.scope, "model_tests").get(connection, test.id)
    assert row["execution"] == {} and row["results"] == []


async def test_full_migration_schema_matches_registered_storage(channel_env):
    from creativity_service.core.database.audit import audit_database

    await setup(channel_env)
    async with channel_env.engine.connect() as connection:
        failures = await connection.run_sync(lambda conn: audit_database(conn, channel_env.schema))
    assert failures == []


async def test_connection_page_authorizes_destination_and_freezes_networks(
    channel_env, monkeypatch
):
    from creativity_service.modules.models import outbound

    tenant, services, body, connection, _, model = await setup(channel_env)
    _, executor = await complete(services, tenant, model)
    frozen = executor.submissions[0].configuration
    calls = []

    async def resolve(host, port):
        assert_external_io_allowed()
        calls.append((host, port))
        return ["93.184.216.34"]

    # 使用正式的连接授权路径，不注入服务器目的地白名单。
    services.configuration.outbound = None
    monkeypatch.setattr(outbound, "OutboundPolicy", lambda rules: OutboundPolicy(rules, resolve))
    current = (await services.configuration.connections(tenant.manager)).items[0]
    updated = await services.configuration.save_connection(
        tenant.manager,
        body.model_copy(
            update={
                "allowed_networks": ["93.184.216.34/32"],
                "revision": current.revision,
            }
        ),
        connection.id,
    )
    assert updated.allowed_networks == ["93.184.216.34/32"]
    assert calls == [("models.example", 443)]
    changed = await services.configuration.detail(tenant.manager, model.id)
    assert all(c.state == "UNVERIFIED" for c in changed.capabilities)
    with pytest.raises(ServiceError, match="模型配置已变化"):
        await services.routing.prepare_attempt(tenant.manager.context, frozen, ["text"])
    history = await services.configuration.history(
        tenant.manager, "model_connection", connection.id
    )
    assert history[-1].content["allowed_networks"] == ["93.184.216.34/32"]
    # 同一凭据可以由有权管理员配置新的公网连接，不依赖额外服务器登记。
    added = await services.configuration.save_connection(
        tenant.manager, body.model_copy(update={"endpoint": "https://another.example:8443/v1"})
    )
    assert added.endpoint == "https://another.example:8443/v1" and added.allowed_networks == []
    assert calls[-1] == ("another.example", 8443)
