"""验证销售推荐归档可完整恢复，拒绝丢包、悬空依赖及运行历史。"""

import json

import pytest

from creativity_service.core.primitives import ServiceError, digest
from scripts.render_gpt_seed import load_gpt, merge_gpt
from scripts.render_init_sql import SEED
from scripts.render_order_guidance_seed import load_guidance, merge_guidance
from scripts.render_sales_access import ACCESS_SCOPES
from scripts.render_sales_seed import load_sales, merge_sales, validate_sales


@pytest.fixture
def archived_base():
    base = json.loads(SEED.read_text())["tables"]
    base = merge_guidance(base, load_guidance(base)["tables"])
    return merge_gpt(base, load_gpt(base)["tables"])


def test_sales_seed_keeps_online_agent_with_complete_client_access(archived_base):
    seed = load_sales(archived_base)
    tables = seed["tables"]
    versions = {row["id"]: row for row in tables["resource_versions"]}
    agent = versions[seed["source"]["version_id"]]
    assert agent["state"] == "CURRENT"
    assert tables["agent_environment_states"][0]["is_online"] is True
    assert agent["content"]["context"]["context_limit"] == 32000
    assert agent["content"]["limits"]["token_limit"] == 96000
    assert len(agent["content"]["steps"]) == 6
    assert agent["content"]["context"]["conversation_enabled"] is True
    assert agent["content"]["context"]["summary_policy"] == "recent"
    assert {row["name"] for row in tables["prompts"]} == {"预检·提示词", "匹配·提示词"}
    assert {row["name"] for row in tables["skills"]} == {"预检·技能", "匹配·技能"}
    assert tables["tools"][0]["name"] == "可售目录·工具"
    assert all(row["resource_type"] != "agent" for row in tables["release_mappings"])
    assert len(seed["objects"]) == 2
    assert len(merge_sales(archived_base, tables)["agents"]) == 2
    assert set(tables["service_clients"][0]["scopes"]) == ACCESS_SCOPES
    assert tables["channel_keys"][0]["last_used_at"] is None
    assert tables["credentials"][0]["secret_value"] is None
    assert tables["subject_review_bindings"][0]["enabled"] is True


@pytest.mark.parametrize(
    "fault",
    [
        "offline_agent",
        "missing_client",
        "extra_scope",
        "key_usage",
        "lookup_scope",
        "delegation_client",
        "plaintext",
        "missing_review",
        "review_contract",
        "missing_admin_access",
    ],
)
def test_sales_seed_rejects_broken_client_access(archived_base, fault):
    seed = load_sales(archived_base)
    tables = seed["tables"]
    if fault == "offline_agent":
        tables["agent_environment_states"][0]["is_online"] = False
    elif fault == "missing_client":
        tables["service_clients"] = []
    elif fault == "extra_scope":
        tables["channel_keys"][0]["scopes"].append("data:export")
    elif fault == "key_usage":
        tables["channel_keys"][0]["last_used_at"] = seed["source"]["captured_at"]
    elif fault == "lookup_scope":
        tables["key_identity_index"][0]["target_channel_id"] = "another-channel"
    elif fault == "delegation_client":
        tables["delegation_keys"][0]["client_id"] = "another-client"
    elif fault == "plaintext":
        tables["credentials"][0]["secret_value"] = "must-not-be-archived"
    elif fault == "missing_review":
        tables["subject_review_bindings"] = []
    elif fault == "review_contract":
        tables["subject_review_bindings"][0]["schema_hash"] = "0" * 64
    else:
        for grant in archived_base["resource_grants"]:
            if grant["resource_type"] == "channel":
                grant["allowed_actions"] = [
                    action for action in grant["allowed_actions"] if action != "data:read_sensitive"
                ]
    with pytest.raises(ValueError):
        validate_sales(seed, archived_base)


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


def matching_result(status="COMPLETED", recommendations=None, questions=None):
    return {
        "schema_version": "2.0",
        "business_status": status,
        "data": {
            "recommendations": recommendations
            if recommendations is not None
            else [{"product_id": "901", "sku_id": "902"}],
            "questions": questions or [],
        },
        "warnings": [],
        "evidence_refs": [],
    }


def script_transform(agent, key):
    """执行仓库内冻结的校验脚本，不执行测试输入中的正文。"""
    namespace = {}
    source = next(step for step in agent["steps"] if step["key"] == key)["script"]["source"]
    exec(compile(source, key, "exec"), namespace)
    return namespace["transform"]


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "foreign_sku",
        "foreign_product",
        "extra",
        "numeric_id",
        "non_complete",
        "question",
        "incomplete",
    ],
)
def test_pc_recommendations_only_return_valid_catalog_ids(archived_base, fault):
    from jsonschema import Draft202012Validator

    seed = load_sales(archived_base)
    agent = next(
        v["content"] for v in seed["tables"]["resource_versions"] if v["resource_type"] == "agent"
    )
    result = matching_result()
    catalog = {"complete": True, "items": [{"id": "901", "skus": [{"id": "902"}]}]}
    row = result["data"]["recommendations"][0]
    if fault == "foreign_sku":
        row["sku_id"] = "903"
    elif fault == "foreign_product":
        row["product_id"] = "900"
    elif fault == "extra":
        row["unit_price"] = "10"
    elif fault == "numeric_id":
        row["product_id"] = 901
    elif fault == "non_complete":
        result["business_status"] = "NO_MATCH"
    elif fault == "question":
        result["data"]["questions"] = ["什么游戏？"]
    elif fault == "incomplete":
        catalog["complete"] = False
    checked = script_transform(agent, "check_recommendation")(
        {"recommendation": result, "service_catalog": catalog}, {}
    )
    Draft202012Validator(agent["output_schema"]).validate(checked)
    assert checked["business_status"] == ("COMPLETED" if fault is None else "INSUFFICIENT_DATA")
    assert checked["data"]["recommendations"] == (
        [{"product_id": "901", "sku_id": "902"}] if fault is None else []
    )


def test_pc_questions_and_catalog_early_exit_obey_final_contract(archived_base):
    from jsonschema import Draft202012Validator

    seed = load_sales(archived_base)
    agent = next(
        v["content"] for v in seed["tables"]["resource_versions"] if v["resource_type"] == "agent"
    )
    validator = Draft202012Validator(agent["output_schema"])
    question = matching_result("NEEDS_INPUT", [], ["想玩哪款游戏？"])
    assert (
        script_transform(agent, "check_recommendation")(
            {"recommendation": question, "service_catalog": {}}, {}
        )
        == question
    )
    validator.validate(question)
    for complete in [True, False]:
        page = {
            "tenantId": "42",
            "items": [],
            "returnedCount": 0,
            "hasMore": False,
            "nextCursor": None,
            "observedAt": "2026-10-19T00:00:00Z",
        }
        checked = script_transform(agent, "check_catalog")(
            {
                "tenantId": "42",
                "query_result": {
                    "pages": [page],
                    "complete": complete,
                    "received": 0,
                    "page_count": 1,
                    "total": 0,
                },
            },
            {},
        )
        final = script_transform(agent, "catalog_exit")({"result": checked}, {})
        validator.validate(final)
        assert final["business_status"] == ("NO_MATCH" if complete else "INSUFFICIENT_DATA")
