"""使用隔离数据库和 Redis 前缀验证一键启动、文件日志及退出清理。"""

import json
import os
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from redis import Redis
from sqlalchemy import create_engine, make_url, text

from creativity_service.core.config import Settings

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_local_launcher_reuses_services_starts_all_roles_and_stops_cleanly(tmp_path):
    settings = Settings()
    database = f"test_local_{uuid4().hex}"
    url = make_url(settings.database_url.get_secret_value())
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(
            text(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin")
        )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log_directory = tmp_path / "log"
    # 若依赖复用过程误调用 Docker，立即失败并保留证据，不能启动额外容器。
    binaries = tmp_path / "bin"
    binaries.mkdir()
    marker = tmp_path / "docker-called"
    fake_docker = binaries / "docker"
    fake_docker.write_text('#!/bin/sh\n: > "$DOCKER_CALL_MARKER"\nexit 91\n')
    fake_docker.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ.get("PATH", ""),
        "DOCKER_CALL_MARKER": str(marker),
        "CREATIVITY_ENVIRONMENT": "test",
        "CREATIVITY_DATABASE_URL": url.set(database=database).render_as_string(hide_password=False),
        "CREATIVITY_REDIS_KEY_PREFIX": database,
        "CREATIVITY_LOG_DIRECTORY": str(log_directory),
        "CREATIVITY_DELETION_LEDGER_PATH": str(tmp_path / "ledger"),
    }
    process = None
    try:
        with (tmp_path / "launcher-console.log").open("w") as output:
            process = subprocess.Popen(
                [str(ROOT / "scripts/start-local.sh"), "--port", str(port), "--no-reload"],
                cwd=tmp_path,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            deadline = time.monotonic() + 60
            with httpx.Client(timeout=2, trust_env=False) as client:
                while time.monotonic() < deadline:
                    assert process.poll() is None, (tmp_path / "launcher-console.log").read_text()
                    try:
                        response = client.get(f"http://127.0.0.1:{port}/health/ready")
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.2)
                else:
                    pytest.fail("本地 API 未就绪")
                assert response.json()["status"] == "ready"
                request_id = response.headers["X-Request-ID"]

            for role in ("api", "worker", "scheduler"):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    path = log_directory / f"{role}.log"
                    if path.exists() and path.stat().st_size:
                        break
                    assert process.poll() is None
                    time.sleep(0.2)
                assert path.exists() and path.stat().st_size, f"缺少 {role} 文件日志"
                for line in path.read_text().splitlines():
                    assert "event" in json.loads(line)
            events = [
                json.loads(line) for line in (log_directory / "api.log").read_text().splitlines()
            ]
            assert any(
                event.get("request_id") == request_id and event.get("status_code") == 200
                for event in events
            )
            assert not marker.exists()

            duplicate = subprocess.run(
                [str(ROOT / "scripts/start-local.sh"), "--port", str(port)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert duplicate.returncode == 1
            assert "已在运行" in duplicate.stderr
            stopped = subprocess.run(
                [str(ROOT / "scripts/stop-local.sh")],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=40,
            )
            assert stopped.returncode == 0, stopped.stderr
            assert "API、Worker 和调度器已停止" in stopped.stdout
            process.wait(timeout=30)
            assert process.returncode == 0
        with socket.socket() as connection:
            assert connection.connect_ex(("127.0.0.1", port)) != 0
        assert "本地启动已结束" in (log_directory / "launcher.log").read_text()
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=30)
        for value in (
            settings.redis_cache_url,
            settings.redis_auth_url,
            settings.celery_broker_url,
            settings.celery_result_url,
        ):
            with Redis.from_url(value.get_secret_value()) as redis:
                keys = list(redis.scan_iter(f"{database}:*"))
                if keys:
                    redis.delete(*keys)
        with engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE `{database}`"))
        engine.dispose()
