"""应用层汇总已实现模型，公共业务服务不依赖模块实现。"""

from sqlalchemy import MetaData

from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion.resources import register_content_models
from creativity_service.modules.agents.tables import metadata as agent_metadata
from creativity_service.modules.channels.tables import metadata as channel_metadata
from creativity_service.modules.conversations.tables import metadata as conversation_metadata
from creativity_service.modules.data_lifecycle.tables import metadata as lifecycle_metadata
from creativity_service.modules.evaluations.tables import metadata as evaluation_metadata
from creativity_service.modules.iam.operations_tables import metadata as operations_metadata
from creativity_service.modules.iam.tables import metadata as iam_metadata
from creativity_service.modules.integrations.automation_tables import (
    metadata as automation_metadata,
)
from creativity_service.modules.integrations.tables import metadata as integration_metadata
from creativity_service.modules.mcp.oauth_tables import metadata as oauth_metadata
from creativity_service.modules.mcp.tables import metadata as mcp_metadata
from creativity_service.modules.memory.embedding_tables import metadata as embedding_metadata
from creativity_service.modules.memory.tables import metadata as memory_metadata
from creativity_service.modules.models.tables import metadata as model_metadata
from creativity_service.modules.prompts.tables import metadata as prompt_metadata
from creativity_service.modules.runs.tables import metadata as run_metadata
from creativity_service.modules.skills.tables import metadata as skill_metadata
from creativity_service.modules.tools.tables import metadata as tool_metadata
from creativity_service.modules.usage.tables import metadata as usage_metadata

metadata = MetaData()
for source in (
    lifecycle_metadata,
    agent_metadata,
    evaluation_metadata,
    memory_metadata,
    embedding_metadata,
    conversation_metadata,
    integration_metadata,
    automation_metadata,
    model_metadata,
    core_metadata,
    run_metadata,
    iam_metadata,
    operations_metadata,
    channel_metadata,
    prompt_metadata,
    tool_metadata,
    skill_metadata,
    mcp_metadata,
    oauth_metadata,
    usage_metadata,
):
    for table in source.tables.values():
        table.to_metadata(metadata)

register_content_models(metadata)
