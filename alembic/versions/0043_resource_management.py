"""资源改为单份可编辑配置；拆分既有配置并保留历史执行证据。"""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import sqlalchemy as sa

from alembic import context, op

revision = "0043_resource_management"
down_revision = "0042_channel_admin_publish"
branch_labels = None
depends_on = None
KINDS = {"prompt": "prompts", "model_route": "model_routes", "tool": "tools", "skill": "skills"}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def batches(connection, statement):
    """有界分批迁移，不将全渠道历史内容一次读入内存。"""
    result = connection.execute(statement.execution_options(yield_per=200)).mappings()
    yield from result.partitions(200)


def rewrite(kind, content, mapping):
    """只替换平台依赖路径，不改提示词正文、工具参数和业务输出中的同名字符串。"""
    value = deepcopy(content)
    if kind == "agent":
        bindings = value.get("bindings", {})
        for key in ("prompt_version", "model_route_version", "embedding_route_version"):
            if bindings.get(key):
                bindings[key] = mapping.get(bindings[key], bindings[key])
        for key in ("tool_versions", "skill_versions"):
            if key in bindings:
                bindings[key] = [mapping.get(i, i) for i in bindings[key]]
        for item in bindings.get("skill_loading", []):
            item["version_id"] = mapping.get(item["version_id"], item["version_id"])
        for step in value.get("steps", []):
            if step.get("dependency"):
                step["dependency"] = mapping.get(step["dependency"], step["dependency"])
    elif kind == "skill":
        settings = value
        settings["tool_bindings"] = {
            k: mapping.get(v, v) for k, v in settings.get("tool_bindings", {}).items()
        }
        if "required_tool_versions" in value:
            value["required_tool_versions"] = [
                mapping.get(i, i) for i in value["required_tool_versions"]
            ]
    elif kind == "tool":
        policy = value.get("write_policy")
        if policy:
            key = "status_tool_version_id"
            policy[key] = mapping.get(policy[key], policy[key])
        binding = value.get("binding", {}).get("script") or {}
        for key in ("skill_version_id",):
            if binding.get(key):
                binding[key] = mapping.get(binding[key], binding[key])
    return value


def upgrade():
    definition = next(
        t
        for t in json.loads(
            (
                Path(__file__).parents[2]
                / "src/creativity_service/core/database/tables_v0043_0.json"
            ).read_text()
        )
        if t["name"] == "resource_uses"
    )
    types = {"bigint": sa.BigInteger(), "timestamptz": sa.DateTime(timezone=True)}
    op.create_table(
        "resource_uses",
        *[
            sa.Column(
                c["name"],
                sa.String(int(c["type"][8:-1]))
                if c["type"].startswith("varchar(")
                else types[c["type"]],
                nullable=True,
                comment=c["comment"],
            )
            for c in definition["columns"]
        ],
        comment=definition["comment"],
    )
    for i, columns in enumerate(definition["indexes"]):
        op.create_index(f"ix_resource_uses_{i}", "resource_uses", columns)
    if context.is_offline_mode():
        # 离线脚本只初始化空库；有内容的库必须在线执行依赖拆分。
        op.execute(
            "SELECT 1 / CASE WHEN EXISTS (SELECT 1 FROM resource_versions "
            "WHERE resource_type IN ('prompt','model_route','tool','skill')) THEN 0 ELSE 1 END"
        )
        return
    connection = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(
        connection,
        only=[
            *KINDS.values(),
            "resource_versions",
            "release_mappings",
            "resource_grants",
            "skill_files",
            "mcp_imports",
            "resource_uses",
            "source_links",
        ],
    )
    tables = metadata.tables
    versions, releases = tables["resource_versions"], tables["release_mappings"]
    # 同一旧配置对应一个稳定资源；保留全部非退役配置，避免静默丢失未发布内容。
    connection.execute(
        sa.text("""
        CREATE TEMP TABLE resource_conversion ON COMMIT DROP AS
        SELECT v.channel_id, v.id AS old_id, v.resource_id AS parent_id, v.resource_type,
          row_number() OVER (PARTITION BY v.channel_id, v.resource_type, v.resource_id
            ORDER BY (v.state <> 'RETIRED') DESC, EXISTS (SELECT 1 FROM release_mappings m
              WHERE m.channel_id=v.channel_id
              AND m.version_id=v.id) DESC, v.updated_at DESC, v.id) AS ordinal,
          v.resource_id AS new_id
        FROM resource_versions v WHERE v.resource_type IN ('prompt','model_route','tool','skill')
          AND v.id <> v.resource_id
    """)
    )
    conversion = sa.table(
        "resource_conversion",
        *(
            sa.column(k)
            for k in ("channel_id", "old_id", "parent_id", "resource_type", "ordinal", "new_id")
        ),
    )
    for batch in batches(connection, sa.select(conversion)):
        for row in batch:
            identifier = (
                row["parent_id"]
                if row["ordinal"] == 1
                else "resource_" + digest([revision, row["channel_id"], row["old_id"]])[:48]
            )
            connection.execute(
                conversion.update()
                .where(
                    conversion.c.channel_id == row["channel_id"],
                    conversion.c.old_id == row["old_id"],
                )
                .values(new_id=identifier)
            )
    # 先建资源，再复制配置。旧版本行保留供历史证据读取，但不再出现在资源选择器。
    for kind, table_name in KINDS.items():
        parent = tables[table_name]
        statement = (
            sa.select(parent, conversion.c.new_id, conversion.c.ordinal)
            .join(
                conversion,
                sa.and_(
                    parent.c.channel_id == conversion.c.channel_id,
                    parent.c.id == conversion.c.parent_id,
                ),
            )
            .where(conversion.c.resource_type == kind, conversion.c.ordinal > 1)
        )
        for batch in batches(connection, statement):
            inserts = []
            for row in batch:
                copy = {c.name: row[c.name] for c in parent.c}
                copy["id"] = row["new_id"]
                copy["name"] = f"{row['name'][:100]}（独立配置 {row['ordinal']}）"
                code = "code" if kind == "model_route" else f"{kind}_code"
                copy[code] = f"{row[code][:50]}_{digest(row['new_id'])[:10]}"
                inserts.append(copy)
            connection.execute(parent.insert(), inserts)
        grants = tables["resource_grants"]
        statement = (
            sa.select(grants, conversion.c.new_id)
            .join(
                conversion,
                sa.and_(
                    grants.c.channel_id == conversion.c.channel_id,
                    grants.c.resource_id == conversion.c.parent_id,
                    grants.c.resource_type == conversion.c.resource_type,
                ),
            )
            .where(conversion.c.resource_type == kind, conversion.c.ordinal > 1)
        )
        for batch in batches(connection, statement):
            connection.execute(
                grants.insert(),
                [
                    {
                        **{c.name: r[c.name] for c in grants.c},
                        "id": digest([revision, r["id"], r["new_id"]]),
                        "resource_id": r["new_id"],
                    }
                    for r in batch
                ],
            )
    statement = sa.select(versions, conversion.c.new_id).join(
        conversion,
        sa.and_(
            versions.c.channel_id == conversion.c.channel_id, versions.c.id == conversion.c.old_id
        ),
    )
    for batch in batches(connection, statement):
        configs = []
        for row in batch:
            copy = {c.name: row[c.name] for c in versions.c}
            copy.update(id=row["new_id"], resource_id=row["new_id"], version_label="当前配置")
            configs.append(copy)
        connection.execute(versions.insert(), configs)
    # 批量重写当前依赖、技能文件索引、MCP 导入定位；运行和测试证据原样保留。
    current = sa.select(versions).where(
        sa.or_(versions.c.id == versions.c.resource_id, versions.c.resource_type == "agent")
    )
    for batch in batches(connection, current):
        targets = {(r["channel_id"], d) for r in batch for d in r["dependencies"]}
        aliases = (
            {
                (r["channel_id"], r["old_id"]): r["new_id"]
                for r in connection.execute(
                    sa.select(conversion).where(
                        sa.tuple_(conversion.c.channel_id, conversion.c.old_id).in_(targets)
                    )
                ).mappings()
            }
            if targets
            else {}
        )
        for row in batch:
            mapping = {d: aliases.get((row["channel_id"], d), d) for d in row["dependencies"]}
            content = rewrite(row["resource_type"], row["content"], mapping)
            deps = sorted({mapping.get(d, d) for d in row["dependencies"]})
            connection.execute(
                versions.update()
                .where(versions.c.channel_id == row["channel_id"], versions.c.id == row["id"])
                .values(
                    content=content,
                    dependencies=deps,
                    content_digest=digest(
                        {"content": content, "output_schema": row["output_schema"]}
                    ),
                    dependencies_digest=digest(deps),
                )
            )
    for name, field in (("skill_files", "version_id"), ("mcp_imports", "imported_version")):
        table = tables[name]
        values = {field: conversion.c.new_id}
        if name == "mcp_imports":
            values["local_tool_id"] = conversion.c.new_id
        connection.execute(
            table.update()
            .where(
                table.c.channel_id == conversion.c.channel_id, table.c[field] == conversion.c.old_id
            )
            .values(**values)
        )
    for batch in batches(
        connection,
        sa.select(releases, conversion.c.new_id).join(
            conversion,
            sa.and_(
                releases.c.channel_id == conversion.c.channel_id,
                releases.c.version_id == conversion.c.old_id,
            ),
        ),
    ):
        for row in batch:
            connection.execute(
                releases.update()
                .where(releases.c.channel_id == row["channel_id"], releases.c.id == row["id"])
                .values(
                    id=digest(
                        [row["channel_id"], row["environment"], row["resource_type"], row["new_id"]]
                    ),
                    resource_id=row["new_id"],
                    version_id=row["new_id"],
                )
            )
    # 当前智能体曾直接引用已冻结内容但未建立环境映射时，补齐其既有使用环境。
    connection.execute(
        sa.text("""
        CREATE TEMP TABLE inherited_releases ON COMMIT DROP AS
        SELECT DISTINCT v.channel_id, d.value AS resource_id, r.resource_type, m.environment
        FROM resource_versions v JOIN release_mappings m
          ON m.channel_id=v.channel_id AND m.version_id=v.id
        CROSS JOIN LATERAL jsonb_array_elements_text(v.dependencies) d(value)
        JOIN resource_versions r ON r.channel_id=v.channel_id
          AND r.id=d.value AND r.id=r.resource_id
        WHERE r.resource_type IN ('prompt','model_route','tool','skill')
        AND r.state='PUBLISHED'
    """)
    )
    inherited = sa.table(
        "inherited_releases",
        *(sa.column(c) for c in ("channel_id", "resource_id", "resource_type", "environment")),
    )
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    for batch in batches(
        connection,
        sa.select(inherited).where(
            ~sa.exists(
                sa.select(releases.c.id).where(
                    releases.c.channel_id == inherited.c.channel_id,
                    releases.c.environment == inherited.c.environment,
                    releases.c.resource_id == inherited.c.resource_id,
                )
            )
        ),
    ):
        connection.execute(
            releases.insert(),
            [
                {
                    **dict(r),
                    "id": digest(
                        [r["channel_id"], r["environment"], r["resource_type"], r["resource_id"]]
                    ),
                    "version_id": r["resource_id"],
                    "published_by": "deployment",
                    "release_note": "保留迁移前的有效引用",
                    "created_at": now,
                    "updated_at": now,
                    "revision": 1,
                }
                for r in batch
            ],
        )
    backfill(connection, tables, conversion)
    connection.execute(
        versions.update()
        .where(
            versions.c.channel_id == conversion.c.channel_id, versions.c.id == conversion.c.old_id
        )
        .values(state="RETIRED")
    )
    # 无环境发布映射的配置统一未发布；历史退役配置不恢复为可管理资源。
    for kind, table_name in KINDS.items():
        parent = tables[table_name]
        connection.execute(
            parent.update()
            .where(
                parent.c.channel_id == versions.c.channel_id,
                parent.c.id == versions.c.id,
                versions.c.resource_type == kind,
                versions.c.state == "RETIRED",
            )
            .values(status="DELETED")
        )
    # 旧停用资源转换为未发布配置，只有后续显式发布才恢复可选状态。
    for kind, table_name in KINDS.items():
        parent = tables[table_name]
        disabled = sa.select(parent.c.id).where(
            parent.c.channel_id == releases.c.channel_id, parent.c.status == "DISABLED"
        )
        connection.execute(
            releases.delete().where(
                releases.c.resource_type == kind, releases.c.resource_id.in_(disabled)
            )
        )
        connection.execute(
            parent.update().where(parent.c.status == "DISABLED").values(status="ACTIVE")
        )
    connection.execute(
        versions.update()
        .where(
            versions.c.id == versions.c.resource_id,
            versions.c.resource_type.in_(KINDS),
            versions.c.state != "RETIRED",
        )
        .values(
            state=sa.case(
                (
                    sa.exists(
                        sa.select(releases.c.id).where(
                            releases.c.channel_id == versions.c.channel_id,
                            releases.c.version_id == versions.c.id,
                        )
                    ),
                    "PUBLISHED",
                ),
                else_="DRAFT",
            )
        )
    )


def backfill(connection, tables, conversion):
    """使用真实上下文、工具调用和已发送模型尝试回填，不将快照白名单当成使用。"""
    connection.execute(
        sa.text("""
        CREATE TEMP TABLE resource_usage_evidence ON COMMIT DROP AS
        SELECT channel_id, environment, run_id, tool_version_id AS version_id, created_at
        FROM tool_calls WHERE state IN ('SUCCEEDED','FAILED','UNKNOWN','CACHED','STARTED')
        UNION ALL
        SELECT t.channel_id,t.environment,t.run_id,t.version_id,MIN(a.sent_at)
        FROM prompt_tests t JOIN attempts a
          ON a.channel_id=t.channel_id AND a.run_id=t.run_id AND a.sent_at IS NOT NULL
        GROUP BY t.channel_id,t.environment,t.run_id,t.version_id
        UNION ALL
        SELECT c.channel_id,c.environment,c.run_id,r.value->>'resource_id',c.created_at
        FROM run_contents c CROSS JOIN LATERAL
          jsonb_array_elements(COALESCE(c.payload->'source_refs','[]')) r(value)
        JOIN resource_versions v ON v.channel_id=c.channel_id
          AND v.id=r.value->>'resource_id' AND v.resource_type='prompt'
        WHERE c.kind LIKE 'inputs:%'
        UNION ALL
        SELECT c.channel_id,c.environment,c.run_id,r.value->>'version_id',c.created_at
        FROM run_contents c CROSS JOIN LATERAL
          jsonb_array_elements(COALESCE(c.payload->'skills'->'loaded','[]')) r(value)
        WHERE c.kind LIKE 'inputs:%'
        UNION ALL
        SELECT c.channel_id,c.environment,c.run_id,v.value->>'version_id',a.started_at
        FROM run_contents c JOIN attempts a ON a.channel_id=c.channel_id AND a.run_id=c.run_id
          AND a.kind='model' AND a.sent_at IS NOT NULL
        CROSS JOIN LATERAL jsonb_array_elements(COALESCE(c.payload->'versions',
          (c.payload->>'payload_json')::jsonb->'versions', '[]')) v(value)
        WHERE c.kind='execution_spec' AND v.value->>'resource_type'='model_route'
          AND EXISTS (SELECT 1 FROM jsonb_array_elements(v.value->'content'->'models') model(value)
            WHERE model.value->>'model_version_id'=a.target_version_id)
    """)
    )
    statement = sa.text("""
        SELECT e.channel_id,e.environment,e.run_id,m.resource_type,m.new_id AS resource_id,
          MIN(e.created_at) AS used_at, r.agent_name, r.purpose,
          COALESCE(a.display_name,s.name) AS caller_name, p.name AS resource_name
        FROM resource_usage_evidence e JOIN resource_conversion m
          ON m.channel_id=e.channel_id AND m.old_id=e.version_id
        JOIN runs r ON r.channel_id=e.channel_id AND r.id=e.run_id
        LEFT JOIN platform_accounts a ON a.channel_id='system' AND a.id=r.actor_id
        LEFT JOIN service_clients s ON s.channel_id=e.channel_id
          AND s.environment=e.environment AND s.id=r.client_id
        JOIN (SELECT channel_id,id,name FROM prompts
          UNION ALL SELECT channel_id,id,name FROM tools
          UNION ALL SELECT channel_id,id,name FROM skills
          UNION ALL SELECT channel_id,id,name FROM model_routes) p
          ON p.channel_id=m.channel_id AND p.id=m.new_id
        GROUP BY e.channel_id,e.environment,e.run_id,m.resource_type,m.new_id,
          r.agent_name,r.purpose,a.display_name,s.name,p.name
    """)
    for batch in batches(connection, statement):
        connection.execute(
            tables["resource_uses"].insert(),
            [
                {
                    "id": digest(
                        [r["channel_id"], r["run_id"], r["resource_type"], r["resource_id"]]
                    ),
                    "channel_id": r["channel_id"],
                    "environment": r["environment"],
                    "revision": 1,
                    "created_at": r["used_at"],
                    "updated_at": r["used_at"],
                    **{
                        key: r[key]
                        for key in (
                            "run_id",
                            "resource_type",
                            "resource_id",
                            "agent_name",
                            "purpose",
                            "caller_name",
                            "resource_name",
                        )
                    },
                }
                for r in batch
            ],
        )


def downgrade():
    raise RuntimeError("资源拆分保留独立内容及关联；回退请恢复升级前备份")
