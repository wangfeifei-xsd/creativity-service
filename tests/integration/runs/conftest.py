"""使用独立 PostgreSQL schema；内部定义与授权替身仅存在于测试装配。"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.primitives import RunInput
from creativity_service.core.versioning import VersionService
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.runs.schemas import ExecutionPolicy, ResolvedDefinition, StepPolicy
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.usage.services import UsageService
from tests.integration.core.conftest import authorization, context, engine, validator

__all__ = ["authorization", "context", "engine", "validator"]


@pytest.fixture(scope="session")
def database_schema():
    engine = create_engine(Settings().database_url.get_secret_value())
    name = f"test_runs_{uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{name}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{name}"'))
            config = Config("alembic.ini")
            config.set_main_option("version_table_schema", name)
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield name
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
        engine.dispose()


class InternalDefinition:
    def __init__(self, definition):
        self.definition = definition

    async def resolve(self, context, request):
        return self.definition


@pytest.fixture
async def env(engine, context, authorization, validator):
    versions = VersionService(engine, authorization, validator)
    schema = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    model = await versions.create_draft(
        context, "model", "model_one", "模型初版", {}, [], {"type": "object"}
    )
    model = await versions.freeze(context, model.version_id, 1)
    agent = await versions.create_draft(
        context, "agent", "agent_one", "测试初版", {}, [model.version_id], schema
    )
    agent = await versions.freeze(context, agent.version_id, 1)
    resolver = InternalDefinition(
        ResolvedDefinition(
            agent_id="agent_one",
            agent_name="内部测试任务",
            version_ids=(agent.version_id, model.version_id),
            input_schema=schema,
            output_schema=schema,
            policy=ExecutionPolicy(
                steps=(
                    StepPolicy(
                        node_key="compute", kind="compute", target_version_id=agent.version_id
                    ),
                    StepPolicy(node_key="model", kind="model", target_version_id=model.version_id),
                    StepPolicy(node_key="tool", kind="tool", target_version_id=agent.version_id),
                )
            ),
        )
    )
    budgets = BudgetService(engine)
    ledger = UsageService(engine, budgets)
    runs = RunService(engine, authorization, versions, budgets, ledger, resolver=resolver)
    return SimpleNamespace(
        runs=runs,
        engine=engine,
        context=context,
        authorization=authorization,
        resolver=resolver,
        budgets=budgets,
        ledger=ledger,
        request=RunInput(agent_code="test_agent", input={"value": 1}),
    )
