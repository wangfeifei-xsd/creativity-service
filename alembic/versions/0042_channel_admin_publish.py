"""渠道管理员默认拥有已授权环境的发布权，保留受限授权和其他独立动作。"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import context, op

revision = "0042_channel_admin_publish"
down_revision = "0041_channel_environments"
branch_labels = None
depends_on = None
ACTION = "release:publish"


def table(name, *columns):
    return sa.table(
        name,
        *(
            sa.column(
                c,
                JSONB()
                if c in {"allowed_actions", "roles", "environments", "menu_ids", "summary"}
                else sa.Integer()
                if c == "revision"
                else sa.DateTime(timezone=True)
                if c == "updated_at"
                else sa.String(),
            )
            for c in columns
        ),
    )


def audit(row, target_type, changes, now):
    channel_id = row["channel_id"]
    return dict(
        id="audit_"
        + hashlib.sha256(f"{revision}:{channel_id}:{row['id']}".encode()).hexdigest()[:48],
        channel_id=channel_id,
        created_at=now,
        updated_at=now,
        revision=1,
        environment="control",
        subject_type=None,
        subject_id=None,
        actor_id="deployment",
        action="role:edit" if target_type == "builtin_role" else "grant:put",
        target_type=target_type,
        target_id=row["id"],
        request_id=revision,
        outcome="SUCCESS",
        summary={
            "migration": revision,
            "fields": changes,
            "added_actions": [ACTION],
            "affected_scopes": [{"environment": e} for e in row.get("environments", [])],
        },
    )


def upgrade():
    roles = table(
        "builtin_roles",
        "id",
        "channel_id",
        "role_code",
        "allowed_actions",
        "menu_ids",
        "revision",
        "updated_at",
    )
    if context.is_offline_mode():
        # 离线升级只支持无成员的初始库；已有授权须在线按来源及动作核验。
        op.execute(
            "SELECT 1 / CASE WHEN EXISTS (SELECT 1 FROM channel_memberships) THEN 0 ELSE 1 END"
        )
        seed = json.loads(
            (
                Path(__file__).parents[2]
                / "src/creativity_service/modules/iam/role_seed_v0041.json"
            ).read_text()
        )
        actions = next(r["allowed_actions"] for r in seed if r["role_code"] == "channel_admin")
        op.execute(
            roles.update()
            .where(roles.c.channel_id == "system", roles.c.role_code == "channel_admin")
            .values(
                allowed_actions=sa.cast(
                    sa.literal(json.dumps(sorted(set(actions) | {ACTION}))), JSONB
                ),
                revision=roles.c.revision + 1,
            )
        )
        return
    connection = op.get_bind()
    now = datetime.now(UTC)
    audit_table = sa.Table("audit_events", sa.MetaData(), autoload_with=connection)
    role = (
        connection.execute(
            sa.select(roles).where(
                roles.c.channel_id == "system", roles.c.role_code == "channel_admin"
            )
        )
        .mappings()
        .one()
    )
    ordinary = set(role["allowed_actions"]) - {ACTION}
    menus = table("iam_menus", "id", "channel_id", "action_key")
    buttons = list(
        connection.scalars(
            sa.select(menus.c.id).where(
                menus.c.channel_id == "system", menus.c.action_key == ACTION
            )
        )
    )
    menu_ids = None if role["menu_ids"] is None else sorted(set(role["menu_ids"]) | set(buttons))
    connection.execute(
        roles.update()
        .where(roles.c.channel_id == "system", roles.c.id == role["id"])
        .values(
            allowed_actions=sorted(ordinary | {ACTION}),
            menu_ids=menu_ids,
            revision=role["revision"] + 1,
            updated_at=now,
        )
    )
    events = [audit(role, "builtin_role", ["allowed_actions", "menu_ids"], now)]
    grants = table(
        "resource_grants",
        "id",
        "channel_id",
        "grantee_type",
        "grantee_id",
        "resource_type",
        "resource_id",
        "allowed_actions",
        "environments",
        "revision",
        "updated_at",
    )
    members = table("channel_memberships", "id", "channel_id", "user_id", "roles", "environments")
    statement = (
        sa.select(
            grants,
            members.c.id.label("member_id"),
            members.c.roles,
            members.c.environments.label("member_environments"),
        )
        .select_from(
            grants.join(
                members,
                sa.and_(
                    grants.c.channel_id == members.c.channel_id,
                    grants.c.grantee_id == members.c.user_id,
                ),
            )
        )
        .where(
            grants.c.channel_id != "system",
            grants.c.grantee_type == "account",
            grants.c.resource_type == "channel",
            grants.c.resource_id == grants.c.channel_id,
            members.c.roles.contains(["channel_admin"]),
        )
        .order_by(grants.c.channel_id, grants.c.id)
        .limit(200)
    )
    cursor = ("", "")
    while True:
        batch = (
            connection.execute(
                statement.where(sa.tuple_(grants.c.channel_id, grants.c.id) > cursor)
            )
            .mappings()
            .all()
        )
        if not batch:
            break
        changes = []
        for row in batch:
            encoded = json.dumps(
                [row["channel_id"], row["grantee_id"]],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            assigned_id = "administrator_" + hashlib.sha256(encoded.encode()).hexdigest()[:40]
            # 只补齐系统分配的完整管理员授权；手工资源授权及收窄过的动作保持原样。
            if (
                row["id"] not in {"initial_" + row["member_id"], assigned_id}
                or ACTION in row["allowed_actions"]
                or not ordinary <= set(row["allowed_actions"])
            ):
                continue
            if not set(row["environments"]) <= set(row["member_environments"]):
                continue
            changes.append(
                dict(
                    target_channel=row["channel_id"],
                    target_id=row["id"],
                    allowed_actions=sorted(set(row["allowed_actions"]) | {ACTION}),
                    revision=row["revision"] + 1,
                )
            )
            events.append(audit(row, "resource_grant", ["allowed_actions"], now))
        if changes:
            connection.execute(
                grants.update()
                .where(
                    grants.c.channel_id == sa.bindparam("target_channel"),
                    grants.c.id == sa.bindparam("target_id"),
                )
                .values(
                    allowed_actions=sa.bindparam("allowed_actions"),
                    revision=sa.bindparam("revision"),
                    updated_at=now,
                ),
                changes,
            )
        if events:
            connection.execute(audit_table.insert(), events)
            events.clear()
        cursor = (batch[-1]["channel_id"], batch[-1]["id"])
    if events:
        connection.execute(audit_table.insert(), events)


def downgrade():
    # 有成员时不能区分后续显式授予的发布权，回退应恢复升级前备份。
    connection = op.get_bind()
    if (
        not context.is_offline_mode()
        and connection.execute(sa.text("SELECT 1 FROM channel_memberships LIMIT 1")).first()
    ):
        raise RuntimeError("已有成员的发布授权升级请通过备份恢复")
    seed = json.loads(
        (
            Path(__file__).parents[2] / "src/creativity_service/modules/iam/role_seed_v0041.json"
        ).read_text()
    )
    actions = next(r["allowed_actions"] for r in seed if r["role_code"] == "channel_admin")
    roles = table("builtin_roles", "channel_id", "role_code", "allowed_actions")
    op.execute(
        roles.update()
        .where(roles.c.channel_id == "system", roles.c.role_code == "channel_admin")
        .values(allowed_actions=sa.cast(sa.literal(json.dumps(actions)), JSONB))
    )
