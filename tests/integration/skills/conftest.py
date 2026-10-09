"""复用真实 MySQL、Redis、IAM 与 MinIO，技能测试不伪造外部执行。"""

import boto3
import pytest

from creativity_service.core.artifacts import S3ObjectStore
from creativity_service.core.config import Settings
from creativity_service.core.services import build_core_services
from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.skills.assembly import build_skill_service, register_skill_cleanup
from creativity_service.modules.tools.assembly import build_tool_services
from tests.integration.channels.conftest import channel_body, channel_env, login

__all__ = ["channel_env"]


class RecordingStore(S3ObjectStore):
    def __init__(self, client, bucket):
        super().__init__(client, bucket)
        self.keys = []
        self.reads = []

    async def put(self, key, data, content_type):
        self.keys.append(key)
        await super().put(key, data, content_type)

    async def get(self, key, max_bytes):
        self.reads.append(key)
        return await super().get(key, max_bytes)


@pytest.fixture
async def skills_env(channel_env):
    env = channel_env
    channel = await env.services.channels.create(
        env.admin,
        channel_body(env).model_copy(
            update={
                "independent_actions": ["release:publish", "data:read_sensitive", "data:export"]
            }
        ),
    )
    _, session = await login(env)
    response = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel.channel_id,
            environment="test",
        ),
    )
    env.context = await env.iam.authentication.authenticate(response.access_token, "management")
    settings = Settings()
    client = boto3.client(
        "s3",
        endpoint_url=str(settings.s3_endpoint_url),
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
    env.store = RecordingStore(client, settings.s3_bucket)
    env.tools = build_tool_services(env.engine, env.iam.authorization)
    env.skills = build_skill_service(
        env.engine, env.iam.authorization, env.store, env.tools.management
    )
    app = env.client._transport.app
    app.state.skills = env.skills
    app.state.tools = env.tools
    app.state.core = build_core_services(env.engine, env.store, env.iam.authorization)
    register_skill_cleanup(app.state.core.cleanup, env.skills)
    env.client.headers["Authorization"] = f"Bearer {response.access_token}"
    yield env
    for key in env.store.keys:
        await env.store.delete(key)
    client.close()
