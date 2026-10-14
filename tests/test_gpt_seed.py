"""验证独立 GTP 配置可完整恢复，并拒绝秘密明文、悬空依赖及失真的能力证据。"""

import json

import pytest

from creativity_service.core.primitives import ServiceError, digest
from scripts import render_init_sql
from scripts.render_gpt_seed import load_gpt, merge_gpt, validate_gpt
from scripts.render_order_guidance_seed import load_guidance, merge_guidance
from scripts.render_weather_seed import load_weather, merged_tables


@pytest.fixture
def archived_base():
    base = json.loads(render_init_sql.SEED.read_text())["tables"]
    combined = merged_tables(base, load_weather(base)["tables"])
    return merge_guidance(combined, load_guidance(combined)["tables"])


def test_gpt_archive_restores_published_text_route_and_transitive_connection(archived_base):
    seed = load_gpt(archived_base)
    tables = seed["tables"]
    model, connection = tables["models"][0], tables["model_connections"][0]
    versions = {row["id"]: row for row in tables["resource_versions"]}
    route = versions[tables["release_mappings"][0]["version_id"]]
    assert model["provider_model_name"] == "gpt-6.1-sol"
    assert connection["endpoint"] == "https://api.masterhuang.top"
    assert route["content"]["required_capabilities"] == ["text"]
    assert route["content"]["primary_model"] == model["id"]
    assert route["content"]["fallback_models"] == []
    assert model["capabilities"]["text"]["state"] == "SUPPORTED"
    assert model["capabilities"]["tools"]["state"] == "UNSUPPORTED"
    assert all("test_id" not in item for item in model["capabilities"].values())
    dependency = versions[model["current_version_id"]]["dependencies"][0]
    assert dependency != connection["current_version_id"]
    assert versions[dependency]["resource_id"] == connection["id"]
    credential = tables["credentials"][0]
    assert credential["secret_value"] is None and credential["key_version"] == "local-model-v1"
    assert len(bytes.fromhex(credential["ciphertext"])) >= 29
    assert len(merge_gpt(archived_base, tables)["models"]) == len(archived_base["models"]) + 1


@pytest.mark.parametrize(
    "fault",
    [
        "history",
        "plaintext",
        "scope",
        "deleted",
        "dependency",
        "digest",
        "mapping",
        "test",
        "capability",
        "snapshot",
        "model",
    ],
)
def test_gpt_archive_rejects_incomplete_or_unsafe_restore(archived_base, fault):
    seed = load_gpt(archived_base)
    tables = seed["tables"]
    route = next(
        row for row in tables["resource_versions"] if row["resource_type"] == "model_route"
    )
    if fault == "history":
        tables["runs"] = []
    elif fault == "plaintext":
        tables["credentials"][0]["secret_value"] = "禁止归档的明文"
    elif fault == "scope":
        tables["credentials"][0]["environment"] = "prod"
    elif fault == "deleted":
        tables["models"][0]["is_deleted"] = True
    elif fault == "dependency":
        old_id = next(
            row for row in tables["resource_versions"] if row["resource_type"] == "model"
        )["dependencies"][0]
        tables["resource_versions"] = [
            row for row in tables["resource_versions"] if row["id"] != old_id
        ]
    elif fault == "digest":
        route["dependencies_digest"] = "0" * 64
    elif fault == "mapping":
        tables["release_mappings"][0]["version_id"] = "missing-version"
    elif fault == "test":
        tables["models"][0]["capabilities"]["text"]["test_id"] = "missing-test"
    elif fault == "capability":
        tables["models"][0]["capabilities"]["text"]["state"] = "UNSUPPORTED"
    elif fault == "snapshot":
        route["content"]["models"][0]["scope"]["channel_id"] = "other-channel"
        route["content_digest"] = digest(
            {"content": route["content"], "output_schema": route["output_schema"]}
        )
    else:
        tables["models"][0]["provider_model_name"] = "changed-model"
    with pytest.raises((ValueError, ServiceError)):
        validate_gpt(seed, archived_base)


def test_gpt_archive_cannot_overwrite_existing_configuration(archived_base):
    seed = load_gpt(archived_base)
    with pytest.raises(ValueError, match="不能覆盖"):
        merge_gpt(merge_gpt(archived_base, seed["tables"]), seed["tables"])
