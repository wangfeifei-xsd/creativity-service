"""既有不同配置拆分后仍保持各自关联，运行快照和真实使用证据不被覆盖。"""

from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.primitives import digest, utcnow

pytestmark = pytest.mark.integration


def test_split_configurations_preserves_bindings_and_history():
    engine = sa.create_engine(Settings().database_url.get_secret_value())
    schema = f"resource_migration_{uuid4().hex}"
    now = utcnow()
    common = dict(channel_id="tenant", created_at=now, updated_at=now, revision=1)
    config = Config("alembic.ini")
    config.set_main_option("version_table_schema", schema)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            config.attributes["connection"] = connection
            command.upgrade(config, "0042_channel_admin_publish")
            meta = sa.MetaData()
            meta.reflect(connection)
            t = meta.tables
            connection.execute(
                t["prompts"].insert(),
                {
                    **common,
                    "id": "prompt",
                    "prompt_code": "shared",
                    "name": "共同提示词",
                    "purpose": "业务",
                    "owner": "owner",
                    "status": "ACTIVE",
                },
            )
            values = []
            for identifier, text in (("old_a", "配置甲"), ("old_b", "配置乙")):
                content = {"system_template": text}
                values.append(
                    {
                        **common,
                        "id": identifier,
                        "resource_type": "prompt",
                        "resource_id": "prompt",
                        "version_label": identifier,
                        "state": "PUBLISHED",
                        "content": content,
                        "content_digest": digest({"content": content, "output_schema": {}}),
                        "dependencies": [],
                        "dependencies_digest": digest([]),
                        "output_schema": {},
                        "created_by": "owner",
                    }
                )
            connection.execute(t["resource_versions"].insert(), values)
            snapshot = {"versions": [], "input": "当时输入"}
            connection.execute(
                t["run_contents"].insert(),
                [
                    {
                        **common,
                        "id": "frozen",
                        "environment": "test",
                        "run_id": "run",
                        "kind": "execution_spec",
                        "payload": snapshot,
                    },
                    {
                        **common,
                        "id": "inputs",
                        "environment": "test",
                        "run_id": "run",
                        "kind": "inputs:model",
                        "payload": {
                            "source_refs": [{"resource_type": "version", "resource_id": "old_b"}]
                        },
                    },
                ],
            )
            connection.execute(
                t["runs"].insert(),
                {
                    **common,
                    "id": "run",
                    "environment": "test",
                    "agent_name": "业务智能体",
                    "purpose": "production",
                },
            )
            for identifier, dependency, environment in (
                ("agent_a", "old_a", "prod"),
                ("agent_b", "old_b", "test"),
            ):
                connection.execute(
                    t["agents"].insert(),
                    {**common, "id": identifier, "name": identifier, "status": "ACTIVE"},
                )
                content = {"bindings": {"prompt_version": dependency}}
                connection.execute(
                    t["resource_versions"].insert(),
                    {
                        **common,
                        "id": identifier + "_draft",
                        "resource_type": "agent",
                        "resource_id": identifier,
                        "version_label": "已存配置",
                        "state": "DRAFT",
                        "content": content,
                        "content_digest": digest({"content": content, "output_schema": {}}),
                        "dependencies": [dependency],
                        "dependencies_digest": digest([dependency]),
                        "output_schema": {},
                        "created_by": "owner",
                    },
                )
                connection.execute(
                    t["release_mappings"].insert(),
                    {
                        **common,
                        "id": identifier + "_release",
                        "environment": environment,
                        "resource_type": "prompt",
                        "resource_id": "prompt",
                        "version_id": dependency,
                        "published_by": "owner",
                        "release_note": "旧发布",
                    },
                )
            connection.execute(
                t["resource_grants"].insert(),
                {
                    **common,
                    "id": "grant",
                    "grantee_type": "account",
                    "grantee_id": "owner",
                    "resource_type": "prompt",
                    "resource_id": "prompt",
                    "allowed_actions": ["prompt:manage"],
                    "environments": ["test", "prod"],
                },
            )
            # JSON 证据只包含可序列化的原始配置，不包含数据库 datetime 字段。
            connection.execute(
                t["run_contents"]
                .update()
                .where(t["run_contents"].c.id == "frozen")
                .values(
                    payload={
                        "versions": [
                            {"version_id": r["id"], "content": r["content"]} for r in values
                        ]
                    }
                )
            )
            before = connection.scalar(
                sa.select(t["run_contents"].c.payload).where(t["run_contents"].c.id == "frozen")
            )
            command.upgrade(config, "head")
            current = list(
                connection.execute(
                    sa.select(t["resource_versions"]).where(
                        t["resource_versions"].c.resource_type == "prompt",
                        t["resource_versions"].c.id == t["resource_versions"].c.resource_id,
                    )
                ).mappings()
            )
            assert len(current) == 2
            assert {r["content"]["system_template"] for r in current} == {"配置甲", "配置乙"}
            by_id = {r["id"]: r for r in current}
            agents = list(
                connection.execute(
                    sa.select(t["resource_versions"]).where(
                        t["resource_versions"].c.resource_type == "agent"
                    )
                ).mappings()
            )
            assert {by_id[a["dependencies"][0]]["content"]["system_template"] for a in agents} == {
                "配置甲",
                "配置乙",
            }
            assert all(
                a["content"]["bindings"]["prompt_version"] == a["dependencies"][0] for a in agents
            )
            assert (
                connection.scalar(
                    sa.select(t["run_contents"].c.payload).where(t["run_contents"].c.id == "frozen")
                )
                == before
            )
            assert (
                connection.scalar(sa.select(sa.func.count()).select_from(t["resource_grants"])) == 2
            )
            uses = (
                connection.execute(
                    sa.text("SELECT resource_id,resource_name,agent_name FROM resource_uses")
                )
                .mappings()
                .all()
            )
            assert (
                len(uses) == 1
                and by_id[uses[0]["resource_id"]]["content"]["system_template"] == "配置乙"
            )
            assert uses[0]["agent_name"] == "业务智能体"
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
