"""管理菜单与角色扩展，保留旧迁移定义不变。"""

import json
from pathlib import Path

from sqlalchemy import Column, Index, MetaData, Table
from sqlalchemy.dialects.postgresql import JSONB

from creativity_service.core.database.tables import column_type
from creativity_service.core.database.tables import metadata as core
from creativity_service.modules.iam.operations_tables import metadata as operations
from creativity_service.modules.iam.tables import metadata as identity

metadata = MetaData()
for definition in json.loads(Path(__file__).with_name("baseline_v0035.json").read_text()):
    table = Table(
        definition["name"],
        metadata,
        *(
            Column(c["name"], column_type(c["type"]), nullable=True, comment=c["comment"], info=c)
            for c in definition["columns"]
        ),
        comment=definition["comment"],
        info=definition,
    )
    for number, columns in enumerate(definition["indexes"]):
        Index(f"ix_{table.name}_{number}", *(table.c[name] for name in columns))

operations.tables["custom_roles"].append_column(
    Column(
        "menu_ids",
        JSONB(),
        nullable=True,
        comment="可见菜单节点清单，空值沿用按动作生成",
        info={"required": False, "source": "受信上下文与服务层校验", "sensitivity": "内部"},
    )
)
operations.tables["custom_roles"].comment = "平台与渠道自定义角色"
Index(
    "ix_platform_accounts_directory",
    *(
        identity.tables["platform_accounts"].c[name]
        for name in ("channel_id", "status", "login_name", "id")
    ),
)
Index(
    "ix_audit_events_directory",
    *(core.tables["audit_events"].c[name] for name in ("channel_id", "created_at", "id")),
)
