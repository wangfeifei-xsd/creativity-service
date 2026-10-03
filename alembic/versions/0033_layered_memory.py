"""增加记忆后台整理与通用画像属性；已有记忆保持独立来源语义。"""

import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.schema import CreateIndex, CreateTable, DropTable, SetColumnComment, SetTableComment

from alembic import op
from creativity_service.modules.memory.layer_tables import BASELINE, COLUMNS, column, extend
from creativity_service.modules.memory.tables import build_metadata

revision = "0033_layered_memory"
down_revision = "0032_run_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, values in COLUMNS.items():
        for value in values:
            op.add_column(name, column(value))
    op.execute(text("UPDATE memories SET source_mode = 'ANY'"))
    # 已有渠道保留旧属性配置；新渠道采用服务端通用属性，不扩散业务示例。
    attributes = json.loads(Path(__file__).with_name("memory_attributes_v0013.json").read_text())
    literal = json.dumps(attributes, ensure_ascii=False).replace("'", "''")
    op.execute(
        text(
            f"UPDATE memory_policies SET attributes = '{literal}'::jsonb, "
            "consolidation = '{}'::jsonb"
        )
    )
    # 旧渠道可能只使用隐式默认策略，已有内容也须保留其属性编辑契约。
    op.execute(
        text(f"""
        INSERT INTO memory_policies
        (id, channel_id, created_at, updated_at, revision, agent_id, allowed_types,
         write_mode, ttl_seconds, max_items, retrieval_limit, read_enabled,
         suggest_enabled, failure_mode, attributes, consolidation)
        SELECT 'legacy_memory_' || md5(source.channel_id), source.channel_id,
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1, NULL, '["PREFERENCE","FACT"]'::jsonb,
               'EXPLICIT', 15552000, 100, 10, true, true, 'OMIT', '{literal}'::jsonb, '{{}}'::jsonb
        FROM (SELECT DISTINCT channel_id FROM memories
              UNION SELECT DISTINCT channel_id FROM memory_policies) source
        WHERE NOT EXISTS (SELECT 1 FROM memory_policies existing
                          WHERE existing.channel_id = source.channel_id
                          AND existing.agent_id IS NULL)
    """)
    )
    model = build_metadata()
    extend(model)
    for definition in BASELINE:
        table = model.tables[definition["name"]]
        op.execute(CreateTable(table))
        op.execute(SetTableComment(table))
        for item in table.c:
            op.execute(SetColumnComment(item))
        for index in table.indexes:
            op.execute(CreateIndex(index))


def downgrade() -> None:
    model = build_metadata()
    extend(model)
    for definition in reversed(BASELINE):
        op.execute(DropTable(model.tables[definition["name"]]))
    for name, values in COLUMNS.items():
        for value in reversed(values):
            op.drop_column(name, value["name"])
