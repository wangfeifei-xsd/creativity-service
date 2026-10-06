"""管理员默认发布权增量升级，保留受限授权、渠道和环境边界。"""

from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, select, text

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.primitives import digest, utcnow

pytestmark = pytest.mark.integration


def test_admin_publish_upgrade_preserves_restricted_grants():
    engine = create_engine(Settings().database_url.get_secret_value())
    schema = "test_admin_publish_" + uuid4().hex
    try:
        with engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
            db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config("alembic.ini")
            config.set_main_option("version_table_schema", schema)
            config.attributes["connection"] = db
            command.upgrade(config, "0041_channel_environments")
            metadata = MetaData()
            metadata.reflect(db)
            roles = metadata.tables["builtin_roles"]
            old_role = (
                db.execute(select(roles).where(roles.c.role_code == "channel_admin"))
                .mappings()
                .one()
            )
            ordinary = old_role["allowed_actions"]
            db.execute(roles.update().where(roles.c.id == old_role["id"]).values(menu_ids=[]))

            def add(table, identifier, channel, **values):
                db.execute(
                    metadata.tables[table]
                    .insert()
                    .values(
                        id=identifier,
                        channel_id=channel,
                        revision=1,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                        **values,
                    )
                )

            expected = set()
            # 跨过分页边界，包含首位管理员、平台追加管理员及手工收窄授权。
            for number in range(205):
                channel, user = "tenant_" + str(number % 2), "user_" + str(number)
                member = "member_" + str(number)
                add(
                    "channel_memberships",
                    member,
                    channel,
                    user_id=user,
                    roles=["builder" if number == 4 else "channel_admin"],
                    environments=["dev", "prod"],
                    status="ACTIVE",
                )
                identifier = (
                    "initial_" + member
                    if number == 0
                    else "manual_grant"
                    if number == 2
                    else "administrator_" + digest([channel, user])[:40]
                )
                add(
                    "resource_grants",
                    identifier,
                    channel,
                    grantee_type="account",
                    grantee_id=user,
                    resource_type="channel",
                    resource_id=channel,
                    environments=["dev"],
                    allowed_actions=["model:manage"] if number == 3 else ordinary,
                )
                if number not in {2, 3, 4}:
                    expected.add(identifier)
            grants = metadata.tables["resource_grants"]
            before = {r["id"]: dict(r) for r in db.execute(select(grants)).mappings()}
            command.upgrade(config, "head")
            after = {r["id"]: dict(r) for r in db.execute(select(grants)).mappings()}
            for identifier, row in after.items():
                old = before[identifier]
                assert row["environments"] == old["environments"] == ["dev"]
                if identifier in expected:
                    assert set(row["allowed_actions"]) == set(old["allowed_actions"]) | {
                        "release:publish"
                    }
                    assert row["revision"] == old["revision"] + 1
                else:
                    assert row == old
            role = db.execute(select(roles).where(roles.c.id == old_role["id"])).mappings().one()
            assert set(role["allowed_actions"]) == set(ordinary) | {"release:publish"}
            assert "button_release_publish" in role["menu_ids"]
            audits = metadata.tables["audit_events"]
            events = (
                db.execute(
                    select(audits).where(audits.c.request_id == "0042_channel_admin_publish")
                )
                .mappings()
                .all()
            )
            assert {r["target_id"] for r in events} == expected | {old_role["id"]}
            assert all(r["summary"]["added_actions"] == ["release:publish"] for r in events)
            command.upgrade(config, "head")
            assert {r["id"]: dict(r) for r in db.execute(select(grants)).mappings()} == after
    finally:
        with engine.begin() as db:
            db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
