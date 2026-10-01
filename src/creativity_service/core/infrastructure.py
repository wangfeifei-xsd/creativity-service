"""外部基础设施连接及只读就绪探测。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from creativity_service.core.config import Settings


class DependencyStatus(BaseModel):
    status: Literal["available", "unavailable"]
    message: str = Field(description="基础设施诊断说明，不含连接凭据")


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, DependencyStatus]


class Infrastructure:
    def __init__(self, settings: Settings) -> None:
        self.timeout = settings.health_timeout_seconds
        self.engine = create_async_engine(
            settings.database_url.get_secret_value(),
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
        self.s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            config=Config(
                connect_timeout=self.timeout,
                read_timeout=self.timeout,
                retries={"max_attempts": 0},
                s3={"addressing_style": "path"},
            ),
        )
        self.bucket = settings.s3_bucket

    async def check_database(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def check_storage(self) -> None:
        await asyncio.to_thread(self.s3.head_bucket, Bucket=self.bucket)

    async def check_readiness(self) -> ReadinessResponse:
        checks: dict[str, Callable[[], Awaitable[object]]] = {
            "database": self.check_database,
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
            except ClientError as exc:
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
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
        self.s3.close()
