"""本地开发入口，统一依赖准备、进程生命周期和启动诊断。"""

import argparse
import asyncio
import fcntl
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO

import httpx
from alembic.config import Config
from pydantic import ValidationError

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.observability import configure_logging
from creativity_service.development.dependencies import (
    REDIS_FIELDS,
    DockerRuntime,
    StartupError,
    port_open,
    prepare_dependencies,
)

ROOT = Path(__file__).resolve().parents[3]
logger = logging.getLogger(__name__)


@contextmanager
def startup_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StartupError("本地启动脚本已在运行，不重复启动依赖或应用进程。") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def export_settings(settings: Settings) -> None:
    # 探测到的本机服务只对本次进程及其子进程生效，不覆盖用户的 .env。
    for name in ("database_url", *REDIS_FIELDS):
        os.environ[f"CREATIVITY_{name.upper()}"] = getattr(settings, name).get_secret_value()
    os.environ["CREATIVITY_S3_ENDPOINT_URL"] = settings.s3_endpoint_url
    os.environ["CREATIVITY_MILVUS_URI"] = settings.milvus_uri
    os.environ["CREATIVITY_LOG_DIRECTORY"] = str(settings.log_directory.resolve())


@dataclass
class Child:
    role: str
    process: subprocess.Popen[str]
    output_thread: threading.Thread
    handler: logging.Handler


def capture_output(stream: IO[str], handler: logging.Handler) -> None:
    try:
        for line in stream:
            record = logging.LogRecord(
                "console", logging.INFO, __file__, 0, line.rstrip(), (), None
            )
            handler.handle(record)
    finally:
        stream.close()


def start_child(role: str, args: list[str], settings: Settings) -> Child:
    handler = RotatingFileHandler(
        settings.log_directory / f"{role}-console.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", *args],
            cwd=ROOT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except Exception:
        handler.close()
        raise
    assert process.stdout is not None
    thread = threading.Thread(target=capture_output, args=(process.stdout, handler), daemon=True)
    thread.start()
    logger.info("已启动 %s，进程号 %s", role, process.pid)
    return Child(role, process, thread, handler)


def check_children(children: list[Child]) -> None:
    for child in children:
        if child.process.poll() is not None:
            raise StartupError(f"{child.role} 进程已退出，请查看对应的 {child.role}-console.log。")


def stop_children(children: list[Child]) -> None:
    for child in reversed(children):
        try:
            os.killpg(child.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 20
    for child in reversed(children):
        try:
            child.process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            logger.warning("%s 未在退出期限内停止，结束本次启动的进程组", child.role)
            try:
                os.killpg(child.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.process.wait(timeout=5)
        child.output_thread.join(timeout=2)
        child.handler.close()


def serve(settings: Settings, port: int, *, reload: bool) -> None:
    children: list[Child] = []
    state = ROOT / ".local/development"
    state.mkdir(parents=True, exist_ok=True)
    api = [
        "uvicorn",
        "creativity_service.app:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-access-log",
    ]
    if reload:
        api += ["--reload", "--reload-dir", str(ROOT / "src")]
    celery = ["celery", "-A", "creativity_service.workers.app:app"]
    commands = {
        "api": api,
        "worker": [
            *celery,
            "worker",
            "--pool=solo",
            f"--loglevel={settings.log_level}",
            "--hostname=creativity-local@%h",
        ],
        "scheduler": [
            *celery,
            "beat",
            f"--loglevel={settings.log_level}",
            f"--schedule={state / 'celerybeat-schedule'}",
            f"--pidfile={state / 'scheduler.pid'}",
        ],
    }
    try:
        for role, args in commands.items():
            children.append(start_child(role, args, settings))
        deadline = time.monotonic() + 45
        with httpx.Client(timeout=2, trust_env=False) as client:
            while time.monotonic() < deadline:
                check_children(children)
                try:
                    response = client.get(f"http://127.0.0.1:{port}/health/ready")
                    if response.status_code == 200 and response.json().get("status") == "ready":
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(0.5)
            else:
                raise StartupError("API 未在期限内就绪，请检查 api.log 和 api-console.log。")
        logger.info(
            "本地环境已就绪：http://127.0.0.1:%s/docs；日志目录：%s",
            port,
            settings.log_directory.resolve(),
        )
        while True:
            check_children(children)
            time.sleep(0.5)
    finally:
        logger.info("正在停止本次启动的应用进程；Docker 依赖继续保留")
        stop_children(children)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="检查并启动本地 MySQL 8、Milvus、Redis、对象存储与后端进程"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="只检查依赖连接和向量能力")
    mode.add_argument("--infra-only", action="store_true", help="只准备基础设施，不迁移或启动应用")
    mode.add_argument(
        "--prepare-only", action="store_true", help="准备依赖并完成迁移和系统渠道初始化"
    )
    mode.add_argument("--stop-infra", action="store_true", help="停止项目 Docker 依赖并保留数据卷")
    parser.add_argument("--port", type=int, default=8000, help="API 本地端口，默认 8000")
    parser.add_argument("--no-reload", action="store_true", help="关闭 API 热重载")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("API 端口必须在 1–65535 范围内")
    os.chdir(ROOT)
    try:
        settings = Settings()
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors())
        raise SystemExit(f"本地配置无效，请检查 .env：{fields}") from None
    if settings.environment == "production":
        raise SystemExit("本地启动脚本不能用于 production 环境。")
    configure_logging(settings, "launcher")

    # SIGTERM 与 Ctrl+C 走同一清理路径，只关闭本脚本拥有的进程组。
    def interrupt(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        if args.check:
            prepare_dependencies(ROOT, settings, check_only=True)
            logger.info("本地依赖检查通过")
            return
        with startup_lock(ROOT / ".local/development/start.lock"):
            if args.stop_infra:
                DockerRuntime(ROOT, settings).compose(["down"])
                logger.info("项目 Docker 依赖已停止，数据卷保留")
                return
            if not (args.infra_only or args.prepare_only) and port_open("127.0.0.1", args.port):
                raise StartupError(
                    f"API 端口 {args.port} 已被占用，请复用现有服务或通过 --port 指定空闲端口。"
                )
            settings = prepare_dependencies(ROOT, settings)
            # 再次验证回退到本机端口后的四个 Redis 用途仍然使用独立数据库。
            settings = Settings.model_validate(settings.model_dump())
            export_settings(settings)
            if args.infra_only:
                return
            logger.info("执行当前数据库迁移")
            command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
            from creativity_service.modules.channels.cli import initialize

            asyncio.run(initialize())
            logger.info("数据库迁移与系统渠道初始化完成")
            if not args.prepare_only:
                serve(settings, args.port, reload=not args.no_reload)
    except KeyboardInterrupt:
        logger.info("本地启动已结束")
    except StartupError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from None
    except Exception as exc:
        logger.error("启动失败，请检查依赖与配置", extra={"error_type": type(exc).__name__})
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
