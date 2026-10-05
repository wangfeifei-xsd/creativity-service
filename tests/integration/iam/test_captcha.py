"""滑块验证码的服务端强制校验、过期、绑定、重放、并发与存储故障。"""

import asyncio
import base64
import io
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from redis.exceptions import ConnectionError as RedisConnectionError

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.schemas import CaptchaVerifyInput, LoginInput
from tests.integration.iam.conftest import PASSWORD
from tests.support.captcha import captcha_challenge, captcha_token

pytestmark = pytest.mark.integration


async def test_login_requires_server_proof_before_password_lookup(iam_env, monkeypatch):
    iam, client, *_ = iam_env
    lookup = AsyncMock()
    monkeypatch.setattr(iam.sessions.repository, "credentials", lookup)
    missing = await client.post(
        "/admin/v1/auth/login", json={"login_name": "admin", "password": "password"}
    )
    assert missing.status_code == 422
    forged = await client.post(
        "/admin/v1/auth/login",
        json={"login_name": "admin", "password": "password", "captcha_token": "x" * 43},
    )
    assert forged.status_code == 400
    with pytest.raises(ServiceError, match="重新验证"):
        await iam.sessions.login(
            LoginInput(login_name="admin", password="password", captcha_token="x" * 43),
            "test-ip",
            "request-test",
        )
    lookup.assert_not_awaited()


async def test_challenge_returns_images_without_answer_and_verifies_over_http(iam_env, admin):
    iam, client, *_ = iam_env
    response = await client.post(
        "/admin/v1/auth/captcha/challenges", json={"login_name": "ROOT-ADMIN"}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    public = response.json()
    assert "target" not in public and "offset" not in public
    for field, size in (("background", (320, 160)), ("piece", (52, 52))):
        image = Image.open(io.BytesIO(base64.b64decode(public[field].split(",")[1])))
        assert image.format == "PNG" and image.size == size
    challenge, target = await captcha_challenge(iam, "ROOT-ADMIN", "127.0.0.1")
    key = iam.captcha.key("challenge", challenge.challenge_id)
    assert 0 < await iam.captcha.redis.ttl(key) <= 120
    checked = await client.post(
        "/admin/v1/auth/captcha/verify",
        json={"challenge_id": challenge.challenge_id, "offset": target + 3},
    )
    assert checked.status_code == 200
    assert checked.headers["cache-control"] == "no-store"
    proof = checked.json()["captcha_token"]
    assert 0 < await iam.captcha.redis.ttl(iam.captcha.key("proof", proof)) <= 60
    login = await client.post(
        "/admin/v1/auth/login",
        json={"login_name": "root-admin", "password": PASSWORD, "captcha_token": proof},
    )
    assert login.status_code == 200
    assert "access_token" in login.json()
    repeated = await client.post(
        "/admin/v1/auth/login",
        json={"login_name": "root-admin", "password": PASSWORD, "captcha_token": proof},
    )
    assert repeated.status_code == 400


async def test_wrong_position_and_remote_address_consume_challenge(iam_env):
    iam = iam_env[0]
    for wrong_ip in (False, True):
        challenge, target = await captcha_challenge(iam, "admin", "test-ip")
        body = CaptchaVerifyInput(
            challenge_id=challenge.challenge_id, offset=target if wrong_ip else target - 15
        )
        with pytest.raises(ServiceError) as failed:
            await iam.captcha.verify(body, "different-ip" if wrong_ip else "test-ip")
        assert failed.value.code == "CAPTCHA_FAILED"
        with pytest.raises(ServiceError) as replay:
            await iam.captcha.verify(body.model_copy(update={"offset": target}), "test-ip")
        assert replay.value.code == "CAPTCHA_EXPIRED"


async def test_proof_is_bound_to_login_and_remote_address(iam_env):
    iam = iam_env[0]
    for name, remote in (("other-admin", "test-ip"), ("admin", "different-ip")):
        proof = await captcha_token(iam, "admin", "test-ip")
        with pytest.raises(ServiceError):
            await iam.captcha.consume(proof, name, remote)
        with pytest.raises(ServiceError):
            await iam.captcha.consume(proof, "admin", "test-ip")


async def test_challenges_and_proofs_are_consumed_once_under_concurrency(iam_env):
    iam = iam_env[0]
    challenge, target = await captcha_challenge(iam, "admin", "test-ip")
    body = CaptchaVerifyInput(challenge_id=challenge.challenge_id, offset=target)
    results = await asyncio.gather(
        *(iam.captcha.verify(body, "test-ip") for _ in range(5)), return_exceptions=True
    )
    accepted = [result for result in results if not isinstance(result, Exception)]
    assert len(accepted) == 1
    proof = accepted[0].captcha_token
    results = await asyncio.gather(
        *(iam.captcha.consume(proof, "admin", "test-ip") for _ in range(5)), return_exceptions=True
    )
    assert results.count(None) == 1
    assert all(result is None or isinstance(result, ServiceError) for result in results)


@pytest.mark.parametrize("kind", ["challenge", "proof"])
@pytest.mark.parametrize("expired", [False, True])
async def test_expired_or_unbounded_redis_records_are_rejected(iam_env, kind, expired):
    iam = iam_env[0]
    challenge, target = await captcha_challenge(iam, "admin", "test-ip")
    secret = challenge.challenge_id
    if kind == "proof":
        secret = (
            await iam.captcha.verify(
                CaptchaVerifyInput(challenge_id=secret, offset=target), "test-ip"
            )
        ).captcha_token
    key = iam.captcha.key(kind, secret)
    if expired:
        await iam.captcha.redis.pexpire(key, 0)
    else:
        await iam.captcha.redis.persist(key)
    with pytest.raises(ServiceError) as failure:
        if kind == "challenge":
            await iam.captcha.verify(
                CaptchaVerifyInput(challenge_id=secret, offset=target), "test-ip"
            )
        else:
            await iam.captcha.consume(secret, "admin", "test-ip")
    assert failure.value.code == "CAPTCHA_EXPIRED"
    assert not await iam.captcha.redis.exists(key)


async def test_failed_password_consumes_proof(iam_env, admin):
    iam, client, *_ = iam_env
    proof = await captcha_token(iam, "root-admin", "127.0.0.1")
    for password, status in (("wrong-password", 401), (PASSWORD, 400)):
        response = await client.post(
            "/admin/v1/auth/login",
            json={"login_name": "root-admin", "password": password, "captcha_token": proof},
        )
        assert response.status_code == status


async def test_challenge_is_rate_limited_and_storage_failure_is_closed(iam_env, monkeypatch):
    iam, client, *_ = iam_env
    for _ in range(30):
        await iam.captcha._limit("127.0.0.1", "challenge", 30)
    response = await client.post("/admin/v1/auth/captcha/challenges", json={"login_name": "admin"})
    assert response.status_code == 429
    monkeypatch.setattr(iam.captcha.redis, "eval", AsyncMock(side_effect=RedisConnectionError()))
    for path, body in (
        ("challenges", {"login_name": "admin"}),
        ("verify", {"challenge_id": "x" * 43, "offset": 130}),
    ):
        response = await client.post(f"/admin/v1/auth/captcha/{path}", json=body)
        assert response.status_code == 503
