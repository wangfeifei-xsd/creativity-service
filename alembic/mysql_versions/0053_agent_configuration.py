"""智能体改为单份当前配置，环境上线状态与启停状态分别保存。"""

import json
from hashlib import sha256

import sqlalchemy as sa

from alembic import context, op

revision = "0053_agent_configuration"
down_revision = "0052_file_urls"
branch_labels = None
depends_on = None


def identifier(parts):
    return sha256(
        json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("智能体配置迁移需要读取现有配置，请在线执行迁移")
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "is_online" not in {c["name"] for c in inspector.get_columns("agent_environment_states")}:
        op.add_column(
            "agent_environment_states",
            sa.Column("is_online", sa.Boolean(), nullable=True, comment="是否上线"),
        )
    metadata = sa.MetaData()
    versions = sa.Table("resource_versions", metadata, autoload_with=connection)
    agents = sa.Table("agents", metadata, autoload_with=connection)
    states = sa.Table("agent_environment_states", metadata, autoload_with=connection)
    mappings = sa.Table("release_mappings", metadata, autoload_with=connection)
    links = sa.Table("source_links", metadata, autoload_with=connection)
    # 分批枚举主档，逐条转换历史数据；运行快照和旧内容行保留原始证据。
    cursor = ""
    while True:
        batch = (
            connection.execute(
                sa.select(agents).where(agents.c.id > cursor).order_by(agents.c.id).limit(200)
            )
            .mappings()
            .all()
        )
        if not batch:
            break
        for agent in batch:
            channel, aid = agent["channel_id"], agent["id"]
            rows = (
                connection.execute(
                    sa.select(versions).where(
                        versions.c.channel_id == channel,
                        versions.c.resource_type == "agent",
                        versions.c.resource_id == aid,
                        versions.c.is_deleted.is_(False),
                    )
                )
                .mappings()
                .all()
            )
            if not rows:
                continue
            releases = (
                connection.execute(
                    sa.select(mappings).where(
                        mappings.c.channel_id == channel,
                        mappings.c.resource_type == "agent",
                        mappings.c.resource_id == aid,
                        mappings.c.is_deleted.is_(False),
                    )
                )
                .mappings()
                .all()
            )
            previous_states = {
                r["environment"]: dict(r)
                for r in connection.execute(
                    sa.select(states).where(
                        states.c.channel_id == channel,
                        states.c.agent_id == aid,
                        states.c.is_deleted.is_(False),
                    )
                ).mappings()
            }
            current = next((r for r in rows if r["id"] == aid), None)
            by_id = {r["id"]: r for r in rows}
            # 停用仍沿用原运行配置，避免重新启用时意外采用后来保存的旧草稿。
            effective = [
                by_id[m["version_id"]]
                for m in releases
                if m["version_id"] in by_id
                and previous_states.get(m["environment"], {}).get("status", "ACTIVE") != "OFFLINE"
            ]
            if len({r["content_digest"] for r in effective}) > 1:
                raise RuntimeError(
                    f"智能体 {agent['name']} 在多个环境使用不同配置，请先统一配置后迁移"
                )
            source = current or (
                effective[0] if effective else max(rows, key=lambda r: (r["updated_at"], r["id"]))
            )
            if current is None:
                values = dict(source)
                values.update(id=aid, version_label="当前配置", state="CURRENT")
                connection.execute(versions.insert().values(**values))
                link = {
                    "id": identifier([aid, "agent", aid]),
                    "channel_id": channel,
                    "source_type": "agent",
                    "source_id": aid,
                    "derived_type": "version",
                    "derived_id": aid,
                    "subject_id": None,
                    "subject_type": None,
                    "environment": releases[0]["environment"] if releases else "dev",
                    "source_version": None,
                    "created_at": source["created_at"],
                    "updated_at": source["updated_at"],
                    "revision": 1,
                    "is_deleted": False,
                }
                # 来源关系只新增稳定配置，不改写历史运行的来源链。
                if not connection.execute(
                    sa.select(links.c.id).where(
                        links.c.channel_id == channel, links.c.id == link["id"]
                    )
                ).first():
                    connection.execute(links.insert().values(**link))
            connection.execute(
                versions.update()
                .where(
                    versions.c.channel_id == channel,
                    versions.c.resource_type == "agent",
                    versions.c.resource_id == aid,
                    versions.c.id != aid,
                )
                .values(state="RETIRED")
            )
            release_by_env = {m["environment"]: m for m in releases}
            for environment in previous_states.keys() | release_by_env.keys():
                old = previous_states.get(environment)
                previous = old["status"] if old else agent["status"]
                online = environment in release_by_env and previous != "OFFLINE"
                values = {
                    "status": "DISABLED"
                    if previous in {"EMERGENCY_STOP", "DISABLED"}
                    else "ACTIVE",
                    "is_online": online,
                }
                if old:
                    if old["is_online"] is None:
                        connection.execute(
                            states.update()
                            .where(states.c.channel_id == channel, states.c.id == old["id"])
                            .values(**values)
                        )
                else:
                    connection.execute(
                        states.insert().values(
                            id=identifier([channel, environment, "agent", aid]),
                            channel_id=channel,
                            environment=environment,
                            agent_id=aid,
                            reason="沿用原有可用状态",
                            revision=1,
                            created_at=source["created_at"],
                            updated_at=source["updated_at"],
                            is_deleted=False,
                            **values,
                        )
                    )
            connection.execute(
                mappings.update()
                .where(
                    mappings.c.channel_id == channel,
                    mappings.c.resource_type == "agent",
                    mappings.c.resource_id == aid,
                )
                .values(is_deleted=True)
            )
        cursor = batch[-1]["id"]


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("智能体配置回退需要读取现有配置，请在线执行迁移")
    connection = op.get_bind()
    # 仅无智能体数据时允许回退结构；包括已删除内容，防止丢失状态或重新暴露历史配置。
    for name in ("agents", "agent_environment_states", "resource_versions", "release_mappings"):
        table = sa.table(name, sa.column("resource_type", sa.String()))
        statement = sa.select(sa.literal(1)).select_from(table).limit(1)
        if name in {"resource_versions", "release_mappings"}:
            statement = statement.where(table.c.resource_type == "agent")
        if connection.scalar(statement):
            raise RuntimeError("单份智能体配置不能自动恢复为多版本，请使用迁移前备份")
    op.drop_column("agent_environment_states", "is_online")
