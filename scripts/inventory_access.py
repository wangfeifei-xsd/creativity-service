"""部署人员只读盘点 19 的兼容对象；仅输出配置标识、计数和摘要，不输出凭据或业务原文。"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine, select, text

from creativity_service.core.config import Settings
from creativity_service.storage import metadata


def inventory(connection):
    index = metadata.tables["channel_code_index"]
    channel_ids = sorted(
        set(
            connection.scalars(
                select(index.c.target_channel_id).where(index.c.channel_id == "system")
            )
        )
    )
    result = {"channels": [], "record_fingerprints": {}}
    for channel_id in channel_ids:

        def records(name, channel_id=channel_id):
            table = metadata.tables[name]
            return (
                connection.execute(select(table).where(table.c.channel_id == channel_id))
                .mappings()
                .all()
            )

        channels = records("channels")
        result["channels"].append(
            {
                "channel_id": channel_id,
                "name": channels[0]["name"] if channels else None,
                "business_type": channels[0]["business_type"] if channels else None,
                "data_scopes": [
                    {
                        k: row[k]
                        for k in (
                            "id",
                            "environment",
                            "external_scope_type",
                            "external_scope_id",
                            "status",
                        )
                    }
                    for row in records("data_scopes")
                ],
                "clients": [
                    {k: row[k] for k in ("id", "environment", "status")}
                    for row in records("service_clients")
                ],
                "keys": [
                    {k: row[k] for k in ("id", "client_id", "environment", "status")}
                    for row in records("channel_keys")
                ],
                "legacy_http": [
                    {
                        k: row[k]
                        for k in (
                            "id",
                            "environment",
                            "data_scope_id",
                            "adapter_code",
                            "adapter_version",
                            "allowed_operations",
                            "revision",
                        )
                    }
                    for row in records("integrations")
                ],
                "agent_versions": [
                    {
                        "id": row["id"],
                        "resource_id": row["resource_id"],
                        "entrypoint": (row["content"] or {}).get("entrypoint"),
                        "content_digest": row["content_digest"],
                    }
                    for row in records("resource_versions")
                    if row["resource_type"] == "agent"
                ],
                "run_count": len(records("runs")),
                "delegation_keys": dict(
                    Counter(row["status"] for row in records("delegation_keys"))
                ),
            }
        )
    # 显式枚举控制面目录，再在已列出的渠道范围内核对整行摘要；不导出行内容。
    for table in metadata.tables.values():
        rows = (
            connection.execute(
                select(table).where(table.c.channel_id.in_(["system", *channel_ids]))
            )
            .mappings()
            .all()
        )
        serialized = sorted(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str) for row in rows
        )
        result["record_fingerprints"][table.name] = {
            "count": len(rows),
            "sha256": hashlib.sha256("\n".join(serialized).encode()).hexdigest(),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description="盘点渠道、旧 HTTP 连接、Agent 入口与历史引用")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    engine = create_engine(Settings().database_url.get_secret_value())
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            result = inventory(connection)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(f"已盘点 {len(result['channels'])} 个业务渠道；结果：{args.output}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
