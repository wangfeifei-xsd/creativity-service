"""移除网络字段后，既有模型证据、版本图和运行快照仍保持一致。"""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

from creativity_service.core.primitives import digest
from creativity_service.modules.models.policy import configuration_digest
from creativity_service.modules.models.schemas import FrozenModel
from tests.models.test_protocols import fixture_config


def migration():
    path = Path(__file__).parents[2] / "alembic/versions/0047_remove_model_networks.py"
    spec = importlib.util.spec_from_file_location("network_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_removes_platform_fields_and_preserves_capability_evidence():
    upgrade = migration()
    frozen = fixture_config().model_dump(mode="json")
    connection = {
        "id": frozen["connection_id"],
        "protocol": frozen["protocol"],
        "endpoint": frozen["endpoint"],
        "timeout_seconds": frozen["timeout_seconds"],
        "allowed_networks": ["198.18.1.151/32"],
        "validation_revision": 2,
    }
    model = {
        "id": frozen["model_id"],
        "connection_id": connection["id"],
        "provider_model_name": frozen["provider_model_name"],
        "context_limit": None,
        "parameters": frozen["parameters"],
        "parameter_allowlist": frozen["parameter_allowlist"],
        "validation_revision": 1,
    }
    old_digest = upgrade.config_digest(model, connection, legacy=True)
    expected = configuration_digest(model, connection)
    frozen.update(allowed_networks=connection["allowed_networks"], config_digest=old_digest)
    model["capabilities"] = {
        "text": {"state": "SUPPORTED", "config_digest": old_digest, "evidence": "live"},
        "tools": {"state": "SUPPORTED", "config_digest": "older-config", "evidence": "live"},
    }
    versions = []

    def version(identifier, kind, resource_id, content, dependencies):
        resolved = [
            {
                "version_id": dep,
                **{
                    k: next(row for row in versions if row["id"] == dep)[k]
                    for k in ("content_digest", "dependencies_digest")
                },
            }
            for dep in dependencies
        ]
        versions.append(
            {
                "id": identifier,
                "resource_type": kind,
                "resource_id": resource_id,
                "content": content,
                "output_schema": {},
                "dependencies": dependencies,
                "content_digest": digest({"content": content, "output_schema": {}}),
                "dependencies_digest": digest(resolved),
            }
        )

    version(frozen["connection_version_id"], "model_connection", connection["id"], connection, [])
    version(
        frozen["model_version_id"],
        "model",
        model["id"],
        {
            **model,
            "config_digest": old_digest,
            "connection_version_id": frozen["connection_version_id"],
        },
        [frozen["connection_version_id"]],
    )
    version("route", "model_route", "route", {"models": [frozen]}, [frozen["model_version_id"]])
    business = {"input_schema": {"properties": {"allowed_networks": {"type": "array"}}}}
    version("tool", "tool", "tool", business, ["route"])
    original = {"models": [model], "model_connections": [connection], "resource_versions": versions}
    saved = deepcopy(original)
    result, mapping = upgrade.transform_configuration(original)
    assert original == saved
    assert "allowed_networks" not in result["model_connections"][0]
    evidence = result["models"][0]["capabilities"]
    assert evidence["text"] == {"state": "SUPPORTED", "config_digest": expected, "evidence": "live"}
    assert evidence["tools"]["config_digest"] == "older-config"
    rows = {row["id"]: row for row in result["resource_versions"]}
    assert (
        FrozenModel.model_validate(rows["route"]["content"]["models"][0]).config_digest == expected
    )
    assert rows["tool"]["content"] == business
    assert rows["tool"]["dependencies_digest"] != versions[-1]["dependencies_digest"]
    for row in rows.values():
        assert row["content_digest"] == digest({"content": row["content"], "output_schema": {}})
    payload = {
        "versions": versions,
        "runtime": {
            "model": frozen,
            "execution": {"configuration": frozen},
        },
    }
    spec = {
        "payload_json": json.dumps(payload),
        "content_digest": versions[0]["content_digest"],
        "dependencies_digest": digest(versions),
        "candidate_digest": "historical-candidate",
    }
    cleaned = upgrade.clean_spec(spec, mapping)
    content = json.loads(cleaned["payload_json"])
    assert "allowed_networks" not in content["runtime"]["model"]
    assert "allowed_networks" not in content["runtime"]["execution"]["configuration"]
    assert cleaned["dependencies_digest"] == digest(content["versions"])
