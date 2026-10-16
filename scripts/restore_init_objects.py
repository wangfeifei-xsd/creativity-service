"""从初始化 SQL 中恢复技能对象；只写入数据库登记且摘要一致的缺失对象。"""

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from alibabacloud_oss_v2.exceptions import ServiceError as OSSServiceError
from sqlalchemy import create_engine, select, text

from creativity_service.core.artifacts import OSSObjectStore
from creativity_service.core.config import Settings
from creativity_service.storage import metadata
from scripts.render_weather_seed import OBJECT_MARKER, object_bytes


def archived_objects(path: Path) -> list[dict[str, Any]]:
    """SQL 注释携带对象原始字节，使单份数据 SQL 保留完整恢复材料。"""
    items = [
        json.loads(line.removeprefix(OBJECT_MARKER))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(OBJECT_MARKER)
    ]
    if not items or len({item["object_key"] for item in items}) != len(items):
        raise ValueError("初始化 SQL 对象清单缺失或重复")
    for item in items:
        object_bytes(item)
    return items


async def restore_objects(
    settings: Settings, items: list[dict[str, Any]], *, check_only: bool
) -> None:
    """只恢复缺失对象；并发写入时仍核验内容，不能覆盖已有文件。"""
    store = OSSObjectStore(settings)
    try:
        for item in items:
            data = object_bytes(item)
            try:
                existing = await store.get(item["object_key"], item["size_bytes"])
            except OSSServiceError as exc:
                if exc.code != "NoSuchKey":
                    raise
                if check_only:
                    raise ValueError("技能对象尚未恢复") from exc
                try:
                    await store.put(
                        item["object_key"], data, item["content_type"], forbid_overwrite=True
                    )
                except OSSServiceError as conflict:
                    if conflict.code != "FileAlreadyExists":
                        raise
                existing = await store.get(item["object_key"], item["size_bytes"])
            if len(existing) != len(data) or hashlib.sha256(existing).hexdigest() != item["sha256"]:
                raise ValueError("目标技能对象内容不同，未覆盖；请核对对象存储环境")
            print(f"技能对象已核验：{item['id']}")
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="校验并恢复初始化 SQL 封存的技能对象")
    parser.add_argument("--sql", type=Path, default=Path("sql/init_data.sql"))
    parser.add_argument("--schema")
    parser.add_argument("--check", action="store_true", help="只校验数据库及对象存储，不写入")
    args = parser.parse_args()
    if args.schema and not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", args.schema):
        parser.error("schema 标识不正确")
    items = archived_objects(args.sql)
    settings = Settings()
    engine = create_engine(settings.database_url.get_secret_value())
    table = metadata.tables["artifacts"]
    try:
        with engine.connect() as connection:
            if args.schema:
                connection.execute(text(f"USE `{args.schema}`"))
            stored = {
                (row["channel_id"], row["id"]): row
                for row in connection.execute(
                    select(table).where(
                        table.c.id.in_([item["id"] for item in items]),
                        table.c.channel_id.in_([item["channel_id"] for item in items]),
                    )
                ).mappings()
            }
        for item in items:
            row = stored.get((item["channel_id"], item["id"]))
            if (
                row is None
                or row["state"] != "AVAILABLE"
                or any(
                    row[key] != item[key]
                    for key in ("environment", "object_key", "content_type", "size_bytes", "sha256")
                )
            ):
                raise ValueError("对象元数据不匹配；请先向目标空库导入完整初始化 SQL")
        asyncio.run(restore_objects(settings, items, check_only=args.check))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
