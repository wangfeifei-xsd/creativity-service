"""本地依赖探测与按需启动；已运行但认证失败的服务不能被当成缺失服务。"""

import ipaddress
import json
import logging
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.engine import URL

from creativity_service.core.config import Settings
from creativity_service.core.migrations import MIGRATION_LOCK_KEY

logger = logging.getLogger(__name__)
REDIS_FIELDS = ("redis_cache_url", "redis_auth_url", "celery_broker_url", "celery_result_url")


class StartupError(RuntimeError):
    """可直接展示的本地启动错误，不携带连接密码或底层异常原文。"""


def is_local(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def ensure_endpoint(
    label: str,
    host: str,
    port: int,
    standard_port: int,
    probe: Callable[[str, int], None],
    start: Callable[[int], None],
    *,
    check_only: bool = False,
) -> tuple[str, int]:
    try:
        probe(host, port)
    except Exception:
        if port_open(host, port):
            raise StartupError(
                f"{label} 的 {host}:{port} 已有服务，但连接校验失败；请检查凭据、数据库和服务类型。"
            ) from None
        if not is_local(host):
            raise StartupError(f"配置的 {label} 不可达；请检查远端服务或连接配置。") from None
    else:
        logger.info("复用 %s：%s:%s", label, host, port)
        return host, port

    # 配置地址没有服务时，检查本机常用端口；只有同一组凭据和数据库验证通过才复用。
    candidates = [(address, port) for address in ("127.0.0.1", "::1") if address != host]
    if port != standard_port:
        candidates += [(address, standard_port) for address in ("127.0.0.1", "::1")]
    for candidate_host, candidate_port in candidates:
        if not port_open(candidate_host, candidate_port):
            continue
        try:
            probe(candidate_host, candidate_port)
        except Exception:
            raise StartupError(
                f"本机 {candidate_port} 已有 {label} 候选服务，但当前配置无法使用；"
                "请修改 .env 后重试，避免重复启动。"
            ) from None
        logger.info("复用本机 %s：%s:%s", label, candidate_host, candidate_port)
        return candidate_host, candidate_port

    if check_only:
        raise StartupError(f"{label} 尚未启动：{host}:{port}")
    start(port)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            probe("127.0.0.1", port)
            logger.info("%s 已就绪：127.0.0.1:%s", label, port)
            return "127.0.0.1", port
        except Exception:
            time.sleep(0.5)
    raise StartupError(f"{label} 启动后连接校验仍失败，请检查 log/launcher.log 和本地配置。")


def redis_address(value: str, host: str, port: int) -> str:
    parsed = urlsplit(value)
    credentials = parsed.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed.netloc else ""
    address = f"[{host}]" if ":" in host else host
    return urlunsplit(parsed._replace(netloc=f"{credentials}{address}:{port}"))


class DockerRuntime:
    def __init__(self, root: Path, settings: Settings) -> None:
        self.root = root
        self.settings = settings
        self.ready = False
        self.compose_command: list[str] = []
        self.environment = os.environ.copy()
        database = make_url(settings.database_url.get_secret_value())
        self.environment.update(
            DEV_POSTGRES_USER=database.username or "",
            DEV_POSTGRES_PASSWORD=database.password or "",
            DEV_POSTGRES_DB=database.database or "",
            DEV_POSTGRES_PORT=str(database.port or 5432),
            CREATIVITY_S3_ACCESS_KEY_ID=settings.s3_access_key_id.get_secret_value(),
            CREATIVITY_S3_SECRET_ACCESS_KEY=settings.s3_secret_access_key.get_secret_value(),
            CREATIVITY_S3_BUCKET=settings.s3_bucket,
        )

    def run(
        self, args: list[str], *, timeout: int = 120, executable: list[str] | None = None
    ) -> str:
        try:
            result = subprocess.run(
                [*(executable or ["docker"]), *args],
                cwd=self.root,
                env=self.environment,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise StartupError(
                "Docker 命令无法完成，请检查 Docker 是否已启动及镜像网络。"
            ) from None
        output = result.stdout + result.stderr
        for secret in (
            self.environment["DEV_POSTGRES_PASSWORD"],
            self.environment["CREATIVITY_S3_SECRET_ACCESS_KEY"],
            self.environment.get("DEV_REDIS_PASSWORD", ""),
        ):
            if secret:
                output = output.replace(secret, "***")
        if result.returncode:
            logger.error("Docker 执行失败：%s", output.strip())
            raise StartupError("Docker 执行失败，详情见启动日志。")
        if output.strip():
            logger.debug("Docker 执行结果：%s", output.strip())
        return result.stdout.strip()

    def ensure_ready(self) -> None:
        if self.ready:
            return
        if shutil.which("docker") is None:
            raise StartupError("缺少 Docker；请安装并启动 Docker，或在 .env 配置现有依赖。")
        self.run(["info", "--format", "{{.ServerVersion}}"], timeout=10)
        plugin = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, timeout=10, check=False
        )
        if plugin.returncode == 0:
            self.compose_command = ["docker", "compose"]
        else:
            standalone = shutil.which("docker-compose")
            bundled = self.root.parent / ".tools/docker/docker-compose"
            if standalone:
                self.compose_command = [standalone]
            elif bundled.is_file() and os.access(bundled, os.X_OK):
                self.compose_command = [str(bundled)]
            else:
                raise StartupError("缺少 Docker Compose v2，请安装插件或 docker-compose 命令。")
            self.run(["version"], executable=self.compose_command, timeout=10)
        self.ready = True

    def compose(self, args: list[str], *, timeout: int = 120) -> str:
        self.ensure_ready()
        return self.run(
            [
                "--env-file",
                str(self.root / ".env"),
                "-f",
                "deploy/compose.dev.yml",
                "-f",
                "deploy/compose.vector.yml",
                *args,
            ],
            timeout=timeout,
            executable=self.compose_command,
        )

    def start(self, service: str, port: int, *, replace_existing: bool = False) -> None:
        variables = {
            "postgres": "DEV_POSTGRES_PORT",
            "redis": "DEV_REDIS_PORT",
            "minio": "DEV_S3_PORT",
        }
        self.environment[variables[service]] = str(port)
        if not replace_existing and self.compose(["ps", "--status", "running", "-q", service]):
            raise StartupError(
                f"项目 Docker 的 {service} 已运行，但当前地址无法连接；请检查端口映射和 .env。"
            )
        logger.info("按需启动 Docker 服务：%s", service)
        if service == "postgres":
            # 先完成向量镜像构建，再启动或替换容器，沿用同一 Compose 数据卷。
            self.compose(["build", "postgres"], timeout=600)
        self.compose(["up", "-d", "--no-deps", "--wait", "--wait-timeout", "60", service])

    def owns_database(self, database: URL) -> bool:
        if not is_local(database.host or ""):
            return False
        try:
            container = self.compose(["ps", "-a", "-q", "postgres"])
            if not container or "\n" in container:
                return False
            # 用数据库集群标识确认容器确实对应当前连接，不能只凭端口推断归属。
            engine = create_engine(database, connect_args={"connect_timeout": 3})
            try:
                with engine.connect() as connection:
                    identity = str(
                        connection.scalar(text("SELECT system_identifier FROM pg_control_system()"))
                    )
                    major = int(connection.scalar(text("SHOW server_version_num"))) // 10000
            finally:
                engine.dispose()
            actual = self.run(
                [
                    "exec",
                    container,
                    "sh",
                    "-ec",
                    'psql -X -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
                    '-c "SELECT system_identifier FROM pg_control_system()"',
                ]
            )
            mounts = self.run(["inspect", "--format", "{{json .Mounts}}", container])
            persistent = any(
                item["Type"] == "volume" and item["Destination"] == "/var/lib/postgresql/data"
                for item in json.loads(mounts)
            )
            return major == 17 and persistent and actual == identity
        except Exception:
            return False


def prepare_database(settings: Settings, docker: DockerRuntime, *, check_only: bool) -> Settings:
    database = make_url(settings.database_url.get_secret_value())

    def probe(host: str, port: int) -> None:
        engine = create_engine(
            database.set(host=host, port=port), connect_args={"connect_timeout": 3}
        )
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        finally:
            engine.dispose()

    host, port = ensure_endpoint(
        "PostgreSQL",
        database.host or "",
        database.port or 5432,
        5432,
        probe,
        lambda port: docker.start("postgres", port),
        check_only=check_only,
    )
    database = database.set(host=host, port=port)
    prepare_vector(database, docker, check_only=check_only)
    return settings.model_copy(
        update={"database_url": SecretStr(database.render_as_string(hide_password=False))}
    )


def prepare_vector(database: URL, docker: DockerRuntime, *, check_only: bool) -> None:
    engine = create_engine(database, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            available = connection.scalar(
                text("SELECT default_version FROM pg_available_extensions WHERE name='vector'")
            )
            installed = connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            )
    finally:
        engine.dispose()
    if not installed and check_only:
        raise StartupError("当前 PostgreSQL 尚未启用 pgvector。")
    if not available:
        if not docker.owns_database(database):
            raise StartupError(
                "现有 PostgreSQL 未安装 pgvector；请为该实例安装扩展后重试，不会另建数据库。"
            )
        logger.info("为现有开发数据库补充 pgvector，保留原数据卷")
        docker.start("postgres", database.port or 5432, replace_existing=True)
    engine = create_engine(database, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            if not installed:
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
                )
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            schema = connection.scalar(
                text(
                    "SELECT n.nspname FROM pg_extension e "
                    "JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='vector'"
                )
            )
            quoted = connection.dialect.identifier_preparer.quote_schema(str(schema))
            distance = connection.scalar(
                text(
                    f"SELECT '[1,0]'::{quoted}.vector "
                    f"OPERATOR({quoted}.<=>) '[1,0]'::{quoted}.vector"
                )
            )
            if distance != 0:
                raise StartupError("pgvector 查询校验失败。")
    except StartupError:
        raise
    except Exception:
        raise StartupError("pgvector 初始化或查询失败，请检查扩展与数据库权限。") from None
    finally:
        engine.dispose()
    logger.info("复用 PostgreSQL 中的 pgvector，向量查询正常")


def prepare_redis(settings: Settings, docker: DockerRuntime, *, check_only: bool) -> Settings:
    groups: dict[tuple[str, int], list[str]] = {}
    for name in REDIS_FIELDS:
        parsed = urlsplit(getattr(settings, name).get_secret_value())
        groups.setdefault((parsed.hostname or "", parsed.port or 6379), []).append(name)
    updates: dict[str, SecretStr] = {}
    for (host, port), names in groups.items():

        def probe(target_host: str, target_port: int, names: list[str] = names) -> None:
            for name in names:
                with Redis.from_url(
                    redis_address(
                        getattr(settings, name).get_secret_value(), target_host, target_port
                    ),
                    socket_connect_timeout=2,
                    socket_timeout=2,
                ) as client:
                    client.ping()

        def start(target_port: int, names: list[str] = names) -> None:
            parsed = [urlsplit(getattr(settings, name).get_secret_value()) for name in names]
            passwords = {item.password or "" for item in parsed}
            if len(passwords) != 1 or any(
                item.scheme != "redis" or item.username not in (None, "", "default")
                for item in parsed
            ):
                raise StartupError("自定义 Redis TLS/ACL 配置需使用已有实例，请先检查 .env。")
            docker.environment["DEV_REDIS_PASSWORD"] = unquote(passwords.pop())
            docker.start("redis", target_port)

        target_host, target_port = ensure_endpoint(
            "Redis",
            host,
            port,
            6379,
            probe,
            start,
            check_only=check_only,
        )
        for name in names:
            updates[name] = SecretStr(
                redis_address(getattr(settings, name).get_secret_value(), target_host, target_port)
            )
    return settings.model_copy(update=updates)


def prepare_storage(settings: Settings, docker: DockerRuntime, *, check_only: bool) -> Settings:
    parsed = urlsplit(settings.s3_endpoint_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    def probe(host: str, target_port: int) -> None:
        endpoint = urlunsplit(
            parsed._replace(netloc=f"{'[' + host + ']' if ':' in host else host}:{target_port}")
        )
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            config=BotoConfig(
                connect_timeout=2,
                read_timeout=2,
                retries={"max_attempts": 0},
                s3={"addressing_style": "path"},
            ),
        )
        try:
            try:
                client.head_bucket(Bucket=settings.s3_bucket)
            except ClientError as exc:
                if (
                    exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404
                    or check_only
                    or not is_local(host)
                ):
                    raise
                client.create_bucket(Bucket=settings.s3_bucket)
                client.head_bucket(Bucket=settings.s3_bucket)
        finally:
            client.close()

    host, port = ensure_endpoint(
        "对象存储",
        parsed.hostname or "",
        port,
        9000,
        probe,
        lambda port: docker.start("minio", port),
        check_only=check_only,
    )
    endpoint = urlunsplit(
        parsed._replace(netloc=f"{'[' + host + ']' if ':' in host else host}:{port}")
    )
    return settings.model_copy(update={"s3_endpoint_url": endpoint})


def prepare_dependencies(root: Path, settings: Settings, *, check_only: bool = False) -> Settings:
    docker = DockerRuntime(root, settings)
    settings = prepare_database(settings, docker, check_only=check_only)
    settings = prepare_redis(settings, docker, check_only=check_only)
    return prepare_storage(settings, docker, check_only=check_only)
