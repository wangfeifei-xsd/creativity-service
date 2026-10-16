"""技能包使用通用上传文件地址，保留历史文件标识供旧配置读取。"""

import sqlalchemy as sa

from alembic import context, op

revision = "0052_file_urls"
down_revision = "0051_schedule_failures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = None if context.is_offline_mode() else sa.inspect(op.get_bind())
    columns = (
        set() if inspector is None else {c["name"] for c in inspector.get_columns("skill_files")}
    )
    if "package_url" not in columns:
        op.add_column(
            "skill_files",
            sa.Column("package_url", sa.String(2048), nullable=True, comment="技能归档包文件地址"),
        )


def downgrade() -> None:
    op.drop_column("skill_files", "package_url")
