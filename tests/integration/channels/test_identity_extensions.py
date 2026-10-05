"""外部身份的受众和上游期限、自定义角色的实时权限上限。"""

import asyncio
import json

import pytest

from creativity_service.core.context import AuthContext
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError, new_id, utcnow
from creativity_service.integrations.outbound import HttpResponse
from creativity_service.modules.iam.custom_roles import CustomRoles, RoleSave
from creativity_service.modules.iam.external import (
    ExternalIdentity,
    IdentityExchange,
    IdentityProfile,
    IdentitySettings,
)
from creativity_service.modules.iam.schemas import (
    AccountCreate,
    ChannelContextInput,
    GrantInput,
    LoginInput,
    MembershipInput,
    PasswordChange,
)
from tests.integration.channels.conftest import INITIAL, PASSWORD, provision
from tests.support.captcha import captcha_token

pytestmark = pytest.mark.integration


class IdentityProvider:
    def __init__(self, expires):
        self.claims = {
            "active": True,
            "iss": "https://issuer.test",
            "aud": "creativity",
            "sub": "external-user",
            "exp": expires,
        }

    async def post(self, scope, purpose, url, body, **kwargs):
        assert_external_io_allowed()
        return HttpResponse(200, json.dumps(self.claims).encode())


async def test_external_identity_expiry_audience_mapping_and_logout(channel_env):
    env = channel_env
    channel = await provision(env)
    expires = int(utcnow().timestamp()) + 120
    provider = IdentityProvider(expires)
    profile = IdentityProfile(
        profile_id="company",
        name="企业身份",
        scope=channel.manager.context.scope,
        issuer="https://issuer.test",
        audience="creativity",
        endpoint="https://issuer.test/introspect",
        client_id="platform",
        client_secret="source-secret",
        subjects={"external-user": env.user_id},
    )
    service = ExternalIdentity(
        env.iam, IdentitySettings(_env_file=None, profiles=[profile]), provider
    )
    body = IdentityExchange(profile_id="company", token="opaque-upstream-token")
    for update in (
        {"aud": "other"},
        {"iss": "https://untrusted.test"},
        {"exp": 1},
        {"active": False},
        {"sub": "unmapped"},
    ):
        original = provider.claims.copy()
        provider.claims.update(update)
        with pytest.raises(ServiceError):
            await service.exchange(body, "test", new_id("request"))
        provider.claims = original
    issued = await service.exchange(body, "test", new_id("request"))
    assert 0 < issued.expires_in <= 120 and "." not in issued.access_token
    session = await env.iam.authentication.admin_session(issued.access_token, new_id("request"))
    switched = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            **{
                k: getattr(channel.manager.context.scope, k)
                for k in ("channel_id", "environment", "data_scope_id")
            }
        ),
    )
    assert switched.expires_at.timestamp() <= expires
    with pytest.raises(ServiceError):
        await env.iam.authentication.admin_session(issued.access_token, new_id("request"))
    session = await env.iam.authentication.admin_session(switched.access_token, new_id("request"))
    with pytest.raises(ServiceError):
        await env.iam.sessions.enter_platform(session)
    await env.iam.sessions.logout(session)
    with pytest.raises(ServiceError):
        await env.iam.authentication.admin_session(switched.access_token, new_id("request"))


async def test_custom_role_scope_revision_and_immediate_revocation(channel_env):
    env = channel_env
    channel = await provision(env)
    service = CustomRoles(env.iam.access)
    role = await service.save(
        channel.manager, RoleSave(name="运行观察", allowed_actions=["run:read"])
    )
    with pytest.raises(ServiceError):
        await service.save(
            channel.manager, RoleSave(name="非法提升", allowed_actions=["account:manage"])
        )
    for role_id in ("builder", "platform_admin"):
        with pytest.raises(ServiceError) as builtin:
            await service.save(
                channel.manager,
                RoleSave(revision=1, name="改内置", allowed_actions=["run:read"]),
                role_id,
            )
        assert builtin.value.code == "BUILTIN_ROLE_READ_ONLY"
    account = await env.iam.accounts.create(
        env.admin,
        AccountCreate(login_name="role-test", display_name="角色验收", initial_password=INITIAL),
    )
    issued = await env.iam.sessions.login(
        LoginInput(
            login_name="role-test",
            password=INITIAL,
            captcha_token=await captcha_token(env.iam, "role-test", "test"),
        ),
        "test",
        new_id("request"),
    )
    initial = await env.iam.authentication.admin_session(
        issued.access_token, new_id("request"), allow_initial=True
    )
    await env.iam.accounts.change_password(
        initial, PasswordChange(current_password=INITIAL, new_password=PASSWORD)
    )
    scope = channel.manager.context.scope
    await env.iam.access.put_member(
        channel.manager,
        scope.channel_id,
        account.user_id,
        MembershipInput(
            roles=[role["id"]], environments=[scope.environment], data_scopes=[scope.data_scope_id]
        ),
    )
    await env.iam.access.put_grant(
        channel.manager,
        scope.channel_id,
        "custom-grant",
        GrantInput(
            grantee_type="role",
            grantee_id=role["id"],
            resource_type="channel",
            resource_id=scope.channel_id,
            allowed_actions=["run:read"],
            environments=[scope.environment],
            data_scopes=[scope.data_scope_id],
        ),
    )
    context = AuthContext(
        scope=scope,
        principal_type="worker",
        principal_id=account.user_id,
        actor_id=account.user_id,
        request_id=new_id("request"),
    )
    assert (
        await env.iam.authorization.check(context, "run:read", "channel", scope.channel_id)
    ).allowed
    results = await asyncio.gather(
        *(
            service.save(
                channel.manager,
                RoleSave(
                    revision=role["revision"],
                    name="已停用观察",
                    allowed_actions=["run:read"],
                    active=False,
                ),
                role["id"],
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(value, ServiceError) for value in results) == 1
    assert not (
        await env.iam.authorization.check(context, "run:read", "channel", scope.channel_id)
    ).allowed
    other = await provision(env, "partner", "playmate")
    with pytest.raises(ServiceError):
        await env.iam.access.put_member(
            other.manager,
            other.channel.channel_id,
            account.user_id,
            MembershipInput(
                roles=[role["id"]], environments=["test"], data_scopes=[other.domain.data_scope_id]
            ),
        )
