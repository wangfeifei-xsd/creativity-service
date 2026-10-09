"""移除模型连接 IP 范围，同步清理配置快照及其摘要。"""

import hashlib
import json
from copy import deepcopy

import sqlalchemy as sa

from alembic import context, op

revision = "0047_remove_model_networks"
down_revision = "0046_mcp_plain_credentials"
branch_labels = None
depends_on = None


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def config_digest(model, connection, *, legacy=False):
    """冻结升级前后摘要算法，不依赖后续版本的运行时代码。"""
    payload = {
        "model_validation_revision": model.get("validation_revision", 1),
        "connection_validation_revision": connection.get("validation_revision", 1),
        "connection": {k: connection[k] for k in ("id", "protocol", "endpoint", "timeout_seconds")},
        "model": {
            k: model[k]
            for k in (
                "connection_id",
                "provider_model_name",
                "context_limit",
                "parameters",
                "parameter_allowlist",
            )
        },
    }
    if legacy and connection.get("allowed_networks"):
        payload["connection"]["allowed_networks"] = connection["allowed_networks"]
    return digest(payload)


def transform_configuration(tables):
    """只转换模型平台字段；保留原验证结论，旧配置不会获得新能力证据。"""
    result = deepcopy(tables)
    versions = {row["id"]: row for row in tables["resource_versions"]}
    connections = {row["id"]: row for row in tables["model_connections"]}
    mapping = {}

    def remember(model, connection):
        mapping[config_digest(model, connection, legacy=True)] = config_digest(model, connection)

    def remember_versions(model_id, connection_id):
        model, connection = versions.get(model_id), versions.get(connection_id)
        if model and connection and model["resource_type"] == "model":
            remember(model["content"], connection["content"] | {"id": connection["resource_id"]})

    for model in tables["models"]:
        if model["connection_id"] in connections:
            remember(model, connections[model["connection_id"]])
    for version in versions.values():
        content = version["content"]
        if version["resource_type"] == "model":
            remember_versions(version["id"], content.get("connection_version_id"))
        elif version["resource_type"] == "model_route":
            for model in content.get("models", []):
                remember_versions(model.get("model_version_id"), model.get("connection_version_id"))

    for row in result["model_connections"]:
        row.pop("allowed_networks", None)
        if (row.get("health_reason") or "").startswith(
            ("模型地址不在允许的 IP 范围内", "模型地址未通过网络访问校验")
        ):
            row.update(health_status="UNKNOWN", health_reason=None, health_checked_at=None)
    for row in result["models"]:
        for capability in (row.get("capabilities") or {}).values():
            old = capability.get("config_digest")
            if old in mapping:
                capability["config_digest"] = mapping[old]

    rewritten = {row["id"]: row for row in result["resource_versions"]}
    visiting, finished = set(), set()

    def visit(identifier):
        if identifier in finished or identifier not in rewritten:
            return
        if identifier in visiting:
            raise RuntimeError("版本依赖有环，模型网络字段迁移已回滚")
        visiting.add(identifier)
        row, old = rewritten[identifier], versions[identifier]
        dependencies = row.get("dependencies") or []
        for dep in dependencies:
            visit(dep)
        row["content"] = clean_content(row["resource_type"], row["content"], mapping)
        if row["content"] != old["content"]:
            row["content_digest"] = digest(
                {"content": row["content"], "output_schema": row["output_schema"]}
            )
        if dependencies and all(dep in versions for dep in dependencies):

            def resolved(records):
                return [
                    {
                        "version_id": dep,
                        **{
                            key: records[dep][key]
                            for key in ("content_digest", "dependencies_digest")
                        },
                    }
                    for dep in dependencies
                ]

            # 草稿仅摘要依赖标识；发布版本摘要依赖内容，按原算法分别维护。
            if old["dependencies_digest"] == digest(resolved(versions)):
                row["dependencies_digest"] = digest(resolved(rewritten))
        for key in ("content_digest", "dependencies_digest"):
            if old[key] != row[key]:
                mapping[old[key]] = row[key]
        visiting.remove(identifier)
        finished.add(identifier)

    for identifier in rewritten:
        visit(identifier)
    return result, mapping


def clean_model(value, mapping):
    result = deepcopy(value)
    result.pop("allowed_networks", None)
    if result.get("config_digest") in mapping:
        result["config_digest"] = mapping[result["config_digest"]]
    return result


def clean_content(kind, value, mapping):
    result = deepcopy(value)
    if kind == "model_connection":
        result.pop("allowed_networks", None)
    elif kind == "model":
        result["config_digest"] = mapping.get(
            result.get("config_digest"), result.get("config_digest")
        )
    elif kind == "model_route":
        result["models"] = [clean_model(model, mapping) for model in result.get("models", [])]
    return result


def clean_versions(values, mapping):
    result = deepcopy(values)
    for previous, row in zip(values, result, strict=True):
        row["content"] = clean_content(row["resource_type"], row["content"], mapping)
        for key in ("content_digest", "dependencies_digest"):
            row[key] = mapping.get(row[key], row[key])
        if row["content"] != previous["content"]:
            row["content_digest"] = digest(
                {"content": row["content"], "output_schema": row["output_schema"]}
            )
    return result


def remap_manifest(value, mapping):
    if isinstance(value, list):
        return [remap_manifest(item, mapping) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: mapping.get(item, item)
        if key.endswith("_digest") and isinstance(item, str)
        else remap_manifest(item, mapping)
        for key, item in value.items()
    }


def clean_spec(value, mapping):
    result = deepcopy(value)
    if not isinstance(result, dict) or not result.get("payload_json"):
        return result
    payload = json.loads(result["payload_json"])
    old_versions_digest = digest(payload.get("versions", []))
    if "versions" in payload:
        payload["versions"] = clean_versions(payload["versions"], mapping)
    if payload.get("runtime", {}).get("model"):
        payload["runtime"]["model"] = clean_model(payload["runtime"]["model"], mapping)
    execution = payload.get("runtime", {}).get("execution")
    if isinstance(execution, dict) and execution.get("configuration"):
        execution["configuration"] = clean_model(execution["configuration"], mapping)
    if "manifest" in payload:
        payload["manifest"] = remap_manifest(payload["manifest"], mapping)
        result["dependencies_digest"] = digest(payload["manifest"])
    elif value["dependencies_digest"] == old_versions_digest:
        result["dependencies_digest"] = digest(payload["versions"])
    else:
        result["dependencies_digest"] = mapping.get(
            result["dependencies_digest"], result["dependencies_digest"]
        )
    result["content_digest"] = mapping.get(result["content_digest"], result["content_digest"])
    previous = [value["content_digest"], value["dependencies_digest"]]
    if value["candidate_digest"] == digest(previous):
        result["candidate_digest"] = digest(
            [result["content_digest"], result["dependencies_digest"]]
        )
    elif value["candidate_digest"] == digest({"content": previous[0], "dependencies": previous[1]}):
        result["candidate_digest"] = digest(
            {
                "content": result["content_digest"],
                "dependencies": result["dependencies_digest"],
            }
        )
    result["payload_json"] = canonical(payload)
    return result


def rows(connection, table, *conditions):
    """按主标识分页，避免一次读入全部历史运行内容。"""
    cursor = ""
    while (
        batch := connection.execute(
            sa.select(table).where(table.c.id > cursor, *conditions).order_by(table.c.id).limit(500)
        )
        .mappings()
        .all()
    ):
        yield from batch
        cursor = batch[-1]["id"]


def write(connection, table, old, values):
    changed = {key: value for key, value in values.items() if old[key] != value}
    if changed:
        connection.execute(
            table.update()
            .where(table.c.channel_id == old["channel_id"], table.c.id == old["id"])
            .values(**changed)
        )


def migrate_data(connection):
    metadata = sa.MetaData()
    metadata.reflect(
        connection,
        only=[
            "channels",
            "model_connections",
            "models",
            "resource_versions",
            "model_tests",
            "release_snapshots",
            "run_contents",
            "agent_candidates",
        ],
    )
    tables = metadata.tables
    for channel in rows(connection, tables["channels"]):
        channel_id = channel["id"]
        saved = {}
        for name in ("model_connections", "models", "resource_versions"):
            saved[name] = []
            for row in rows(connection, tables[name], tables[name].c.channel_id == channel_id):
                if len(saved[name]) >= 10000:
                    raise RuntimeError("单渠道配置超过一万条，请分批整理历史版本后重试迁移")
                saved[name].append(dict(row))
        updated, mapping = transform_configuration(saved)
        for name, records in updated.items():
            for old, new in zip(saved[name], records, strict=True):
                write(connection, tables[name], old, new)
        for name in ("model_tests", "release_snapshots", "run_contents", "agent_candidates"):
            table = tables[name]
            conditions = [table.c.channel_id == channel_id]
            if name == "run_contents":
                conditions.append(table.c.kind == "execution_spec")
            for row in rows(connection, table, *conditions):
                values = {}
                if name == "model_tests":
                    execution = deepcopy(row["execution"])
                    if execution and execution.get("configuration"):
                        execution["configuration"] = clean_model(
                            execution["configuration"], mapping
                        )
                    values = {
                        "execution": execution,
                        "config_digest": mapping.get(row["config_digest"], row["config_digest"]),
                    }
                elif name == "release_snapshots":
                    versions = clean_versions(row["versions"], mapping)
                    values = {"versions": versions, "dependencies_digest": digest(versions)}
                elif name == "run_contents":
                    values = {"payload": clean_spec(row["payload"], mapping)}
                elif name == "agent_candidates":
                    spec = clean_spec(row["spec"], mapping)
                    values = {
                        "spec": spec,
                        **{
                            key: spec[key]
                            for key in (
                                "content_digest",
                                "dependencies_digest",
                                "candidate_digest",
                            )
                        },
                    }
                write(connection, table, row, values)


def upgrade():
    if context.is_offline_mode():
        # 非空库需要在线维护模型与版本摘要；空库可直接执行结构迁移。
        op.execute(
            "SELECT 1 / CASE WHEN EXISTS (SELECT 1 FROM model_connections) "
            "OR EXISTS (SELECT 1 FROM resource_versions) THEN 0 ELSE 1 END"
        )
    else:
        migrate_data(op.get_bind())
    op.drop_column("model_connections", "allowed_networks")


def downgrade():
    raise RuntimeError("模型 IP 范围及相关快照字段已移除，回退须恢复升级前备份")
