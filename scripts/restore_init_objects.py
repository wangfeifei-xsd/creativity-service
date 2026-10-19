"""从初始化 SQL 中恢复技能对象；只写入数据库登记且摘要一致的缺失对象。"""

import argparse
import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alibabacloud_oss_v2.exceptions import ServiceError as OSSServiceError
from sqlalchemy import Connection, Engine, create_engine, select
from sqlalchemy.engine import make_url

from creativity_service.core.artifacts import OSSObjectStore
from creativity_service.core.config import Settings
from creativity_service.core.primitives import digest
from creativity_service.storage import metadata
from scripts.render_archive import OBJECT_MARKER, object_bytes
from scripts.render_sales_seed import validate_url_objects


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
                        item["object_key"],
                        data,
                        item["content_type"],
                        forbid_overwrite=True,
                        public=item.get("kind") == "skill_package",
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


def database_objects(
    connection: Connection, items: list[dict[str, Any]], urls: dict[str, str], *, lock: bool = False
) -> dict[str, Any]:
    """只接受数据库中已登记的归档对象，文件 URL 可为采集地址或本次部署地址。"""
    legacy = [item for item in items if item.get("kind") != "skill_package"]
    packages = [item for item in items if item.get("kind") == "skill_package"]
    index = {}
    for name, selected, key in (
        ("artifacts", legacy, "id"),
        ("resource_versions", packages, "id"),
        ("skill_files", packages, "version_id"),
    ):
        table = metadata.tables[name]
        query = select(table).where(
            table.c[key].in_([i["id"] for i in selected]),
            table.c.channel_id.in_([i["channel_id"] for i in selected]),
            table.c.is_deleted.is_(False),
        )
        if lock:
            query = query.with_for_update()
        index[name] = {row["id"]: dict(row) for row in connection.execute(query).mappings()}
    for item in legacy:
        row = index["artifacts"].get(item["id"])
        if (
            row is None
            or row["state"] != "AVAILABLE"
            or any(
                row[key] != item[key]
                for key in (
                    "channel_id",
                    "environment",
                    "object_key",
                    "content_type",
                    "size_bytes",
                    "sha256",
                )
            )
        ):
            raise ValueError("对象元数据不匹配；请先向目标空库导入完整初始化 SQL")
    adjusted = []
    for item in packages:
        version = index["resource_versions"].get(item["id"], {})
        content = version.get("content", {})
        if (
            version.get("resource_type") != "skill"
            or version.get("resource_id") != item["id"]
            or version.get("state") != "PUBLISHED"
            or version.get("channel_id") != item["channel_id"]
            or content.get("package_url") not in (item["package_url"], urls[item["id"]])
            or version.get("content_digest")
            != digest({"content": content, "output_schema": version.get("output_schema")})
        ):
            raise ValueError("技能配置与归档不匹配，未修改对象或文件地址")
        adjusted.append({**item, "package_url": content["package_url"]})
    validate_url_objects(adjusted, index)
    return index


async def restore_archive(
    settings: Settings, engine: Engine, items: list[dict[str, Any]], *, check_only: bool
) -> None:
    """先验证元数据，再恢复对象；换桶时只在尚无运行的初始化库更新技能地址。"""
    store = OSSObjectStore(settings)
    try:
        urls = {
            i["id"]: store.public_url(i["object_key"])
            for i in items
            if i.get("kind") == "skill_package"
        }
    finally:
        store.close()

    def pending(connection: Connection, *, lock: bool = False) -> dict[str, Any]:
        index = database_objects(connection, items, urls, lock=lock)
        changed = {
            key: row
            for key, row in index["resource_versions"].items()
            if row["content"]["package_url"] != urls[key]
        }
        if changed:
            if check_only:
                raise ValueError("技能文件地址尚未适配当前对象存储，请先执行恢复命令")
            if connection.execute(select(metadata.tables["runs"].c.id).limit(1)).first():
                raise ValueError("已有运行的数据库不能通过初始化脚本改写技能文件地址")
        return changed

    with engine.connect() as connection:
        pending(connection)
    await restore_objects(settings, items, check_only=check_only)
    if check_only:
        return
    with engine.begin() as connection:
        # 外部读写完成后重新加锁核对，防止恢复期间配置被修改而遭到覆盖。
        changed = pending(connection, lock=True)
        versions, files = metadata.tables["resource_versions"], metadata.tables["skill_files"]
        for key, row in changed.items():
            content = {**row["content"], "package_url": urls[key]}
            connection.execute(
                versions.update()
                .where(
                    versions.c.id == key,
                    versions.c.channel_id == row["channel_id"],
                    versions.c.is_deleted.is_(False),
                )
                .values(
                    content=content,
                    content_digest=digest(
                        {"content": content, "output_schema": row["output_schema"]}
                    ),
                    revision=versions.c.revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            connection.execute(
                files.update()
                .where(
                    files.c.version_id == key,
                    files.c.channel_id == row["channel_id"],
                    files.c.is_deleted.is_(False),
                )
                .values(
                    package_url=urls[key],
                    revision=files.c.revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
        database_objects(connection, items, urls)


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
    url = make_url(settings.database_url.get_secret_value())
    engine = create_engine(url.set(database=args.schema) if args.schema else url)
    try:
        asyncio.run(restore_archive(settings, engine, items, check_only=args.check))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
