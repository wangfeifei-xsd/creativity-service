"""进程配置加载与启动校验。"""

from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import SettingsConfigDict
from sqlalchemy.engine import make_url

from creativity_service.core.environment import (
    Environment,
    EnvironmentSettings,
    normalize_environment,
)


class Settings(EnvironmentSettings):
    model_config = SettingsConfigDict(
        env_prefix="CREATIVITY_",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    environment: Environment = "dev"
    service_name: str = "creativity-service"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_directory: Path = Path("log")
    log_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    log_backup_count: int = Field(default=5, ge=1, le=30)
    database_url: SecretStr
    qdrant_uri: str = "http://127.0.0.1:6333"
    qdrant_api_key: SecretStr = SecretStr("")
    qdrant_collection_prefix: str = Field(
        default="creativity_memory", pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,100}$"
    )
    qdrant_timeout_seconds: float = Field(default=10, gt=0, le=60)
    redis_cache_url: SecretStr
    redis_auth_url: SecretStr
    celery_broker_url: SecretStr
    celery_result_url: SecretStr
    redis_key_prefix: str = Field(default="creativity", pattern=r"^[a-z][a-z0-9_-]*$")
    management_token_ttl: int = Field(default=28800, ge=60, le=86400)
    service_token_ttl: int = Field(default=3600, ge=60, le=86400)
    oss_endpoint: str
    oss_region: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    oss_access_key_id: SecretStr
    oss_access_key_secret: SecretStr
    oss_bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
    oss_path_prefix: str = Field(
        default="creativity/", pattern=r"^[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*/$"
    )
    health_timeout_seconds: float = Field(default=10, gt=0, le=30)
    cors_origins: list[str] = Field(default_factory=list)
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None

    @field_validator("environment", mode="before")
    @classmethod
    def validate_environment(cls, value: str) -> Environment:
        return normalize_environment(value)

    @property
    def async_database_url(self) -> str:
        return (
            make_url(self.database_url.get_secret_value())
            .set(drivername="mysql+asyncmy")
            .render_as_string(hide_password=False)
        )

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except Exception as exc:
            raise ValueError("数据库连接地址格式不正确") from exc
        if url.drivername != "mysql+pymysql" or not url.host or not url.database:
            raise ValueError("数据库必须使用 mysql+pymysql 并指定主机和数据库")
        return value

    @field_validator("otel_exporter_otlp_endpoint", "qdrant_uri")
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

    @field_validator("oss_access_key_id", "oss_access_key_secret")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("凭据不能为空")
        return value

    @model_validator(mode="after")
    def validate_services(self) -> Self:
        # 接受地域地址或包含桶名的地址，统一交给 OSS SDK 按虚拟主机方式访问。
        endpoint = self.oss_endpoint.strip().rstrip("/")
        endpoint = endpoint if "://" in endpoint else "https://" + endpoint
        url = urlsplit(endpoint)
        host = (url.hostname or "").removeprefix(self.oss_bucket + ".")
        if (
            url.scheme != "https"
            or url.username
            or url.password
            or url.port is not None
            or url.path
            or url.query
            or url.fragment
            or host
            not in {
                f"oss-{self.oss_region}.aliyuncs.com",
                f"oss-{self.oss_region}-internal.aliyuncs.com",
            }
        ):
            raise ValueError("OSS 必须配置与地域一致的阿里云 HTTPS 端点，不能使用本地存储")
        self.oss_endpoint = f"https://{host}"
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
