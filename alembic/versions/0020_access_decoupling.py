"""渠道分类改为可选展示元数据，数据域保持显式映射；不改写既有记录。"""

from sqlalchemy import String

from alembic import op

revision = "0020_access_decoupling"
down_revision = "0016_agents"
branch_labels = None
depends_on = None

COMMENTS = (
    ("channels", "business_type", 32, "接入业务类型", "可选业务分类展示文本，历史分类原值保留"),
    ("data_scopes", "external_scope_type", 64, "外部域类型", "显式配置的外部数据域类型"),
    ("data_scopes", "external_scope_id", 128, "外部域编号", "显式配置的外部数据域编号"),
)


def upgrade() -> None:
    for table, column, length, before, after in COMMENTS:
        op.alter_column(
            table, column, existing_type=String(length), existing_comment=before, comment=after
        )


def downgrade() -> None:
    for table, column, length, before, after in reversed(COMMENTS):
        op.alter_column(
            table, column, existing_type=String(length), existing_comment=after, comment=before
        )
