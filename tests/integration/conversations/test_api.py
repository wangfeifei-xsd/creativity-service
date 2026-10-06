"""真实 IAM、Redis Token 和管理路由的附件交付及权限验收。"""

import httpx
import pytest

from creativity_service.app import create_schema_app
from creativity_service.core.deletion import CleanupRegistry, ContentRef
from creativity_service.core.primitives import new_id
from creativity_service.core.versioning import VersionService
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.channels.schemas import RetentionPolicy
from creativity_service.modules.conversations.assembly import build_conversation_service
from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.runs.assembly import build_run_service
from creativity_service.modules.runs.schemas import ExecutionPolicy, ResolvedDefinition, StepPolicy
from creativity_service.modules.usage.services import UsageService
from tests.integration.channels.conftest import channel_body, channel_env, login
from tests.integration.conversations.conftest import Store
from tests.integration.core.conftest import TestAuthorization, TestVersionValidator
from tests.integration.runs.conftest import InternalDefinition

pytestmark = pytest.mark.integration
__all__ = ["channel_env"]


async def test_real_token_scope_attachments_retention_and_expiration(channel_env):
    env = channel_env
    body = channel_body(env).model_copy(
        update={
            "independent_actions": ["data:read_sensitive", "data:export"],
            "retention_policy": RetentionPolicy(retention_days=7),
        }
    )
    channel = await env.services.channels.create(env.admin, body)
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel.channel_id,
            environment="test",
        ),
    )
    context = (
        await env.iam.authentication.admin_session(token.access_token, new_id("request"))
    ).context
    versions = VersionService(env.engine, TestAuthorization(), TestVersionValidator())
    agent = await versions.create_draft(
        context, "agent", "agent_one", "会话正式夹具", {}, [], {"type": "object"}
    )
    agent = await versions.freeze(context, agent.version_id, 1)
    budgets = BudgetService(env.engine)
    cleanup = CleanupRegistry()
    runs = build_run_service(
        env.engine,
        env.iam,
        versions,
        budgets,
        UsageService(env.engine, budgets),
        cleanup=cleanup,
        resolver=InternalDefinition(
            ResolvedDefinition(
                agent_id="agent_one",
                agent_name="受控助手",
                version_ids=(agent.version_id,),
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                policy=ExecutionPolicy(
                    steps=(
                        StepPolicy(
                            node_key="compute", kind="compute", target_version_id=agent.version_id
                        ),
                    )
                ),
            )
        ),
    )
    conversations = build_conversation_service(
        env.engine, env.iam.authorization, runs, Store(), cleanup
    )
    app = create_schema_app()
    app.state.conversations, app.state.authentication = conversations, env.iam.authentication
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token.access_token}"},
    ) as client:
        response = await client.post(
            "/admin/v1/conversations", json={"agent_code": "test", "channel_id": "other"}
        )
        assert response.status_code == 422
        response = await client.post("/admin/v1/conversations", json={"agent_code": "test"})
        assert response.status_code == 201, response.text
        conversation = response.json()
        from datetime import datetime

        assert (
            datetime.fromisoformat(conversation["expires_at"])
            - datetime.fromisoformat(conversation["created_at"])
        ).days in {6, 7}
        path = f"/admin/v1/conversations/{conversation['conversation_id']}"
        assert (await client.get(path)).status_code == 200
        upload = await client.post(
            f"{path}/attachments?name=notes.txt",
            content=b"private-content",
            headers={"Content-Type": "text/plain"},
        )
        assert upload.status_code == 201, upload.text
        download = upload.json()["download_path"]
        assert (await client.get(download)).content == b"private-content"
        posted = await client.post(
            f"{path}/messages",
            json={
                "client_message_id": "one",
                "content": "请处理附件",
                "attachments": [
                    {"type": "attachment", "artifact_id": upload.json()["artifact_id"]}
                ],
            },
        )
        assert posted.status_code == 202, posted.text
        assert (await client.get(f"{path}/messages")).json()["items"][0]["attachments"][0][
            "name"
        ] == "notes.txt"
        assert (await client.get(path.replace("/admin/", "/api/"))).status_code == 401
        deleted = await client.delete(path)
        assert deleted.status_code == 202, deleted.text
        assert (await client.get(path)).status_code == 404
        assert (await client.get(download)).status_code == 404
        progress = f"/admin/v1/deletions/{deleted.json()['deletion_id']}"
        assert (await client.get(progress)).status_code == 200
        await cleanup.clean(context, ContentRef("run", posted.json()["run"]["run_id"]))
        await cleanup.clean(context, ContentRef("conversation", conversation["conversation_id"]))
        assert (await client.get(progress)).json()["status"] == "WAITING_PROPAGATION"
        await env.redis.expire(env.iam.authentication.tokens.token_key(context.token_digest), 0)
        assert (await client.get(download)).status_code == 401
        assert (await client.get(progress)).status_code == 401
