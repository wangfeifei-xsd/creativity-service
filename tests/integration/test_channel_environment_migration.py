"""旧范围迁移不扩大跨环境动作，保留业务 JSON 并关闭恢复中的屏障。"""

import json
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, inspect, select, text

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.primitives import digest, utcnow

pytestmark = pytest.mark.integration


def test_scope_upgrade_preserves_environment_permissions_and_version_content():
    engine = create_engine(Settings().database_url.get_secret_value())
    schema = "test_scope_upgrade_" + uuid4().hex
    try:
        with engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
            db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config("alembic.ini")
            config.set_main_option("version_table_schema", schema)
            config.attributes["connection"] = db
            command.upgrade(config, "0040_model_networks")
            old = MetaData()
            old.reflect(db)

            def add(table_name, identifier, **values):
                db.execute(
                    old.tables[table_name]
                    .insert()
                    .values(
                        id=identifier,
                        channel_id="tenant",
                        revision=1,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                        **values,
                    )
                )

            add("channels", "tenant", name="旧渠道", status="ACTIVE")
            add(
                "channel_memberships",
                "member",
                user_id="user",
                environments=["dev", "prod"],
                data_scopes=["manage_dev", "manage_prod"],
                status="ACTIVE",
                roles=["channel_admin"],
            )
            for env, action in (("dev", "run:create"), ("prod", "run:read")):
                add("channel_environments", env, environment=env, status="ACTIVE", name=env)
                add(
                    "resource_grants",
                    "workspace_" + env,
                    grantee_type="account",
                    grantee_id="user",
                    resource_type="data_scope",
                    resource_id="manage_" + env,
                    environments=[env],
                    data_scopes=["manage_" + env],
                    allowed_actions=[action],
                )
                add(
                    "recovery_barriers",
                    "barrier_" + env,
                    environment="dev",
                    data_scope_id=env,
                    subject_type=None,
                    subject_id=None,
                    state="BLOCKED" if env == "prod" else "READY",
                    recovery_id="old",
                    marker_digest=digest([]),
                )
            business = {
                "input": {"data_scope_id": "用户自己的业务字段"},
                "scope": {
                    "channel_id": "tenant",
                    "environment": "dev",
                    "data_scope_id": "manage_dev",
                    "subject_type": None,
                    "subject_id": None,
                },
            }
            add(
                "run_contents",
                "content",
                kind="execution_spec",
                environment="dev",
                data_scope_id="manage_dev",
                payload=business,
            )
            add(
                "run_contents",
                "business_input",
                kind="input",
                environment="dev",
                data_scope_id="manage_dev",
                payload=business,
            )
            content = {
                "allowed_data_domains": ["manage_dev"],
                "input_schema": {"properties": {"data_scope_id": {"type": "string"}}},
            }
            add(
                "resource_versions",
                "tool_version",
                resource_type="tool",
                resource_id="tool",
                content=content,
                output_schema={},
                content_digest=digest({"content": content, "output_schema": {}}),
                dependencies=[],
                dependencies_digest=digest([]),
            )
            command.upgrade(config, "head")
            assert "data_scopes" not in inspect(db).get_table_names()
            current = MetaData()
            current.reflect(db)
            grants = db.execute(select(current.tables["resource_grants"])).mappings().all()
            assert {tuple(g["environments"]): set(g["allowed_actions"]) for g in grants} == {
                ("dev",): {"run:create"},
                ("prod",): {"run:read"},
            }
            assert all(
                g["resource_type"] == "channel" and g["resource_id"] == "tenant" for g in grants
            )
            barrier = db.execute(select(current.tables["recovery_barriers"])).mappings().one()
            assert barrier["state"] == "BLOCKED"
            assert barrier["id"] == digest(
                [
                    "recovery",
                    {
                        "channel_id": "tenant",
                        "environment": "dev",
                        "subject_type": None,
                        "subject_id": None,
                    },
                ]
            )
            payload = db.scalar(
                select(current.tables["run_contents"].c.payload).where(
                    current.tables["run_contents"].c.id == "content"
                )
            )
            assert payload["input"] == business["input"] and "data_scope_id" not in payload["scope"]
            assert (
                db.scalar(
                    select(current.tables["run_contents"].c.payload).where(
                        current.tables["run_contents"].c.id == "business_input"
                    )
                )
                == business
            )
            version = db.execute(select(current.tables["resource_versions"])).mappings().one()
            assert "allowed_data_domains" not in version["content"]
            assert "data_scope_id" in json.dumps(version["content"])
            assert version["content_digest"] == digest(
                {"content": version["content"], "output_schema": {}}
            )
    finally:
        with engine.begin() as db:
            db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
