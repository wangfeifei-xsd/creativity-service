"""06 浏览器联调用的临时渠道服务；退出时清理独立 schema 和 Redis 前缀。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import uvicorn
from alembic.config import Config
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from creativity_service.api.errors import register_error_handlers
from creativity_service.api.middleware import RequestContextMiddleware
from creativity_service.core.config import Settings
from creativity_service.modules.channels.api import router as channels_router
from creativity_service.modules.channels.assembly import build_channel_services
from creativity_service.modules.iam.api import router as iam_router
from creativity_service.modules.iam.schemas import AccountCreate
from creativity_service.modules.runs.assembly import RunLifecycleGuard


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    schema = f"test_ui06_{uuid4().hex}"
    sync_engine = create_engine(settings.database_url.get_secret_value())
    with sync_engine.connect() as connection:
        connection.execute(
            text(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin")
        )
        connection.execute(text(f"USE `{schema}`"))
        config = Config("alembic.ini")
        config.set_main_option("version_table_schema", schema)
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    engine = create_async_engine(
        make_url(settings.async_database_url).set(database=schema),
        isolation_level="READ COMMITTED",
    )
    redis = Redis.from_url(settings.redis_auth_url.get_secret_value(), socket_timeout=2)
    iam, channels = build_channel_services(engine, redis, schema)
    channels.lifecycle.register_tasks(RunLifecycleGuard())
    await channels.channels.initialize_system()
    await iam.accounts.initialize_admin(
        AccountCreate(
            login_name="ui-admin",
            display_name="页面管理员",
            initial_password="Initial-ui-password-1234",
        )
    )
    app.state.iam, app.state.channels, app.state.authentication = iam, channels, iam.authentication
    try:
        yield
    finally:
        keys = [key async for key in redis.scan_iter(f"{schema}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        await engine.dispose()
        with sync_engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE `{schema}`"))
        sync_engine.dispose()


app = FastAPI(lifespan=lifespan)
register_error_handlers(app)
app.add_middleware(RequestContextMiddleware)
app.include_router(iam_router, prefix="/admin/v1")
app.include_router(channels_router, prefix="/admin/v1")


@app.get("/ready")
async def ready() -> dict[str, bool]:
    return {"ready": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18006, access_log=False)
