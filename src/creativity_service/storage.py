"""应用层汇总已实现模型，公共业务服务不依赖模块实现。"""

from sqlalchemy import MetaData

from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.modules.channels.tables import metadata as channel_metadata
from creativity_service.modules.iam.tables import metadata as iam_metadata

metadata = MetaData()
for source in (core_metadata, iam_metadata, channel_metadata):
    for table in source.tables.values():
        table.to_metadata(metadata)
