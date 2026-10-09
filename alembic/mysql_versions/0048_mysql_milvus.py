"""MySQL 8 全量冻结基线；旧 PostgreSQL 迁移不在本迁移链执行。"""

import json
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0048_mysql_milvus"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    snapshot = json.loads(Path(__file__).with_name("0048_schema.json").read_text())
    for table in snapshot:
        for statement in table["ddl"]:
            op.execute(statement)
    slots = sa.table(
        "transaction_lock_slots",
        sa.column("channel_id", sa.String(64)),
        sa.column("slot", sa.Integer()),
    )
    for start in range(0, 12288, 256):
        op.bulk_insert(
            slots, [{"channel_id": "system", "slot": i} for i in range(start, start + 256)]
        )
    for statement in json.loads(Path(__file__).with_name("0048_seed.json").read_text()):
        op.execute(statement)


def downgrade() -> None:
    snapshot = json.loads(Path(__file__).with_name("0048_schema.json").read_text())
    for table in reversed(snapshot):
        op.drop_table(table["name"])
