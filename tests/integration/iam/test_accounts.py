"""IAM-A05/A08/A10/A11：账号、初始凭据、并发及 Redis 故障。"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from creativity_service.core.primitives import ServiceError, new_id
from creativity_service.modules.iam.repositories import rows
from creativity_service.modules.iam.schemas import (
    AccountCreate,
    AccountUpdate,
    LoginInput,
    PasswordReset,
)

from .conftest import INITIAL, PASSWORD, create_user, login

pytestmark = pytest.mark.integration


async def test_initial_credentials_only_allow_change_and_logout(iam_env):
    iam, client, _, engine, _ = iam_env
    await iam.accounts.initialize_admin(
        AccountCreate(login_name="first-admin", display_name="管理员", initial_password=INITIAL)
    )
    response = await client.post(
        "/admin/v1/auth/login", json={"login_name": "FIRST-ADMIN", "password": INITIAL}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    token = response.json()["access_token"]
    assert len(token) == 43 and response.json()["must_change_password"]
    headers = {"Authorization": f"Bearer {token}"}
    for path in ("auth/session", "auth/channels", "roles", "accounts", "audit-events"):
        response = await client.get(f"/admin/v1/{path}", headers=headers)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    response = await client.post(
        "/admin/v1/auth/change-password",
        headers=headers,
        json={"current_password": INITIAL, "new_password": PASSWORD},
    )
    assert response.status_code == 204
    assert (await client.get("/admin/v1/auth/session", headers=headers)).status_code == 401
    response, _ = await login(iam, "first-admin")
    assert not response.must_change_password
    async with engine.connect() as connection:
        accounts = await rows(connection, "platform_accounts", "system")
        audit = await rows(connection, "audit_events", "system")
    assert accounts[0]["password_hash"].startswith("pbkdf2_sha256$600000$")
    assert INITIAL not in str(accounts) and PASSWORD not in str(accounts)
    assert INITIAL not in json.dumps(audit, default=str) and PASSWORD not in str(audit)


async def test_concurrent_normalized_login_names_accept_once(iam_env, admin):
    iam, _, _, engine, _ = iam_env

    async def create(index):
        try:
            return await iam.accounts.create(
                admin[1],
                AccountCreate(
                    login_name=" Duplicate.User " if index % 2 else "duplicate.user",
                    display_name="并发用户",
                    initial_password=INITIAL,
                ),
            )
        except ServiceError as exc:
            return exc

    results = await asyncio.gather(*(create(i) for i in range(8)))
    assert sum(not isinstance(result, ServiceError) for result in results) == 1
    assert [r.status for r in results if isinstance(r, ServiceError)] == [409] * 7
    async with engine.connect() as connection:
        assert (
            len(await rows(connection, "platform_accounts", "system", login_name="duplicate.user"))
            == 1
        )


async def test_reset_disable_and_logout_block_even_when_redis_cleanup_fails(
    iam_env, admin, monkeypatch
):
    iam, client, _, _, redis = iam_env
    account, (response, session) = await create_user(iam, admin[1])
    tokens = iam.authentication.tokens
    original = tokens.revoke
    monkeypatch.setattr(
        tokens,
        "revoke",
        AsyncMock(side_effect=ServiceError("DEPENDENCY_UNAVAILABLE", "暂不可用", 503)),
    )
    current = await iam.accounts.repository.account(account.user_id)
    await iam.accounts.reset(
        admin[1],
        account.user_id,
        PasswordReset(revision=current.revision, initial_password="Reset-password-9988"),
    )
    assert await redis.exists(tokens.token_key(tokens.digest(response.access_token)))
    failed = await client.get(
        "/admin/v1/auth/session", headers={"Authorization": f"Bearer {response.access_token}"}
    )
    assert failed.status_code == 401
    fresh, initial = await login(iam, account.login_name, "Reset-password-9988", initial=True)
    assert fresh.must_change_password
    current = await iam.accounts.repository.account(account.user_id)
    await iam.accounts.update(
        admin[1], account.user_id, AccountUpdate(revision=current.revision, status="DISABLED")
    )
    with pytest.raises(ServiceError) as exc:
        await iam.authentication.admin_session(
            fresh.access_token, new_id("request"), allow_initial=True
        )
    assert exc.value.code == "ACCOUNT_DISABLED"
    await iam.sessions.logout(admin[1])
    with pytest.raises(ServiceError) as exc:
        await iam.authentication.admin_session(admin[0].access_token, new_id("request"))
    assert exc.value.status == 401
    monkeypatch.setattr(tokens, "revoke", original)
    complete, pending = await iam.revocations.reconcile()
    assert complete >= 3 and pending == 0
    assert not await redis.exists(tokens.token_key(tokens.digest(response.access_token)))


async def test_redis_unavailable_is_503_and_errors_are_distinct(iam_env, admin, monkeypatch):
    iam, client, _, _, _ = iam_env
    assert (await client.get("/admin/v1/auth/session")).status_code == 401
    _, (response, _) = await create_user(iam, admin[1])
    denied = await client.get(
        "/admin/v1/accounts", headers={"Authorization": f"Bearer {response.access_token}"}
    )
    assert denied.status_code == 403
    monkeypatch.setattr(
        iam.authentication.tokens.redis,
        "eval",
        AsyncMock(side_effect=RedisConnectionError("测试断线")),
    )
    for path in ("auth/session", "accounts", "audit-events"):
        response = await client.get(
            f"/admin/v1/{path}", headers={"Authorization": f"Bearer {admin[0].access_token}"}
        )
        assert response.status_code == 503
    response = await client.post(
        "/admin/v1/auth/login", json={"login_name": "root-admin", "password": PASSWORD}
    )
    assert response.status_code == 503


async def test_login_rate_limit_and_initialization_is_explicit(iam_env, admin):
    iam = iam_env[0]
    with pytest.raises(ServiceError) as exc:
        await iam.accounts.initialize_admin(
            AccountCreate(
                login_name="another-admin", display_name="管理员", initial_password=INITIAL
            )
        )
    assert exc.value.status == 409
    for _ in range(10):
        with pytest.raises(ServiceError) as exc:
            await iam.sessions.login(
                LoginInput(login_name="missing-user", password="wrong"),
                "another-ip",
                new_id("request"),
            )
        assert exc.value.status == 401
    with pytest.raises(ServiceError) as exc:
        await iam.sessions.login(
            LoginInput(login_name="missing-user", password="wrong"), "another-ip", new_id("request")
        )
    assert exc.value.status == 429
