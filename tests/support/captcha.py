"""隔离测试通过服务端挑战夹具取得凭据，不在生产路径提供验证码旁路。"""

from creativity_service.modules.iam.captcha import CaptchaRecord
from creativity_service.modules.iam.schemas import CaptchaChallenge, CaptchaVerifyInput
from creativity_service.modules.iam.services import IamServices


async def captcha_challenge(
    iam: IamServices, name: str, remote_ip: str
) -> tuple[CaptchaChallenge, int]:
    challenge = await iam.captcha.challenge(name, remote_ip)
    key = iam.captcha.key("challenge", challenge.challenge_id)
    raw = await iam.captcha.redis.get(key)
    assert raw is not None
    record = CaptchaRecord.model_validate_json(raw)
    assert record.target is not None
    await iam.captcha.redis.set(
        key,
        record.model_copy(update={"issued_at": record.issued_at - 1}).model_dump_json(),
        xx=True,
        keepttl=True,
    )
    return challenge, record.target


async def captcha_token(iam: IamServices, name: str, remote_ip: str) -> str:
    challenge, target = await captcha_challenge(iam, name, remote_ip)
    result = await iam.captcha.verify(
        CaptchaVerifyInput(challenge_id=challenge.challenge_id, offset=target), remote_ip
    )
    return result.captcha_token
