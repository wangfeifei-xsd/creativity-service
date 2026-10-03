"""真实 PostgreSQL 与会话来源；授权替身只用于模块独立验收。"""

import json
from pathlib import Path

import pytest

from creativity_service.core.artifacts import ArtifactService
from creativity_service.core.deletion import RecoveryService
from creativity_service.modules.conversations.schemas import ConversationCreate, MessageInput
from creativity_service.modules.conversations.services import ConversationService
from creativity_service.modules.memory.schemas import MemoryAttribute, PolicyInput, SourceInput
from creativity_service.modules.memory.services import MemoryService
from tests.integration.conversations.conftest import Store
from tests.integration.core.conftest import (
    authorization,
    context,
    database_schema,
    engine,
    validator,
)
from tests.integration.runs.conftest import env as run_env

__all__ = ["authorization", "context", "database_schema", "engine", "validator", "run_env"]


@pytest.fixture
async def env(run_env):
    env = run_env
    env.context = env.context.model_copy(
        update={
            "scope": env.context.scope.model_copy(
                update={
                    "data_scope_id": "club",
                    "subject_type": "user",
                    "subject_id": "same-user",
                }
            )
        }
    )
    await RecoveryService(env.engine, env.authorization).initialize_fresh(env.context)
    env.memory = MemoryService(env.engine, env.authorization)
    await env.memory.set_policy(
        env.context, PolicyInput(revision=0, attributes=business_attributes())
    )
    await env.memory.set_policy(env.context, PolicyInput(revision=0), "agent_one")
    env.conversations = ConversationService(
        env.engine,
        env.authorization,
        env.runs,
        ArtifactService(env.engine, Store(), env.authorization),
    )
    env.runs.turns = env.conversations.hooks
    conversation = await env.conversations.create(
        env.context, ConversationCreate(agent_code="test_agent", title="偏好咨询")
    )
    env.cid = conversation.conversation_id
    receipt = await env.conversations.submit(
        env.context,
        env.cid,
        MessageInput(
            client_message_id="initial", content="以后通常预算按 100 到 200 元", input={"value": 1}
        ),
    )
    env.run_id = receipt.run.run_id
    env.source = SourceInput(
        source_type="message", source_id=receipt.user_message_id, source_version="1"
    )
    return env


def business_attributes():
    return [
        MemoryAttribute.model_validate(v)
        for v in json.loads(Path(__file__).with_name("attributes.json").read_text())
    ]
