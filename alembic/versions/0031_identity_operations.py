"""登记渠道角色、告警与账单核查。"""

from sqlalchemy import Column

from alembic import op
from creativity_service.modules.iam.operations_tables import build_metadata

revision = "0031_identity_operations"
down_revision = "0030_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in build_metadata().tables.values():
        op.create_table(
            table.name,
            *(Column(c.name, c.type, nullable=True, comment=c.comment) for c in table.c),
            comment=table.comment,
        )
        for index in sorted(table.indexes, key=lambda value: value.name):
            op.create_index(index.name, table.name, [c.name for c in index.columns], unique=False)


def downgrade() -> None:
    for table in reversed(list(build_metadata().tables.values())):
        for index in sorted(table.indexes, key=lambda value: value.name):
            op.drop_index(index.name, table_name=table.name)
        op.drop_table(table.name)
