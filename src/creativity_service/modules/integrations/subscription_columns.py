"""订阅范围的增量字段；旧迁移继续使用原冻结模型。"""

from typing import Any

from sqlalchemy import Column, MetaData

from creativity_service.core.database.tables import column_type

CLIENT_IDS: dict[str, Any] = {
    "name": "client_ids",
    "type": "jsonb",
    "comment": "订阅的调用服务列表；空列表仅包含配置者运行",
    "required": True,
    "source": "管理配置",
    "sensitivity": "内部",
}


def extend_subscription(metadata: MetaData, table: str) -> None:
    metadata.tables[table].append_column(
        Column(
            "client_ids",
            column_type("jsonb"),
            nullable=True,
            comment=CLIENT_IDS["comment"],
            info=CLIENT_IDS,
        )
    )
