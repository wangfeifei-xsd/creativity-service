"""验证环境选择、进程变量优先级和模块配置隔离。"""

import json
import os
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from creativity_service.core.config import Settings
from creativity_service.core.deletion.ledger import configured_root
from creativity_service.integrations.sandbox import SandboxSettings
from creativity_service.modules.iam.external import IdentitySettings
from creativity_service.modules.integrations.assembly import IntegrationSettings
from creativity_service.modules.integrations.automation_assembly import AutomationSettings
from creativity_service.modules.mcp.assembly import McpSettings
from creativity_service.modules.mcp.oauth import OAuthSettings
from creativity_service.modules.models.assembly import ModelSettings


@pytest.fixture
def environment_directory(settings, monkeypatch, tmp_path):
    lines = []
    for key, value in settings:
        if value is None:
            continue
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        elif isinstance(value, list | bool):
            value = json.dumps(value)
        lines.append(f"CREATIVITY_{key.upper()}={value}")
    source = "\n".join(lines)
    for key in os.environ:
        if key.startswith("CREATIVITY_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    for environment in ("dev", "test", "prod"):
        (tmp_path / f".env.{environment}").write_text(
            source.replace("CREATIVITY_ENVIRONMENT=test", f"CREATIVITY_ENVIRONMENT={environment}")
            + f"\nCREATIVITY_SERVICE_NAME={environment}\n"
        )
    return tmp_path


@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        (None, "dev"),
        ("dev", "dev"),
        ("test", "test"),
        ("prod", "prod"),
        ("development", "dev"),
        ("production", "prod"),
    ],
)
def test_selects_only_requested_environment(environment_directory, monkeypatch, selected, expected):
    if selected is not None:
        monkeypatch.setenv("CREATIVITY_ENVIRONMENT", selected)
    loaded = Settings()
    assert loaded.environment == expected
    assert loaded.service_name == expected


@pytest.mark.parametrize("environment", ["dev", "test", "prod"])
def test_each_environment_ignores_legacy_and_local_files(
    environment_directory, monkeypatch, environment
):
    for filename in (".env", ".env.local", f".env.{environment}.local"):
        (environment_directory / filename).write_text("CREATIVITY_DATABASE_URL=invalid\n")
    monkeypatch.setenv("CREATIVITY_ENVIRONMENT", environment)
    assert Settings().service_name == environment
    (environment_directory / f".env.{environment}").unlink()
    with pytest.raises(ValidationError, match="database_url"):
        Settings()


def test_process_and_explicit_overrides(environment_directory, monkeypatch):
    monkeypatch.setenv("CREATIVITY_ENVIRONMENT", "test")
    assert Settings().service_name == "test"
    monkeypatch.setenv("CREATIVITY_SERVICE_NAME", "process")
    assert Settings().service_name == "process"
    assert Settings(service_name="explicit").service_name == "explicit"
    assert Settings(environment="prod").environment == "prod"


def test_explicit_file_and_disabled_file_loading(environment_directory):
    assert Settings(_env_file=".env.test").environment == "test"
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_unknown_environment_fails_instead_of_loading_dev(environment_directory, monkeypatch):
    monkeypatch.setenv("CREATIVITY_ENVIRONMENT", "prdo")
    with pytest.raises(ValueError, match="dev、test 或 prod"):
        Settings()


@pytest.mark.parametrize(
    ("settings_type", "variable", "field", "value"),
    [
        (ModelSettings, "CREATIVITY_MODEL_KEY_VERSION", "key_version", "test-key"),
        (IntegrationSettings, "CREATIVITY_BUSINESS_KEY_VERSION", "key_version", "test-key"),
        (AutomationSettings, "CREATIVITY_AUTOMATION_KEY_VERSION", "key_version", "test-key"),
        (McpSettings, "CREATIVITY_MCP_DESTINATIONS", "destinations", []),
        (OAuthSettings, "CREATIVITY_MCP_OAUTH_PROFILES", "profiles", []),
        (IdentitySettings, "CREATIVITY_IDENTITY_PROFILES", "profiles", []),
        (SandboxSettings, "CREATIVITY_SANDBOX_PROFILES", "profiles", []),
    ],
)
def test_optional_modules_share_environment_selection(
    environment_directory, monkeypatch, settings_type, variable, field, value
):
    monkeypatch.setenv("CREATIVITY_ENVIRONMENT", "test")
    (environment_directory / ".env").write_text(f"{variable}=invalid\n")
    with (environment_directory / ".env.test").open("a") as stream:
        stream.write(f"{variable}={value}\n")
    assert getattr(settings_type(), field) == value
    assert getattr(settings_type(_env_file=None), field) == (None if field == "key_version" else [])


def test_ledger_cache_tracks_environment_and_file_changes(environment_directory, monkeypatch):
    for environment in ("dev", "test", "prod"):
        monkeypatch.setenv("CREATIVITY_ENVIRONMENT", environment)
        path = environment_directory / f".env.{environment}"
        path.write_text(f"CREATIVITY_DELETION_LEDGER_PATH=/ledger/{environment}\n")
        assert configured_root() == Path(f"/ledger/{environment}")
        path.write_text(f"CREATIVITY_DELETION_LEDGER_PATH=/ledger/{environment}/changed\n")
        assert configured_root() == Path(f"/ledger/{environment}/changed")
    monkeypatch.setenv("CREATIVITY_DELETION_LEDGER_PATH", "/ledger/process")
    assert configured_root() == Path("/ledger/process")


@pytest.mark.parametrize("environment", ["test", "prod"])
def test_deployment_templates_require_real_connection_configuration(settings, environment):
    with pytest.raises(ValidationError) as failure:
        Settings(_env_file=f".env.{environment}")
    fields = {error["loc"][0] for error in failure.value.errors()}
    assert {"database_url", "oss_access_key_id", "oss_access_key_secret"} <= fields
    assert ("qdrant_uri" in fields) == (environment == "prod")
