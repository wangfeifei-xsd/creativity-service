"""PKCE 一次性状态、身份隔离及网络刷新在短事务之外执行。"""

import asyncio
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from creativity_service.core.database import Repository, assert_external_io_allowed, transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.core.security.outbound import Destination
from creativity_service.integrations.outbound import HttpResponse
from creativity_service.modules.mcp.oauth import (
    OAuthCallback,
    OAuthProfile,
    OAuthSettings,
    OAuthStart,
)
from creativity_service.modules.mcp.oauth_tables import metadata
from creativity_service.modules.mcp.schemas import McpCreate

pytestmark = pytest.mark.integration


class Provider:
    def __init__(self):
        self.calls = []

    async def post(self, scope, purpose, url, body, **kwargs):
        assert_external_io_allowed()
        data = parse_qs(body.decode())
        self.calls.append(data)
        await asyncio.sleep(0.1)
        return HttpResponse(
            200,
            json.dumps(
                {
                    "access_token": "provider-token",
                    "refresh_token": "provider-refresh",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            ).encode(),
        )


async def test_pkce_replay_wrong_user_refresh_and_revoke(mcp_env, monkeypatch):
    env = mcp_env
    service, context = env.mcp, env.context
    resource = f"http://127.0.0.1:{env.source.port}/mcp"
    service.outbound.destinations += (
        Destination(
            context.scope.channel_id,
            "test",
            "oauth",
            "127.0.0.1",
            port=env.source.port,
            scheme="http",
            allowed_networks=("127.0.0.1/32",),
        ),
    )
    profile = OAuthProfile(
        profile_id="fixture",
        name="受信身份方",
        channels={context.scope.channel_id},
        resource=resource,
        authorization_endpoint=resource + "/authorize",
        token_endpoint=resource + "/token",
        redirect_uri="https://platform.test/mcp-connections",
        client_id="registered-client",
        scopes=("read",),
    )
    service.oauth.settings = OAuthSettings(_env_file=None, profiles=[profile])
    provider = Provider()
    service.oauth.http = provider
    connection = await service.create(
        context, McpCreate(name="委托连接", transport="oauth", endpoint=resource)
    )
    started = await service.oauth.start(
        context, connection.connection_id, OAuthStart(profile_id="fixture")
    )
    params = parse_qs(urlsplit(started["authorization_url"]).query)
    assert params["code_challenge_method"] == ["S256"]
    body = OAuthCallback(state=params["state"][0], code="one-time-code")
    with pytest.raises(ServiceError) as wrong:
        await service.oauth.callback(
            context.model_copy(update={"principal_id": "other-person"}), body
        )
    assert wrong.value.code == "OAUTH_STATE_INVALID"
    grant = await service.oauth.callback(context, body)
    assert provider.calls[0]["redirect_uri"] == [profile.redirect_uri]
    assert provider.calls[0]["code_verifier"]
    with pytest.raises(ServiceError):
        await service.oauth.callback(context, body)
    assert len(provider.calls) == 1
    with pytest.raises(ServiceError):
        await service.oauth.grant(
            context.model_copy(update={"principal_id": "other-person"}), connection.connection_id
        )
    row = await service.get(context, "mcp_connections", connection.connection_id)

    async def read(secret):
        return secret.get_secret_value().decode()

    assert await service.oauth.call(context, row, read) == "provider-token"
    repo = Repository(metadata.tables["mcp_oauth_tokens"], context.scope)
    async with transaction(
        env.engine,
        context.scope,
        [record_key(context.scope.channel_id, "mcp_oauth_tokens", grant.grant_id)],
    ) as uow:
        old = await repo.get(uow.connection, grant.grant_id)
        await repo.change(
            uow, grant.grant_id, old["revision"], {"expires_at": utcnow() - timedelta(seconds=1)}
        )
    results = await asyncio.gather(
        *(service.oauth.call(context, row, read) for _ in range(2)), return_exceptions=True
    )
    assert "provider-token" in results
    assert len([c for c in provider.calls if c["grant_type"] == ["refresh_token"]]) == 1
    await service.oauth.revoke(context, connection.connection_id, "user")
    with pytest.raises(ServiceError):
        await service.oauth.call(context, row, read)

    # 过期流程无人回调时也清除 verifier 凭据。
    from creativity_service.core.database.tables import metadata as core_metadata
    from creativity_service.core.primitives import digest

    started = await service.oauth.start(
        context, connection.connection_id, OAuthStart(profile_id="fixture")
    )
    state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
    flows = Repository(metadata.tables["mcp_oauth_flows"], context.scope)
    async with transaction(
        env.engine,
        context.scope,
        [record_key(context.scope.channel_id, "mcp_oauth_flows", digest(state))],
    ) as uow:
        flow = await flows.get(uow.connection, digest(state))
        await flows.change(
            uow, flow["id"], flow["revision"], {"expires_at": utcnow() - timedelta(seconds=1)}
        )
    await service.oauth.sweep(context.scope.channel_id)
    async with env.engine.connect() as db:
        assert (
            await Repository(core_metadata.tables["credentials"], context.scope).get(
                db, flow["verifier_ref"]
            )
            is None
        )
    with pytest.raises(ServiceError):
        await service.oauth.callback(context, OAuthCallback(state=state, code="expired"))

    # 轮换刷新响应丢失后需要重新授权，不重放可能已经消费的 refresh_token。
    started = await service.oauth.start(
        context, connection.connection_id, OAuthStart(profile_id="fixture")
    )
    state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
    grant = await service.oauth.callback(context, OAuthCallback(state=state, code="replacement"))
    async with transaction(
        env.engine,
        context.scope,
        [record_key(context.scope.channel_id, "mcp_oauth_tokens", grant.grant_id)],
    ) as uow:
        old = await repo.get(uow.connection, grant.grant_id)
        await repo.change(
            uow, grant.grant_id, old["revision"], {"expires_at": utcnow() - timedelta(seconds=1)}
        )
    lost_calls = []

    async def lost_response(*args, **kwargs):
        lost_calls.append(True)
        raise ServiceError("UPSTREAM_TIMEOUT", "刷新响应丢失", 504)

    monkeypatch.setattr(provider, "post", lost_response)
    for _ in range(2):
        with pytest.raises(ServiceError):
            await service.oauth.call(context, row, read)
    assert len(lost_calls) == 1
    async with env.engine.connect() as db:
        latest = await repo.get(db, grant.grant_id)
        assert latest["state"] == "REAUTH_REQUIRED"
        assert (
            await Repository(core_metadata.tables["credentials"], context.scope).get(
                db, latest["credential_ref"]
            )
            is None
        )
