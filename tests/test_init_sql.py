"""离线检查两份初始化归档、冻结凭据与控制面关联。"""

import json
from copy import deepcopy

import pytest

from creativity_service.core.auth.passwords import PasswordHasher
from creativity_service.core.primitives import digest
from creativity_service.modules.budgets.schemas import BudgetCreate
from creativity_service.modules.channels.codes import channel_code
from creativity_service.modules.channels.schemas import RetentionPolicy
from creativity_service.modules.iam.repositories import membership_id, role_catalog_rows
from creativity_service.modules.models.policy import PROTOCOLS, validate_endpoint
from creativity_service.modules.models.schemas import ProviderView
from scripts import render_init_sql
from scripts.render_weather_seed import load_weather, merged_tables, validate_weather
from scripts.restore_init_objects import archived_objects


def test_structure_and_initial_data_are_separate_and_reproducible():
    schema = render_init_sql.render()
    data = render_init_sql.render_data()
    assert schema == render_init_sql.ARCHIVE.read_text()
    assert data == render_init_sql.DATA_ARCHIVE.read_text()
    assert "INSERT INTO" not in schema
    assert "CREATE TABLE" not in data
    assert data == render_init_sql.render_data()
    assert "qwerty123$%^" not in data
    assert "qwerty123$%^" not in render_init_sql.SEED.read_text()


def test_seed_contains_initialized_channel_and_pending_administrator():
    seed = json.loads(render_init_sql.SEED.read_text())["tables"]
    channels = [row for row in seed["channels"] if row["id"] != "system"]
    assert len(channels) == 1
    channel = channels[0]
    assert channel["name"] == "寻弈乐竞" and channel["owner"] == "小苏打"
    assert channel["channel_code"] == channel_code(channel["name"]) == "XYLJ"
    assert channel["channel_id"] == channel["id"] != "system"
    assert channel["status"] == "ACTIVE" and channel["archived_at"] is None
    assert channel["retention_policy"] == RetentionPolicy().model_dump()
    assert channel["budget_policy_refs"] == channel["rate_limit_policy_refs"] == []
    index = seed["channel_code_index"][0]
    assert index["channel_id"] == "system"
    assert index["target_channel_id"] == channel["id"]
    assert index["channel_code"] == channel["channel_code"]
    admin = seed["platform_accounts"][0]
    member = seed["channel_memberships"][0]
    grant = seed["resource_grants"][0]
    assert member["channel_id"] == grant["channel_id"] == channel["id"]
    assert member["id"] == membership_id(channel["id"], admin["id"])
    assert member["user_id"] == member["granted_by"] == grant["grantee_id"] == admin["id"]
    assert member["roles"] == ["channel_admin"] and member["status"] == "ACTIVE"
    assert grant["id"] == "initial_" + member["id"]
    assert grant["resource_type"] == "channel" and grant["resource_id"] == channel["id"]
    assert grant["grantee_type"] == "account"
    role = next(row for row in seed["builtin_roles"] if row["role_code"] == "channel_admin")
    assert grant["allowed_actions"] == sorted(role["allowed_actions"])
    assert member["environments"] == []
    assert grant["environments"] == []
    assert channel["revision"] == index["revision"] == member["revision"] == grant["revision"] == 1
    assert render_init_sql.render_data().count("INSERT INTO channels ") == 2


@pytest.mark.parametrize(
    ("table", "field", "value", "message"),
    [
        ("channels", "channel_id", "system", "主档归属"),
        ("channels", "channel_code", "WRNG", "名称生成规则"),
        ("channel_code_index", "target_channel_id", "missing-channel", "目录缺失"),
        ("channel_code_index", "channel_code", "WRNG", "目录编码"),
        ("channel_code_index", "channel_id", "other-channel", "系统渠道"),
        ("channel_memberships", "channel_id", "system", "空范围授权"),
        ("channel_memberships", "user_id", "missing-account", "管理员或授权关联"),
        ("channel_memberships", "environments", ["dev"], "必须为空"),
        ("resource_grants", "resource_id", "other-channel", "管理员或授权关联"),
        ("resource_grants", "grantee_id", "missing-account", "管理员或授权关联"),
        ("resource_grants", "allowed_actions", [], "管理员或授权关联"),
        ("resource_grants", "environments", ["prod"], "必须为空"),
    ],
)
def test_seed_validation_rejects_invalid_channel_associations(table, field, value, message):
    changed = json.loads(render_init_sql.SEED.read_text())
    row = next(
        row for row in changed["tables"][table] if table != "channels" or row["id"] != "system"
    )
    row[field] = value
    with pytest.raises(ValueError, match=message):
        render_init_sql.validate_seed(changed)


def test_seed_validation_requires_system_channel_and_unique_channel_codes():
    original = json.loads(render_init_sql.SEED.read_text())
    changed = deepcopy(original)
    changed["tables"]["channels"] = [
        row for row in changed["tables"]["channels"] if row["id"] != "system"
    ]
    with pytest.raises(ValueError, match="必须包含系统渠道"):
        render_init_sql.validate_seed(changed)
    changed = deepcopy(original)
    tenant = next(row for row in changed["tables"]["channels"] if row["id"] != "system")
    changed["tables"]["channels"].append(
        {**tenant, "id": "other-channel", "channel_id": "other-channel"}
    )
    with pytest.raises(ValueError, match="渠道编码重复"):
        render_init_sql.validate_seed(changed)
    changed = deepcopy(original)
    indexes = changed["tables"]["channel_code_index"]
    indexes.append({**indexes[0], "id": "duplicate-index"})
    with pytest.raises(ValueError, match="目录缺失"):
        render_init_sql.validate_seed(changed)


def test_seed_contains_platform_and_channel_concurrency_limits_with_frozen_versions():
    seed = json.loads(render_init_sql.SEED.read_text())
    render_init_sql.validate_seed(seed)
    tables = seed["tables"]
    platform = tables["platform_limits"]
    assert len(platform) == 1
    limit = platform[0]
    assert limit["channel_id"] == "system"
    assert limit["limit_code"] == limit["kind"] == limit["unit"] == "concurrency"
    assert limit["limit_value"] == "20" and limit["status"] == "ACTIVE"
    assert limit["revision"] == 1 and limit["replaces_id"] is None
    assert "effective_at" not in limit
    tenants = {row["id"] for row in tables["channels"] if row["id"] != "system"}
    policies = tables["budget_policies"]
    assert len(policies) == len(tenants)
    assert {row["channel_id"] for row in policies} == tenants
    versions = {
        row["id"]: row
        for row in tables["resource_versions"]
        if row["resource_type"] == "budget_policy"
    }
    assert len(versions) == len(policies)
    for policy in policies:
        assert policy["scope_type"] == "channel"
        assert policy["scope_id"] == policy["channel_id"]
        assert policy["limit_value"] == "5" and policy["unit"] == "concurrency"
        assert policy["mode"] == "HARD" and policy["status"] == "ACTIVE"
        assert policy["currency"] is None and policy["thresholds"] == ["0.8", "1"]
        payload = BudgetCreate.model_validate(
            {key: policy[key] for key in BudgetCreate.model_fields}
        ).model_dump(mode="json")
        version = versions[policy["version_id"]]
        assert version["channel_id"] == policy["channel_id"]
        assert version["resource_type"] == "budget_policy"
        assert version["resource_id"] == policy["id"]
        assert version["state"] == "FROZEN" and version["content"] == payload
        assert version["version_label"] == "预算版本 1"
        assert version["revision"] == policy["revision"] == 1
        assert version["content_digest"] == digest({"content": payload, "output_schema": {}})
        assert version["dependencies"] == [] and version["dependencies_digest"] == digest([])
        assert version["output_schema"] == {}
        assert version["created_by"] == tables["platform_accounts"][0]["id"]
    data = render_init_sql.render_data()
    assert data.count("INSERT INTO platform_limits ") == 1
    assert data.count("INSERT INTO budget_policies ") == len(tenants)
    weather = load_weather(tables)
    combined = merged_tables(tables, weather["tables"])
    assert data.count("INSERT INTO resource_versions ") == len(combined["resource_versions"])
    assert "platform_quota_occupancies" not in data
    assert data.count("INSERT INTO admissions ") == len(weather["tables"]["admissions"])
    assert all(row["status"] == "RELEASED" for row in weather["tables"]["admissions"])
    assert "INSERT INTO budget_alerts " not in data


@pytest.mark.parametrize(
    ("table", "field", "value", "message"),
    [
        ("platform_limits", "channel_id", "other-channel", "系统渠道"),
        ("platform_limits", "limit_value", "0", "正整数"),
        ("platform_limits", "limit_value", "20.5", "正整数"),
        ("platform_limits", "limit_value", "NaN", "正整数"),
        ("platform_limits", "limit_value", "Infinity", "正整数"),
        ("platform_limits", "limit_value", 20.0, "十进制值必须使用字符串"),
        ("platform_limits", "unit", "requests", "并发初始版本"),
        ("platform_limits", "kind", "requests", "并发初始版本"),
        ("platform_limits", "status", "DISABLED", "并发初始版本"),
        ("platform_limits", "replaces_id", "old-limit", "并发初始版本"),
        ("platform_limits", "timezone", "Invalid/Timezone", "时区无效"),
        ("platform_limits", "effective_at", "2026-10-05T00:00:00+00:00", "字段与模型"),
        ("budget_policies", "channel_id", "system", "每个初始业务渠道"),
        ("budget_policies", "scope_id", "other-channel", "策略归属"),
        ("budget_policies", "limit_value", "0", "正整数上限"),
        ("budget_policies", "limit_value", "5.5", "正整数上限"),
        ("budget_policies", "limit_value", 5.0, "十进制值必须使用字符串"),
        ("budget_policies", "unit", "requests", "硬控制配置"),
        ("budget_policies", "mode", "ALERT_ONLY", "硬控制配置"),
        ("budget_policies", "status", "DISABLED", "硬控制配置"),
        ("budget_policies", "currency", "CNY", "硬控制配置"),
        ("budget_policies", "thresholds", [], "硬控制配置"),
        ("budget_policies", "thresholds", ["1.1"], "硬控制配置"),
        ("budget_policies", "version_id", "missing-version", "策略版本缺失"),
        ("resource_versions", "channel_id", "other-channel", "摘要或归属"),
        ("resource_versions", "resource_id", "other-policy", "摘要或归属"),
        ("resource_versions", "content", {}, "版本内容"),
        ("resource_versions", "content_digest", "wrong-digest", "摘要或归属"),
        ("resource_versions", "dependencies_digest", "wrong-digest", "摘要或归属"),
        ("resource_versions", "state", "DRAFT", "摘要或归属"),
        ("resource_versions", "created_by", "other-admin", "摘要或归属"),
    ],
)
def test_seed_validation_rejects_invalid_initial_concurrency_configuration(
    table, field, value, message
):
    changed = json.loads(render_init_sql.SEED.read_text())
    changed["tables"][table][0][field] = value
    with pytest.raises(ValueError, match=message):
        render_init_sql.validate_seed(changed)


@pytest.mark.parametrize("table", ["platform_limits", "budget_policies", "resource_versions"])
def test_seed_validation_requires_exactly_one_initial_concurrency_configuration(table):
    original = json.loads(render_init_sql.SEED.read_text())
    changed = deepcopy(original)
    changed["tables"][table] = []
    with pytest.raises(ValueError, match="初始数据缺失"):
        render_init_sql.validate_seed(changed)
    changed = deepcopy(original)
    rows = changed["tables"][table]
    rows.append({**rows[0], "id": "duplicate-configuration"})
    with pytest.raises(ValueError, match="平台并发限额|一份并发策略|策略版本缺失"):
        render_init_sql.validate_seed(changed)


def test_seed_contains_four_credential_free_provider_templates():
    seed = json.loads(render_init_sql.SEED.read_text())
    rows = seed["tables"]["provider_catalog"]
    expected = {
        "deepseek": ("DeepSeek", "https://api.deepseek.com"),
        "qwen": ("千问", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "doubao": ("豆包", "https://ark.cn-beijing.volces.com/api/v3"),
        "openai": ("OpenAI（ChatGPT）", "https://api.openai.com/v1"),
    }
    assert len(rows) == len(expected)
    assert {row["code"] for row in rows} == expected.keys()
    for row in rows:
        provider = ProviderView.model_validate({key: row[key] for key in ProviderView.model_fields})
        name, endpoint = expected[provider.code]
        assert row["channel_id"] == "system"
        assert provider.id == "provider_" + provider.code
        assert provider.name == name and provider.revision == 1
        assert provider.protocols == ["chat_completions"]
        assert PROTOCOLS[provider.protocols[0]].enabled
        assert provider.template_content == {
            "protocol": "chat_completions",
            "endpoint": endpoint,
            "timeout_seconds": 60,
        }
        assert validate_endpoint(provider.protocols[0], endpoint) == endpoint
    assert render_init_sql.render_data().count("INSERT INTO provider_catalog ") == 4


def test_seed_contains_deepseek_model_and_ciphertext_without_deployment_secrets():
    seed = json.loads(render_init_sql.SEED.read_text())
    render_init_sql.validate_seed(seed)
    tables = seed["tables"]
    (model,) = tables["models"]
    (connection,) = tables["model_connections"]
    (credential,) = tables["credentials"]
    assert model["provider_model_name"] == model["model_code"] == "deepseek-v4-flash"
    assert model["connection_id"] == connection["id"]
    assert model["capabilities"] == {} and model["verified_at"] is None
    assert connection["provider_id"] == "provider_deepseek"
    assert connection["endpoint"] == "https://api.deepseek.com"
    assert connection["credential_ref"] == credential["id"]
    assert connection["environment"] == credential["environment"] == "dev"
    assert len(bytes.fromhex(credential["ciphertext"])) > 28
    data = render_init_sql.render_data()
    assert "decode(" in data and "sk-" not in data
    assert "CREATIVITY_MODEL_ENCRYPTION_KEYS" not in data
    assert "198.18.0.36/32" in data  # 保留天气验证时的真实连接配置及摘要。
    assert len(tables["resource_references"]) == 1
    assert len(tables["source_links"]) == 3


@pytest.mark.parametrize(
    ("table", "field", "value"),
    [
        ("credentials", "channel_id", "system"),
        ("credentials", "ciphertext", "sk-test-plaintext"),
        ("credentials", "ciphertext", "abcd"),
        ("credentials", "environment", "prod"),
        ("model_connections", "credential_ref", "missing-credential"),
        ("model_connections", "channel_id", "other-channel"),
        ("model_connections", "health_status", "HEALTHY"),
        ("models", "connection_id", "missing-connection"),
        ("models", "capabilities", {"text": {"state": "SUPPORTED"}}),
        ("models", "provider_model_name", "changed-name"),
        ("resource_references", "target_version_id", "missing-version"),
        ("source_links", "channel_id", "other-channel"),
    ],
)
def test_seed_rejects_plaintext_and_broken_model_dependencies(table, field, value):
    seed = json.loads(render_init_sql.SEED.read_text())
    seed["tables"][table][0][field] = value
    with pytest.raises(ValueError):
        render_init_sql.validate_seed(seed)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("channel_id", "other-channel", "系统渠道"),
        ("id", "provider_other", "标识与编码"),
        ("protocols", ["chat_completions", "chat_completions"], "协议重复"),
        ("protocol", "responses", "模板只能包含"),
        ("api_key", "不可归档的凭据", "不含凭据"),
        ("endpoint", "https://api.deepseek.com/chat/completions", "基础地址不合法"),
        ("endpoint", "https://api.deepseek.com?api_key=secret", "基础地址不合法"),
        ("endpoint", "https://secret@api.deepseek.com", "基础地址不合法"),
        ("timeout_seconds", 0, "超时必须"),
        ("timeout_seconds", 601, "超时必须"),
        ("timeout_seconds", True, "超时必须"),
    ],
)
def test_seed_validation_rejects_invalid_provider_templates(field, value, message):
    changed = json.loads(render_init_sql.SEED.read_text())
    row = changed["tables"]["provider_catalog"][0]
    target = row if field in {"channel_id", "id", "protocols"} else row["template_content"]
    target[field] = value
    with pytest.raises(ValueError, match=message):
        render_init_sql.validate_seed(changed)


def test_seed_validation_rejects_duplicate_provider_codes():
    changed = json.loads(render_init_sql.SEED.read_text())
    providers = changed["tables"]["provider_catalog"]
    providers[1]["code"] = providers[0]["code"]
    with pytest.raises(ValueError, match="供应商编码重复"):
        render_init_sql.validate_seed(changed)


async def test_seed_default_password_and_persisted_role_associations():
    seed = json.loads(render_init_sql.SEED.read_text())
    account = seed["tables"]["platform_accounts"][0]
    hasher = PasswordHasher()
    assert await hasher.verify("qwerty123$%^", account["password_hash"])
    assert not await hasher.verify("wrong-password", account["password_hash"])
    assert account["role_id"] == account["platform_roles"][0] == "platform_admin"
    builtin = seed["tables"]["builtin_roles"]
    catalog = role_catalog_rows([], builtin, platform=True, all_scopes=True)
    for role in builtin:
        assert catalog[role["role_code"]]["menu_ids"] == role["menu_ids"]
    assert "menu:manage" in catalog["platform_admin"]["allowed_actions"]
    assert "menu_accounts" in catalog["platform_admin"]["menu_ids"]
    assert "menu_members" in catalog["channel_admin"]["menu_ids"]
    assert all(r["menu_ids"] is None for r in builtin if not r["account_assignable"])


def test_seed_validation_rejects_cross_channel_and_broken_associations():
    original = json.loads(render_init_sql.SEED.read_text())
    changed = deepcopy(original)
    changed["tables"]["platform_accounts"][0]["channel_id"] = "other-channel"
    with pytest.raises(ValueError, match="系统渠道"):
        render_init_sql.validate_seed(changed)
    changed = deepcopy(original)
    role = next(
        row for row in changed["tables"]["builtin_roles"] if row["role_code"] == "platform_admin"
    )
    role["menu_ids"].append("missing-menu")
    with pytest.raises(ValueError, match="菜单关联"):
        render_init_sql.validate_seed(changed)
    changed = deepcopy(original)
    changed["tables"]["platform_accounts"][0]["role_id"] = "channel_admin"
    with pytest.raises(ValueError, match="角色关联"):
        render_init_sql.validate_seed(changed)
    changed = deepcopy(original)
    changed["tables"]["iam_menus"][0]["name"] = "超" * 129
    with pytest.raises(ValueError, match="长度超限"):
        render_init_sql.validate_seed(changed)


def test_check_detects_stale_data_archive_independently(tmp_path, monkeypatch):
    monkeypatch.setattr(render_init_sql, "ROOT", tmp_path)
    schema = tmp_path / "init.sql"
    data = tmp_path / "init_data.sql"
    monkeypatch.setattr(render_init_sql, "ARCHIVE", schema)
    monkeypatch.setattr(render_init_sql, "DATA_ARCHIVE", data)
    monkeypatch.setattr(render_init_sql, "render", lambda: "结构归档")
    monkeypatch.setattr(render_init_sql, "render_data", lambda: "数据归档")
    monkeypatch.setattr("sys.argv", ["render_init_sql.py", "--check"])
    schema.write_text("结构归档")
    data.write_text("过期数据")
    with pytest.raises(SystemExit, match="init_data.sql"):
        render_init_sql.main()
    data.write_text("数据归档")
    render_init_sql.main()


@pytest.mark.parametrize("fault", ["failed", "date", "evidence", "session", "object"])
def test_weather_archive_rejects_incomplete_or_unverified_chain(fault):
    base = json.loads(render_init_sql.SEED.read_text())["tables"]
    weather = load_weather(base)
    run_id = weather["source"]["cases"]["天气如何？"]
    run = next(row for row in weather["tables"]["runs"] if row["id"] == run_id)
    result = next(
        row["payload"]
        for row in weather["tables"]["run_contents"]
        if row["id"] == run["result_ref"]
    )
    if fault == "failed":
        run["state"] = "FAILED"
    elif fault == "date":
        result["data"]["date"] = "2026-10-07"
    elif fault == "evidence":
        result["evidence_refs"] = []
    elif fault == "session":
        run["identity"]["token_digest"] = "不应恢复的登录会话"
    else:
        weather["objects"][0]["base64"] = "AA=="
    with pytest.raises(ValueError):
        validate_weather(weather, base)


def test_sql_contains_complete_skill_object_without_external_file_dependency():
    base = json.loads(render_init_sql.SEED.read_text())["tables"]
    weather = load_weather(base)
    assert archived_objects(render_init_sql.DATA_ARCHIVE) == weather["objects"]


@pytest.mark.parametrize("fault", ["missing", "scope", "blocked", "digest", "unverified"])
def test_weather_archive_requires_verified_content_barriers(fault):
    base = json.loads(render_init_sql.SEED.read_text())["tables"]
    weather = load_weather(base)
    barrier = weather["tables"]["recovery_barriers"][0]
    if fault == "missing":
        barrier["id"] = "wrong-scope"
    elif fault == "scope":
        barrier.update(subject_type="user", subject_id="other-user")
    elif fault == "blocked":
        barrier["state"] = "BLOCKED"
    elif fault == "digest":
        barrier["marker_digest"] = digest(["unreconciled-deletion"])
    else:
        barrier["verified_at"] = None
    with pytest.raises(ValueError, match="恢复屏障"):
        validate_weather(weather, base)
