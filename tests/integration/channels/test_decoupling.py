"""19：任意数据域的开通、精确委托映射、并发隔离与旧配置兼容。"""

import asyncio
import base64
from datetime import timedelta
from types import SimpleNamespace

import pytest

from creativity_service.core.primitives import ServiceError, new_id, utcnow
from creativity_service.integrations.business.delegation import DelegationClaims, bind_request, sign
from creativity_service.modules.channels.schemas import (
    ChannelView,
    DataScopeCreate,
    DataScopeUpdate,
    EnvironmentCreate,
)
from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.integrations.assembly import build_integration_services
from creativity_service.modules.integrations.schemas import DelegationKeyCreate
from tests.integration.integrations.conftest import TestKeys

from .conftest import credential, login, provision

pytestmark = pytest.mark.integration


async def open_workspace(env, code="research"):
    body = {
        "name": "资料工作区" if code == "research" else f"{code}工作区",
        "owner": "资料管理员",
        "first_admin_user_id": env.user_id,
        "environment": "test",
        "data_scope": {
            "name": "研发资料",
            "external_scope_type": "源系统/workspace",
            "external_scope_id": "研发:001/甲",
        },
    }
    response = await env.client.post(
        "/admin/v1/channels",
        json=body,
        headers={"Authorization": f"Bearer {env.admin_token.access_token}"},
    )
    assert response.status_code == 201, response.text
    channel = ChannelView.model_validate(response.json())
    domain = (await env.services.channels.data_scopes(env.admin, channel.channel_id))[0]
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel.channel_id,
            environment="test",
            data_scope_id=domain.data_scope_id,
        ),
    )
    manager = await env.iam.authentication.admin_session(
        token.access_token,
        new_id("request"),
        governance=True,
    )
    return SimpleNamespace(channel=channel, domain=domain, token=token, manager=manager)


async def test_arbitrary_channel_issues_credentials_without_business_category(channel_env):
    env = channel_env
    workspace = await open_workspace(env)
    identity = await credential(env, workspace)
    assert "business_type" not in workspace.channel.model_dump()
    assert "business_type_name" not in workspace.channel.model_dump()
    assert workspace.domain.external_scope_type == "源系统/workspace"
    assert workspace.domain.external_scope_type_name is None
    assert identity.context.scope.channel_id == workspace.channel.channel_id
    assert identity.context.client_id == identity.client.client_id
    assert identity.context.key_id == identity.key.key.key_id
    options = await env.client.get(
        "/admin/v1/channel-create-options",
        headers={
            "Authorization": f"Bearer {env.admin_token.access_token}",
        },
    )
    assert options.status_code == 200
    assert "business_types" not in options.json()
    schema = env.client._transport.app.openapi()
    assert "/admin/v1/channel-business-types" not in schema["paths"]
    assert "business_type" not in schema["components"]["schemas"]["ChannelCreate"]["properties"]


async def test_custom_mapping_concurrency_channel_environment_and_no_default(channel_env):
    env = channel_env
    first = await open_workspace(env)
    other = await open_workspace(env, code="other")
    assert first.domain.data_scope_id != other.domain.data_scope_id
    body = DataScopeCreate(
        environment="test",
        name="第二工作区",
        external_scope_type="org/project",
        external_scope_id="001",
    )
    results = await asyncio.gather(
        *(
            env.services.channels.create_data_scope(env.admin, first.channel.channel_id, body)
            for _ in range(6)
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert {r.code for r in results if isinstance(r, ServiceError)} == {"MAPPING_EXISTS"}
    await env.services.channels.create_environment(
        env.admin, first.channel.channel_id, EnvironmentCreate(environment="dev", name="开发")
    )
    separate = await env.services.channels.create_data_scope(
        env.admin, first.channel.channel_id, body.model_copy(update={"environment": "dev"})
    )
    assert separate.environment == "dev"
    path = f"/admin/v1/channels/{first.channel.channel_id}/data-scopes"
    headers = {"Authorization": f"Bearer {env.admin_token.access_token}"}
    for invalid in (
        {"environment": "test", "name": "缺少映射"},
        {**body.model_dump(), "external_scope_type": " "},
        {**body.model_dump(), "external_scope_id": " 001"},
        {**body.model_dump(), "external_scope_id": "001\n"},
    ):
        assert (await env.client.post(path, json=invalid, headers=headers)).status_code == 422
    response = await env.client.patch(
        f"{path}/{first.domain.data_scope_id}",
        headers=headers,
        json={"revision": first.domain.revision, "external_scope_id": "other"},
    )
    assert response.status_code == 422


async def test_custom_scope_delegation_without_http_connection_and_strict_isolation(channel_env):
    env = channel_env
    first = await open_workspace(env)
    second = await open_workspace(env, code="another")
    identities = [await credential(env, channel) for channel in (first, second)]
    bundle = build_integration_services(env.engine, env.iam.authorization, provider=TestKeys())
    env.client._transport.app.state.integrations = bundle
    resolved = []
    signed = []
    for channel, identity in zip((first, second), identities, strict=True):
        key = await bundle.keys.create(
            channel.manager.context,
            DelegationKeyCreate(
                client_id=identity.client.client_id,
                issuer="workspace-backend",
                audience="creativity-api",
                expires_at=utcnow() + timedelta(hours=1),
            ),
        )
        now = int(utcnow().timestamp())
        claims = DelegationClaims(
            subject_type="RESEARCHER",
            subject_id="user-001",
            data_scope={
                "type": channel.domain.external_scope_type,
                "id": channel.domain.external_scope_id,
            },
            actions=["run:create", "run:read"],
            resources={"agent": ["assistant"], "run": ["*"]},
            issuer="workspace-backend",
            audience="creativity-api",
            issued_at=now,
            expires_at=now + 300,
            nonce="same_nonce_12345678",
            request=bind_request("POST", "/api/v1/runs", b"{}", "same"),
        )
        secret = base64.b64decode(key.signing_secret)
        envelope = sign(claims, key.key.kid, secret)
        current = await bundle.delegation.verify(identity.context, envelope, claims.request)
        resolved.append(current)
        signed.append((key, secret, claims, envelope))
        assert current.scope.data_scope_id == channel.domain.data_scope_id
        assert current.scope.subject_id == "user-001"
        options = await env.client.get(
            "/admin/v1/delegation-keys/options",
            headers={
                "Authorization": f"Bearer {channel.token.access_token}",
            },
        )
        assert options.status_code == 200
        assert options.json() == {
            "clients": [{"value": identity.client.client_id, "label": "业务后端"}]
        }
    assert resolved[0].scope.channel_id != resolved[1].scope.channel_id
    assert resolved[0].delegation_id != resolved[1].delegation_id
    key, secret, claims, envelope = signed[0]
    with pytest.raises(ServiceError, match="委托无效"):
        await bundle.delegation.verify(identities[1].context, envelope, claims.request)
    for change in (
        {"environment": "dev"},
        {"channel_id": second.channel.channel_id},
        {"data_scope": {"type": "default", "id": "default"}},
        {"data_scope": {"type": "源系统/workspace", "id": "研发:001/乙"}},
    ):
        modified = DelegationClaims.model_validate({**claims.model_dump(), **change})
        with pytest.raises(ServiceError):
            await bundle.delegation.verify(
                identities[0].context, sign(modified, key.key.kid, secret), modified.request
            )
    modified = claims.model_copy(update={"subject_id": "another-user"})
    with pytest.raises(ServiceError, match="随机数"):
        await bundle.delegation.verify(
            identities[0].context, sign(modified, key.key.kid, secret), modified.request
        )
    await env.services.channels.update_data_scope(
        env.admin,
        first.channel.channel_id,
        first.domain.data_scope_id,
        DataScopeUpdate(revision=first.domain.revision, status="DISABLED"),
    )
    with pytest.raises(ServiceError):
        await bundle.delegation.verify(identities[0].context, envelope, claims.request)


@pytest.mark.parametrize(
    "code,kind,source_id",
    [
        ("rental", "default", "default"),
        ("playmate", "club", "club-1"),
    ],
)
async def test_legacy_explicit_mapping_and_identity_are_preserved(
    channel_env, code, kind, source_id
):
    workspace = await provision(channel_env, code, kind)
    identity = await credential(channel_env, workspace)
    assert (workspace.domain.external_scope_type, workspace.domain.external_scope_id) == (
        kind,
        source_id,
    )
    await channel_env.iam.authentication.revalidate(identity.context)
