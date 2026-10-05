"""离线检查两份初始化归档、冻结凭据与控制面关联。"""

import json
from copy import deepcopy

import pytest

from creativity_service.core.auth.passwords import PasswordHasher
from creativity_service.modules.iam.repositories import role_catalog_rows
from scripts import render_init_sql


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
