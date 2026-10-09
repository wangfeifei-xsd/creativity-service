"""验证依赖复用、端口与认证冲突、只读检查及本地启动互斥。"""

from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest
from pydantic import SecretStr

from creativity_service.development import dependencies
from creativity_service.development.dependencies import (
    DockerRuntime,
    StartupError,
    ensure_endpoint,
    redis_address,
)
from creativity_service.development.launcher import startup_lock


def test_healthy_configured_dependency_never_touches_docker(monkeypatch):
    probe, start = Mock(), Mock()
    monkeypatch.setattr(dependencies, "port_open", Mock(side_effect=AssertionError))
    assert ensure_endpoint("MySQL", "127.0.0.1", 53306, 3306, probe, start) == (
        "127.0.0.1",
        53306,
    )
    start.assert_not_called()


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_existing_standard_port_is_reused_with_same_connection_probe(monkeypatch, host):
    start = Mock()

    def probe(address, port):
        if (address, port) != (host, 6379):
            raise ConnectionError

    monkeypatch.setattr(
        dependencies, "port_open", lambda address, port: (address, port) == (host, 6379)
    )
    assert ensure_endpoint("Redis", "127.0.0.1", 56379, 6379, probe, start) == (host, 6379)
    start.assert_not_called()


@pytest.mark.parametrize("occupied", [53306, 3306])
def test_occupied_port_with_wrong_credentials_does_not_start_another_database(
    monkeypatch, occupied
):
    probe = Mock(side_effect=PermissionError("不应输出的连接密码"))
    start = Mock()
    monkeypatch.setattr(dependencies, "port_open", lambda _host, port: port == occupied)
    with pytest.raises(StartupError) as failure:
        ensure_endpoint("MySQL", "127.0.0.1", 53306, 3306, probe, start)
    assert "连接密码" not in str(failure.value)
    start.assert_not_called()


def test_missing_remote_dependency_is_not_replaced_by_local_docker(monkeypatch):
    start = Mock()
    monkeypatch.setattr(dependencies, "port_open", lambda *_: False)
    with pytest.raises(StartupError, match="远端"):
        ensure_endpoint("MySQL", "db.example", 3306, 3306, Mock(side_effect=ConnectionError), start)
    start.assert_not_called()


def test_missing_local_dependency_is_started_once_then_verified(monkeypatch):
    running = False
    starts = []

    def probe(_host, _port):
        if not running:
            raise ConnectionError

    def start(port):
        nonlocal running
        starts.append(port)
        running = True

    monkeypatch.setattr(dependencies, "port_open", lambda *_: False)
    assert ensure_endpoint("Redis", "127.0.0.1", 56379, 6379, probe, start) == ("127.0.0.1", 56379)
    assert starts == [56379]


def test_check_mode_never_starts_missing_dependency(monkeypatch):
    start = Mock()
    monkeypatch.setattr(dependencies, "port_open", lambda *_: False)
    with pytest.raises(StartupError, match="尚未启动"):
        ensure_endpoint(
            "Redis",
            "127.0.0.1",
            56379,
            6379,
            Mock(side_effect=ConnectionError),
            start,
            check_only=True,
        )
    start.assert_not_called()


def test_running_project_container_is_not_recreated_for_a_bad_address(
    settings, monkeypatch, tmp_path
):
    docker = DockerRuntime(tmp_path, settings)
    compose = Mock(return_value="existing-container")
    monkeypatch.setattr(docker, "compose", compose)
    with pytest.raises(StartupError, match="已运行"):
        docker.start("redis", 56380)
    assert compose.call_count == 1
    assert "up" not in compose.call_args.args[0]


def test_startup_lock_blocks_duplicate_launch_and_is_released(tmp_path):
    lock = tmp_path / "start.lock"
    with startup_lock(lock):
        with pytest.raises(StartupError, match="已在运行"):
            with startup_lock(lock):
                pytest.fail("重复启动不应取得锁")
    with startup_lock(lock):
        pass


def test_redis_fallback_preserves_credentials_and_database():
    original = "redis://default:p%40ss%2Fword@localhost:56379/3"
    updated = urlsplit(redis_address(original, "::1", 6379))
    assert updated.hostname == "::1"
    assert updated.port == 6379
    assert updated.path == "/3"
    assert updated.username == "default"
    assert updated.password == "p%40ss%2Fword"


def test_milvus_fallback_is_propagated_to_application(settings, monkeypatch, tmp_path):
    settings = settings.model_copy(update={"milvus_uri": "http://localhost:19531"})
    docker = DockerRuntime(tmp_path, settings)
    monkeypatch.setattr(dependencies, "ensure_endpoint", Mock(return_value=("::1", 19530)))
    prepared = dependencies.prepare_vector(settings, docker, check_only=True)
    assert prepared.milvus_uri == "http://[::1]:19530"
    assert settings.milvus_uri == "http://localhost:19531"


def test_compose_falls_back_to_project_binary(settings, monkeypatch, tmp_path):
    root = tmp_path / "service"
    root.mkdir()
    compose = tmp_path / ".tools/docker/docker-compose"
    compose.parent.mkdir(parents=True)
    compose.write_text("#!/bin/sh\nexit 0\n")
    compose.chmod(0o755)
    docker = DockerRuntime(root, settings)
    run = Mock(return_value="")
    monkeypatch.setattr(docker, "run", run)
    monkeypatch.setattr(
        dependencies.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None
    )
    monkeypatch.setattr(dependencies.subprocess, "run", Mock(return_value=Mock(returncode=1)))
    docker.compose(["config", "--quiet"])
    assert run.call_args.kwargs["executable"] == [str(compose)]
    assert run.call_args.args[0][-2:] == ["config", "--quiet"]


def test_docker_redis_accepts_password_only_connection_urls(settings, monkeypatch, tmp_path):
    settings = settings.model_copy(
        update={
            name: SecretStr(f"redis://:p%40ss@127.0.0.1:56379/{number}")
            for number, name in enumerate(dependencies.REDIS_FIELDS)
        }
    )
    docker = DockerRuntime(tmp_path, settings)
    start_docker = Mock()
    monkeypatch.setattr(docker, "start", start_docker)

    def missing_endpoint(_label, host, port, _standard_port, _probe, start, **_kwargs):
        start(port)
        return host, port

    monkeypatch.setattr(dependencies, "ensure_endpoint", missing_endpoint)
    dependencies.prepare_redis(settings, docker, check_only=False)
    start_docker.assert_called_once_with("redis", 56379)
    assert docker.environment["DEV_REDIS_PASSWORD"] == "p@ss"
