"""使用真实依赖验证启动、探针和独立 Worker；不调用模型供应商。"""

import os
import socket
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from celery import Celery
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from creativity_service.app import create_app
from creativity_service.core.config import Settings
from creativity_service.core.database.tables import metadata

pytestmark = pytest.mark.integration


def test_real_services_and_no_automatic_tables():
    settings = Settings()
    engine = create_engine(settings.database_url.get_secret_value())
    query = text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    try:
        with engine.connect() as connection:
            before = connection.execute(query).all()
        with TestClient(create_app(settings)) as client:
            assert client.get("/health/live").status_code == 200
            response = client.get("/health/ready")
            assert response.status_code == 200, response.json()
            assert len(response.json()["checks"]) == 6
        with engine.connect() as connection:
            assert connection.execute(query).all() == before
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
        with engine.connect() as connection:
            after = {row[0] for row in connection.execute(query)}
            assert set(metadata.tables) | {"creativity_alembic_version"} <= after
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("field", "value", "failed"),
    [
        ("database_url", "postgresql+psycopg://test:test@127.0.0.1:1/test", "database"),
        ("redis_auth_url", "redis://127.0.0.1:1/1", "redis_auth"),
        ("s3_bucket", "creativity-missing-test-bucket", "object_storage"),
    ],
)
def test_real_dependency_failure_is_diagnosable(field, value, failed):
    values = Settings().model_dump() | {field: value, "health_timeout_seconds": 1}
    with TestClient(create_app(Settings(_env_file=None, **values))) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        unavailable = {
            name
            for name, result in response.json()["checks"].items()
            if result["status"] == "unavailable"
        }
        assert unavailable == {failed}, response.json()


def test_worker_starts_as_independent_process(tmp_path):
    settings = Settings()
    name = f"creativity-test-{uuid4().hex[:8]}@{socket.gethostname()}"
    client = Celery(
        "probe",
        broker=settings.celery_broker_url.get_secret_value(),
        broker_transport_options={
            "global_keyprefix": f"{settings.redis_key_prefix}:celery:broker:"
        },
    )
    log_path = tmp_path / "worker.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "creativity_service.workers.app:app",
                "worker",
                "--pool=solo",
                "--loglevel=INFO",
                f"--hostname={name}",
                "--without-gossip",
                "--without-mingle",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
        )
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                assert process.poll() is None, log_path.read_text()
                response = client.control.inspect(destination=[name], timeout=1).ping()
                if response and response.get(name) == {"ok": "pong"}:
                    break
                time.sleep(0.2)
            else:
                pytest.fail(f"Worker 未就绪：{log_path.read_text()}")
            assert "Signal handler" not in log_path.read_text()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            client.close()
