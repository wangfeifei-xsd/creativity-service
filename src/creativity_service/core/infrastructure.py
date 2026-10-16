"""外部基础设施连接及只读就绪探测。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from alibabacloud_oss_v2.exceptions import ServiceError as OSSServiceError
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from creativity_service.core.artifacts import OSSObjectStore
from creativity_service.core.config import Settings


class DependencyStatus(BaseModel):
    status: Literal["available", "unavailable"]
    message: str = Field(description="基础设施诊断说明，不含连接凭据")


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, DependencyStatus]


class Infrastructure:
    def __init__(self, settings: Settings) -> None:
        from creativity_service.modules.memory.vector_store import QdrantStore

        self.vectors = QdrantStore(settings)
        self.timeout = settings.health_timeout_seconds
        self.engine = create_async_engine(
            settings.async_database_url,
            isolation_level="READ COMMITTED",
            pool_pre_ping=True,
            connect_args={"connect_timeout": max(1, int(self.timeout))},
        )
        self.redis_clients: dict[str, Redis] = {
            name: Redis.from_url(
                url.get_secret_value(),
                socket_connect_timeout=self.timeout,
                socket_timeout=self.timeout,
            )
            for name, url in (
                ("redis_cache", settings.redis_cache_url),
                ("redis_auth", settings.redis_auth_url),
                ("celery_broker", settings.celery_broker_url),
                ("celery_result", settings.celery_result_url),
            )
        }
        self.object_store = OSSObjectStore(settings)

    async def check_database(self) -> None:
        async with self.engine.connect() as connection:
            version = await connection.scalar(text("SELECT VERSION()"))
            if not str(version).startswith("8."):
                raise RuntimeError("业务数据库要求 MySQL 8")

    async def check_storage(self) -> None:
        await asyncio.to_thread(self.object_store.check)

    async def check_readiness(self) -> ReadinessResponse:
        checks: dict[str, Callable[[], Awaitable[object]]] = {
            "database": self.check_database,
            "vector_store": self.vectors.health,
            **{name: client.ping for name, client in self.redis_clients.items()},
            "object_storage": self.check_storage,
        }

        async def probe(check: Callable[[], Awaitable[object]]) -> DependencyStatus:
            try:
                async with asyncio.timeout(self.timeout):
                    await check()
                return DependencyStatus(status="available", message="连接正常")
            except TimeoutError:
                return DependencyStatus(status="unavailable", message="连接超时")
            except OSSServiceError as exc:
                status = exc.status_code
                message = {403: "对象存储认证失败或无桶访问权限", 404: "对象存储桶不存在"}.get(
                    status or 0, "对象存储暂不可用"
                )
                return DependencyStatus(status="unavailable", message=message)
            except Exception:
                return DependencyStatus(status="unavailable", message="连接失败，请检查服务和凭据")

        results = await asyncio.gather(*(probe(check) for check in checks.values()))
        return ReadinessResponse(
            status="ready" if all(item.status == "available" for item in results) else "not_ready",
            checks=dict(zip(checks, results, strict=True)),
        )

    async def close(self) -> None:
        await self.engine.dispose()
        for client in self.redis_clients.values():
            await client.aclose()
        self.object_store.close()
