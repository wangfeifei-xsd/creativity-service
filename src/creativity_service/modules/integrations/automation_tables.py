"""通用调度、批量运行与事件投递冻结模型。"""

import json
from pathlib import Path

from sqlalchemy import Column, Index, MetaData

from creativity_service.core.database.soft_delete import soft_delete_table
from creativity_service.core.database.tables import column_type
from creativity_service.modules.integrations.subscription_columns import extend_subscription

BASELINE = json.loads(
    Path(__file__).with_name("automation_tables_v0041_0.json").read_text(encoding="utf-8")
)


def build_metadata() -> MetaData:
    result = MetaData()
    for definition in BASELINE:
        table = soft_delete_table(
            definition["name"],
            result,
            *(
                Column(
                    c["name"], column_type(c["type"]), nullable=True, comment=c["comment"], info=c
                )
                for c in definition["columns"]
            ),
            comment=definition["comment"],
            info=definition,
        )
        for number, columns in enumerate(definition["indexes"]):
            Index(f"ix_{table.name}_{number}", *(table.c[name] for name in columns))
    return result


metadata = build_metadata()
extend_subscription(metadata, "webhook_endpoints")

# 计数与结果游标由调度服务在同一计划锁内维护，旧冻结迁移不追加新字段。
for field in (
    {"name": "consecutive_failures", "type": "integer", "comment": "定时计划连续失败次数"},
    {"name": "outcome_cursor", "type": "jsonb", "comment": "按触发顺序已统计的条目游标"},
):
    definition = {**field, "required": True, "source": "调度服务", "sensitivity": "内部"}
    metadata.tables["automation_schedules"].append_column(
        Column(
            field["name"],
            column_type(field["type"]),
            nullable=True,
            comment=field["comment"],
            info=definition,
        )
    )
items = metadata.tables["automation_items"]
Index(
    "ix_automation_items_schedule_outcome",
    items.c.channel_id,
    items.c.schedule_id,
    items.c.created_at,
    items.c.id,
)
