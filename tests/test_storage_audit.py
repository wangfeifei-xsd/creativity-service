"""迁移源码与模型归档检查不依赖外部服务。"""

from pathlib import Path

from creativity_service.core.database.audit import (
    audit_catalog,
    audit_definitions,
    audit_sources,
    check_sql,
)


def test_storage_definitions_catalog_and_sources():
    assert audit_definitions() == []
    assert audit_catalog(Path.cwd()) == []
    assert audit_sources(Path.cwd()) == []


def test_audit_detects_implicit_uniqueness_and_framework_ddl(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (tmp_path / "alembic").mkdir()
    (source / "bad.py").write_text('metadata.create_all(engine)\nColumn("id", primary_key=True)\n')
    assert len(audit_sources(tmp_path)) == 2
    assert check_sql("CREATE TABLE x (id integer PRIMARY KEY)")
    assert check_sql("CREATE UNIQUE INDEX bad ON x (id)")
    assert not check_sql("COMMENT ON TABLE x IS '不使用 UNIQUE';")
