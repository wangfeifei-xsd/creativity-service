"""验证销售推荐归档可完整恢复，拒绝丢包、悬空依赖及运行历史。"""

import json

import pytest

from creativity_service.core.primitives import ServiceError, digest
from scripts.render_gpt_seed import load_gpt, merge_gpt
from scripts.render_init_sql import SEED
from scripts.render_order_guidance_seed import load_guidance, merge_guidance
from scripts.render_sales_seed import load_sales, merge_sales, validate_sales


@pytest.fixture
def archived_base():
    base = json.loads(SEED.read_text())["tables"]
    base = merge_guidance(base, load_guidance(base)["tables"])
    return merge_gpt(base, load_gpt(base)["tables"])


def test_sales_seed_keeps_unpublished_agent_with_all_current_dependencies(archived_base):
    seed = load_sales(archived_base)
    tables = seed["tables"]
    versions = {row["id"]: row for row in tables["resource_versions"]}
    agent = versions[seed["source"]["version_id"]]
    assert agent["state"] == "DRAFT"
    assert agent["content"]["context"]["context_limit"] == 32000
    assert agent["content"]["limits"]["token_limit"] == 96000
    assert len(agent["content"]["steps"]) == 5
    assert {row["name"] for row in tables["prompts"]} == {"预检·提示词", "匹配·提示词"}
    assert {row["name"] for row in tables["skills"]} == {"预检·技能", "匹配·技能"}
    assert tables["tools"][0]["name"] == "可售目录·工具"
    assert all(row["resource_type"] != "agent" for row in tables["release_mappings"])
    assert len(seed["objects"]) == 2
    assert len(merge_sales(archived_base, tables)["agents"]) == 2


@pytest.mark.parametrize(
    "fault",
    [
        "history",
        "deleted",
        "scope",
        "dependency",
        "missing_prompt",
        "published_agent",
        "missing_release",
        "digest",
        "object_bytes",
        "missing_object",
        "object_scope",
        "object_path",
        "file_index",
        "file_url",
        "mcp_binding",
        "subject",
        "historical_link",
    ],
)
def test_sales_seed_rejects_incomplete_or_changed_configuration(archived_base, fault):
    seed = load_sales(archived_base)
    tables = seed["tables"]
    version = next(r for r in tables["resource_versions"] if r["resource_type"] == "agent")
    if fault == "history":
        tables["runs"] = []
    elif fault == "deleted":
        tables["skills"][0]["is_deleted"] = True
    elif fault == "scope":
        tables["mcp_imports"][0]["environment"] = "prod"
    elif fault == "dependency":
        version["dependencies"].append("missing")
    elif fault == "missing_prompt":
        tables["prompts"].pop()
    elif fault == "published_agent":
        version["state"] = "PUBLISHED"
    elif fault == "missing_release":
        tables["release_mappings"].pop()
    elif fault == "digest":
        version["content_digest"] = "0" * 64
    elif fault == "object_bytes":
        seed["objects"][0]["base64"] = "AA=="
    elif fault == "missing_object":
        seed["objects"].pop()
    elif fault == "object_scope":
        seed["objects"][0]["channel_id"] = "other-channel"
    elif fault == "object_path":
        seed["objects"][0]["object_key"] += "/../other"
    elif fault == "file_index":
        tables["skill_files"].pop()
    elif fault == "file_url":
        tables["skill_files"][0]["package_url"] = "https://example.test/missing.zip"
    elif fault == "mcp_binding":
        tables["mcp_imports"][0]["discovery_id"] = "missing"
    elif fault == "subject":
        tool = next(r for r in tables["resource_versions"] if r["resource_type"] == "tool")
        tool["content"]["subject_requirements"]["required"] = True
        tool["content_digest"] = digest(
            {"content": tool["content"], "output_schema": tool["output_schema"]}
        )
    else:
        tables["source_links"][0].update(source_type="run", source_id="historical")
    with pytest.raises((ValueError, ServiceError)):
        validate_sales(seed, archived_base)


def test_sales_seed_cannot_overwrite_existing_resources(archived_base):
    seed = load_sales(archived_base)
    with pytest.raises(ValueError, match="不能覆盖"):
        merge_sales(merge_sales(archived_base, seed["tables"]), seed["tables"])
