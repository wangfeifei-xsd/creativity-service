"""统一渠道和环境；撤销数据域并保留环境动作边界及现有配置。"""

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision = "0041_channel_environments"
down_revision = "0040_model_networks"
branch_labels = None
depends_on = None
CHANGES = json.loads(Path(__file__).with_name("0041_schema.json").read_text())
COMMENTS = json.loads(Path(__file__).with_name("0041_comments.json").read_text())
REMOVED_ACTION = "data_scope:manage"
# 本项目尚未上线。已有个人内容或外部删除账本不能通过删除列隐式合并。
# 此类库必须先完成单独的主体冲突与外部账本核验；开发库没有这些记录。
PROTECTED = (
    "conversations",
    "memories",
    "memory_preferences",
    "mcp_oauth_tokens",
    "mcp_oauth_flows",
    "deletion_markers",
    "deletion_receipts",
    "deletion_jobs",
    "deletion_work_items",
    "subject_review_bindings",
    "automation_schedules",
    "webhook_endpoints",
)


def digest(value):
    raw = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def rows(connection, table, *filters):
    """使用游标分批读取，迁移不把历史运行与审计一次装入内存。"""
    statement = sa.select(table).where(*filters).execution_options(yield_per=200)
    yield from connection.execute(statement).mappings()


def write(connection, table, row, values):
    connection.execute(
        sa.update(table)
        .where(table.c.channel_id == row["channel_id"], table.c.id == row["id"])
        .values(**values)
    )


def clean_scope(value):
    if isinstance(value, dict):
        value.pop("data_scope_id", None)


def clean_platform_json(table, column, value):
    """只改平台契约路径；业务输入、输出和工具参数中同名字段原样保留。"""
    result = deepcopy(value)
    if not isinstance(result, dict):
        return result
    if table == "admissions" and column == "snapshot":
        clean_scope(result)
    elif table == "runs" and column == "identity":
        clean_scope(result.get("scope"))
    elif table == "run_contents" and column == "payload":
        clean_scope(result.get("scope"))
    elif table == "model_tests" and column == "execution":
        clean_scope(result.get("configuration", {}).get("scope"))
    elif table == "audit_events" and column == "summary":
        for item in result.get("affected_scopes", []):
            clean_scope(item)
    return result


def migrate_permissions(connection, tables):
    members, grants, environments = (
        tables[n] for n in ("channel_memberships", "resource_grants", "channel_environments")
    )
    channels = tables["channels"]
    accounts = tables["platform_accounts"]
    active_accounts = set(
        connection.scalars(
            sa.select(accounts.c.id).where(
                accounts.c.channel_id == "system", accounts.c.status == "ACTIVE"
            )
        )
    )
    for channel in rows(connection, channels, channels.c.channel_id != "system"):
        cid = channel["channel_id"]
        enabled = {
            r["environment"]
            for r in rows(
                connection,
                environments,
                environments.c.channel_id == cid,
                environments.c.status == "ACTIVE",
            )
        }
        membership = {
            r["user_id"]: dict(r) for r in rows(connection, members, members.c.channel_id == cid)
        }
        grouped = defaultdict(list)
        for original in rows(connection, grants, grants.c.channel_id == cid):
            row = dict(original)
            row["allowed_actions"] = [a for a in row["allowed_actions"] if a != REMOVED_ACTION]
            if row["resource_type"] == "data_scope":
                # 旧工作区仅是环境的配置授权，不能跨越其原环境。
                if not row["id"].startswith("workspace_"):
                    raise RuntimeError("发现非工作区数据域资源授权，请先转换为明确资源和环境授权")
                row.update(resource_type="channel", resource_id=cid)
            grouped[
                (row["grantee_type"], row["grantee_id"], row["resource_type"], row["resource_id"])
            ].append(row)
        for (kind, uid, resource_type, _), group in grouped.items():
            member = membership.get(uid) if kind == "account" else None
            # 账号管理分配的整渠道管理员需要修复旧版本漏同步环境的问题。
            assigned = [r for r in group if r["id"].startswith("administrator_")]
            if (
                member
                and uid in active_accounts
                and member["status"] == "ACTIVE"
                and resource_type == "channel"
                and assigned
            ):
                # 自定义收窄动作的授权不扩展；渠道管理员菜单动作仍由角色实时限制。
                ordinary = set(
                    json.loads(Path(__file__).with_name("0041_actions.json").read_text())
                )
                for r in assigned:
                    if set(r["allowed_actions"]) == ordinary and set(r["environments"]) <= set(
                        member["environments"]
                    ):
                        r["environments"] = sorted(set(r["environments"]) | enabled)
                        member["environments"] = sorted(set(member["environments"]) | enabled)
                        write(connection, members, member, {"environments": member["environments"]})
            by_environment = defaultdict(set)
            for r in group:
                for env in r["environments"]:
                    by_environment[env].update(r["allowed_actions"])
            # 相同动作的环境才能合并；不能把两环境的不同动作做笛卡尔积。
            by_actions = defaultdict(list)
            for env, actions in by_environment.items():
                by_actions[tuple(sorted(actions))].append(env)
            if not by_actions:
                by_actions[tuple(sorted({a for r in group for a in r["allowed_actions"]}))] = []
            preferred = min(
                group,
                key=lambda r: (
                    not r["id"].startswith("initial_"),
                    not r["id"].startswith("administrator_"),
                    r["id"],
                ),
            )
            connection.execute(
                sa.delete(grants).where(
                    grants.c.channel_id == cid, grants.c.id.in_([r["id"] for r in group])
                )
            )
            for number, (actions, envs) in enumerate(
                sorted(by_actions.items(), key=lambda item: (-len(item[1]), item[0]))
            ):
                row = dict(preferred)
                row.update(
                    id=preferred["id"]
                    if number == 0
                    else "environment_" + digest([preferred["id"], envs])[:40],
                    environments=sorted(envs),
                    allowed_actions=list(actions),
                    revision=preferred["revision"] + 1,
                )
                connection.execute(sa.insert(grants).values(**row))
    for name in ("builtin_roles", "custom_roles"):
        if name not in tables:
            continue
        table = tables[name]
        for row in rows(connection, table):
            values = {}
            if REMOVED_ACTION in (row.get("allowed_actions") or []):
                values["allowed_actions"] = [
                    a for a in row["allowed_actions"] if a != REMOVED_ACTION
                ]
            if values:
                write(connection, table, row, values)
    menus = tables["iam_menus"]
    removed = list(
        connection.execute(
            sa.select(menus.c.id).where(
                menus.c.channel_id == "system", menus.c.action_key == REMOVED_ACTION
            )
        ).scalars()
    )
    connection.execute(
        sa.delete(menus).where(menus.c.channel_id == "system", menus.c.action_key == REMOVED_ACTION)
    )
    for name in ("builtin_roles", "custom_roles"):
        if name not in tables:
            continue
        for row in rows(connection, tables[name]):
            if set(row.get("menu_ids") or []) & set(removed):
                write(
                    connection,
                    tables[name],
                    row,
                    {"menu_ids": [m for m in row["menu_ids"] if m not in removed]},
                )


def migrate_versions(connection, tables):
    versions = tables["resource_versions"]
    for channel in rows(connection, tables["channels"]):
        records = {
            r["id"]: dict(r)
            for r in rows(connection, versions, versions.c.channel_id == channel["channel_id"])
        }
        visited = set()

        def visit(identifier, stack, records=records, visited=visited):
            if identifier in visited:
                return
            if identifier in stack:
                raise RuntimeError("版本依赖有环，无法安全重算摘要")
            row = records[identifier]
            dependencies = row["dependencies"] or []
            for dep in dependencies:
                visit(dep, stack | {identifier})
            content = deepcopy(row["content"])
            if row["resource_type"] == "tool":
                content.pop("allowed_data_domains", None)
            if row["resource_type"] == "model_route":
                for model in content.get("models", []):
                    clean_scope(model.get("scope"))
            values = {
                "content": content,
                "content_digest": digest(
                    {"content": content, "output_schema": row["output_schema"]}
                ),
            }
            if dependencies:
                values["dependencies_digest"] = digest(
                    [
                        {k: records[d][k] for k in ("content_digest", "dependencies_digest")}
                        | {"version_id": d}
                        for d in dependencies
                    ]
                )
            if any(row[k] != v for k, v in values.items()):
                write(connection, versions, row, values)
                row.update(values)
            visited.add(identifier)

        for identifier in records:
            visit(identifier, set())


def migrate_barriers(connection, tables):
    barriers = tables["recovery_barriers"]
    for channel in rows(connection, tables["channels"]):
        cid = channel["channel_id"]
        grouped = defaultdict(list)
        for row in rows(connection, barriers, barriers.c.channel_id == cid):
            scope = {k: row[k] for k in ("channel_id", "environment", "subject_type", "subject_id")}
            grouped[digest(["recovery", scope])].append(dict(row))
        for identifier, group in grouped.items():
            row = dict(group[0])
            row["id"] = identifier
            row["revision"] = max(r["revision"] for r in group) + 1
            if any(r["state"] != "READY" for r in group):
                row.update(state="BLOCKED", verified_at=None)
            connection.execute(
                sa.delete(barriers).where(
                    barriers.c.channel_id == cid, barriers.c.id.in_([r["id"] for r in group])
                )
            )
            connection.execute(sa.insert(barriers).values(**row))


def migrate_idempotency(connection, tables):
    table, runs = tables["run_idempotency"], tables["runs"]
    statement = sa.select(table, runs.c.agent_code).select_from(
        table.join(
            runs, sa.and_(table.c.channel_id == runs.c.channel_id, table.c.run_id == runs.c.id)
        )
    )
    occupied = set()
    for row in connection.execute(statement).mappings():
        scope = {k: row[k] for k in ("channel_id", "environment", "subject_type", "subject_id")}
        scope_digest = digest([scope, row["identity_type"], row["identity_id"], row["agent_code"]])
        key = (row["channel_id"], scope_digest, row["key"])
        if key in occupied:
            raise RuntimeError("环境合并后存在重复幂等键，请先核验运行对应关系；事务未提交")
        occupied.add(key)
        write(connection, table, row, {"scope_digest": scope_digest})


def migrate_data():
    connection = op.get_bind()
    tables = sa.MetaData()
    tables.reflect(connection)
    tables = tables.tables
    for name in PROTECTED:
        if connection.execute(sa.select(tables[name].c.id).limit(1)).first():
            raise RuntimeError(
                f"{name} 已有主体内容或持久身份，请先完成环境合并核验后再升级；事务未提交"
            )
    runs = tables["runs"]
    if connection.execute(
        sa.select(runs.c.id)
        .where(runs.c.state.not_in(["FAILED", "SUCCEEDED", "CANCELLED"]))
        .limit(1)
    ).first():
        raise RuntimeError("仍有未结束运行，请结束运行后再升级")
    migrate_permissions(connection, tables)
    migrate_versions(connection, tables)
    migrate_barriers(connection, tables)
    migrate_idempotency(connection, tables)
    for name, column in (
        ("admissions", "snapshot"),
        ("runs", "identity"),
        ("run_contents", "payload"),
        ("model_tests", "execution"),
        ("audit_events", "summary"),
    ):
        table = tables[name]
        for row in rows(connection, table):
            # 输入、结果、步骤内容属于业务原文；只有冻结执行定义的 scope 是平台字段。
            if name == "run_contents" and row["kind"] != "execution_spec":
                continue
            value = clean_platform_json(name, column, row[column])
            if value != row[column]:
                write(connection, table, row, {column: value})


def upgrade():
    if context.is_offline_mode():
        # 离线 SQL 仅用于空库。已有数据的摘要、权限与屏障必须由在线迁移核验。
        op.execute(
            "SELECT 1 / CASE WHEN EXISTS (SELECT 1 FROM channel_memberships) "
            "OR EXISTS (SELECT 1 FROM resource_versions) "
            "OR EXISTS (SELECT 1 FROM recovery_barriers) THEN 0 ELSE 1 END"
        )
    else:
        migrate_data()
    for item in COMMENTS:
        op.alter_column(item["table"], item["column"], comment=item["new"])
    for change in CHANGES:
        if change["drop"]:
            op.drop_table(change["name"])
            continue
        for index in change["old_indexes"]:
            op.drop_index(index["name"], table_name=change["name"])
        for column in change["columns"]:
            op.drop_column(change["name"], column["name"])
        for index in change["new_indexes"]:
            op.create_index(index["name"], change["name"], index["columns"], unique=False)


def column_type(name):
    if name.startswith("VARCHAR("):
        return sa.String(int(name[8:-1]))
    return {
        "JSONB": postgresql.JSONB(),
        "BIGINT": sa.BigInteger(),
        "TIMESTAMP": sa.DateTime(timezone=True),
        "TEXT": sa.Text(),
    }[name]


def downgrade():
    # 已有数据不能伪造撤销的数据域；只能从升级前备份恢复。
    if not context.is_offline_mode():
        connection = op.get_bind()
        if connection.execute(sa.text("SELECT 1 FROM channel_memberships LIMIT 1")).first():
            raise RuntimeError("有数据的范围合并不可逆，请恢复升级前数据库备份")
    for item in COMMENTS:
        op.alter_column(item["table"], item["column"], comment=item["old"])
    for change in reversed(CHANGES):
        columns = [
            sa.Column(c["name"], column_type(c["type"]), nullable=True, comment=c["comment"])
            for c in change["columns"]
        ]
        if change["drop"]:
            op.create_table(change["name"], *columns, comment=change["comment"])
        else:
            for index in change["new_indexes"]:
                op.drop_index(index["name"], table_name=change["name"])
            for column in columns:
                op.add_column(change["name"], column)
        for index in change["old_indexes"]:
            op.create_index(index["name"], change["name"], index["columns"], unique=False)
