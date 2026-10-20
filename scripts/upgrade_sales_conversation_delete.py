"""为现有本地 AI 匹配接入补齐会话删除能力，不轮换凭据或改动业务会话。"""

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from creativity_service.core.config import Settings
from creativity_service.core.context import Scope
from creativity_service.core.database import transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import new_id, utcnow
from creativity_service.modules.channels.repositories import required, save
from creativity_service.modules.channels.services import capabilities
from creativity_service.modules.iam.audit import append_event
from creativity_service.modules.iam.repositories import policy_key
from scripts.render_sales_access import ACCESS_SCOPES

SEED_PATH = Path(__file__).resolve().parents[1] / "sql/sales_recommendation_data.json"


async def upgrade(apply: bool) -> None:
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if settings.environment != "dev" or url.host not in {"localhost", "127.0.0.1"}:
        raise ValueError("本脚本仅用于本机开发库；其他环境须单独审核接入权限")
    seed = json.loads(await asyncio.to_thread(SEED_PATH.read_text))
    key_seed = seed["tables"]["channel_keys"][0]
    if key_seed["environment"] != "dev":
        raise ValueError("初始化配置不是开发环境")
    scope = Scope(channel_id=key_seed["channel_id"], environment="dev")
    targets = [("service_clients", key_seed["client_id"]), ("channel_keys", key_seed["id"])]
    events = [new_id("audit") for _ in targets]
    locks = [policy_key("system"), policy_key(scope.channel_id)]
    locks += [record_key(scope.channel_id, table, identifier) for table, identifier in targets]
    locks += [record_key(scope.channel_id, "audit_events", identifier) for identifier in events]
    engine = create_async_engine(settings.async_database_url, isolation_level="READ COMMITTED")
    try:
        async with transaction(engine, scope, locks) as uow:
            changes = []
            for (table, identifier), event_id in zip(targets, events, strict=True):
                row = await required(uow.connection, table, scope.channel_id, id=identifier)
                if row["environment"] != "dev" or row["status"] != "ACTIVE":
                    raise ValueError("接入记录不是启用的开发配置")
                if table == "channel_keys" and (
                    row["client_id"] != key_seed["client_id"] or row["expires_at"] <= utcnow()
                ):
                    raise ValueError("Key 归属或有效期已变化")
                if frozenset(row["scopes"]) not in {
                    ACCESS_SCOPES,
                    ACCESS_SCOPES - {"content:delete"},
                }:
                    raise ValueError("现有能力与 AI 匹配归档不符，请先核查")
                if "content:delete" not in row["scopes"]:
                    changes.append((table, row, event_id))
            for table, row, event_id in changes:
                print(f"{'更新' if apply else '待更新'}：{row['name']} → 增加会话删除能力")
                if apply:
                    scopes = sorted({*row["scopes"], "content:delete"})
                    capabilities(scopes)
                    await save(uow, table, row["id"], {"scopes": scopes}, row["revision"])
                    await append_event(
                        uow,
                        event_id,
                        "local_ai_match_upgrade",
                        "ai_match_delete_upgrade",
                        "client:update" if table == "service_clients" else "key:update",
                        "client" if table == "service_clients" else "key",
                        row["id"],
                        ["scopes"],
                        target_name=row["name"],
                    )
            if not changes:
                print("当前接入已具备会话删除能力，无需更新")
        print("本机开发配置核对完成；凭据、有效期、租户与用户隔离保持原值")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="执行已核对的本机开发配置更新，默认只预览"
    )
    asyncio.run(upgrade(parser.parse_args().apply))
