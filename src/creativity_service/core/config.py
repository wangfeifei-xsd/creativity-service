"""进程配置加载与启动校验。"""

from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CREATIVITY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    service_name: str = "creativity-service"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_directory: Path = Path("log")
    log_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    log_backup_count: int = Field(default=5, ge=1, le=30)
    database_url: SecretStr
    redis_cache_url: SecretStr
    redis_auth_url: SecretStr
    celery_broker_url: SecretStr
    celery_result_url: SecretStr
    redis_key_prefix: str = Field(default="creativity", pattern=r"^[a-z][a-z0-9_-]*$")
    management_token_ttl: int = Field(default=7200, ge=60, le=86400)
    service_token_ttl: int = Field(default=3600, ge=60, le=86400)
    s3_endpoint_url: str
    s3_region: str = Field(min_length=1)
    s3_access_key_id: SecretStr
    s3_secret_access_key: SecretStr
    s3_bucket: str = Field(min_length=3)
    health_timeout_seconds: float = Field(default=3, gt=0, le=30)
    cors_origins: list[str] = Field(default_factory=list)
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except Exception as exc:
            raise ValueError("数据库连接地址格式不正确") from exc
        if url.drivername != "postgresql+psycopg" or not url.host or not url.database:
            raise ValueError("数据库必须使用 postgresql+psycopg 并指定主机和数据库")
        return value

    @field_validator("s3_endpoint_url", "otel_exporter_otlp_endpoint")
    @classmethod
    def validate_http_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("服务地址必须为不含认证信息的 HTTP 或 HTTPS 地址")
        if url.query or url.fragment:
            raise ValueError("服务地址不能包含查询参数或片段")
        return value.rstrip("/")

    @field_validator("s3_access_key_id", "s3_secret_access_key")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("凭据不能为空")
        return value

    @model_validator(mode="after")
    def validate_services(self) -> Self:
        locations: set[tuple[str, int, int]] = set()
        for value in (
            self.redis_cache_url,
            self.redis_auth_url,
            self.celery_broker_url,
            self.celery_result_url,
        ):
            url = urlsplit(value.get_secret_value())
            if url.scheme not in {"redis", "rediss"} or not url.hostname:
                raise ValueError("Redis 连接地址必须使用 redis 或 rediss")
            if not url.path.removeprefix("/").isdigit() or url.query or url.fragment:
                raise ValueError("Redis 必须显式指定独立数据库编号，不能包含查询参数或片段")
            location = (url.hostname.lower(), url.port or 6379, int(url.path[1:]))
            if location in locations:
                raise ValueError("缓存、认证、任务队列与任务结果必须使用独立 Redis 数据库")
            locations.add(location)
        if self.otel_enabled and not self.otel_exporter_otlp_endpoint:
            raise ValueError("启用追踪导出时必须配置 OTLP HTTP 端点")
        return self
