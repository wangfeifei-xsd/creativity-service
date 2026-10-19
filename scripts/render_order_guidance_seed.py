"""离线校验订单协助配置、执行授权与技能对象，不访问当前环境。"""

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from mcp.types import Tool

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import validate_row
from creativity_service.core.deletion import barrier_id
from creativity_service.core.primitives import digest
from creativity_service.modules.agents.schemas import AgentDefinition
from creativity_service.modules.integrations.automation import owner
from creativity_service.modules.integrations.automation_schemas import ScheduleCreate
from creativity_service.modules.mcp.schemas import (
    McpAuthentication,
    McpAuthenticationInput,
    McpCreate,
)
from creativity_service.modules.mcp.transport import remote_tool
from creativity_service.modules.skills.schemas import SkillDefinition
from creativity_service.modules.tools.schemas import ToolDefinition
from creativity_service.storage import metadata
from scripts.render_archive import (
    object_bytes,
    typed_archive_values,
    validate_configuration_references,
)

GUIDANCE_SEED = Path(__file__).resolve().parents[1] / "sql/rental_order_guidance_data.json"
GUIDANCE_TABLES = frozenset(
    "agent_environment_states "
    "agents resource_versions resource_references release_mappings source_links tools "
    "prompts skills skill_files artifacts automation_schedules credentials mcp_connections "
    "mcp_discoveries mcp_imports model_routes model_connections models "
    "channel_environments channel_memberships resource_grants recovery_barriers".split()
)
OVERRIDE_TABLES = frozenset(
    "credentials mcp_connections model_connections model_routes models resource_versions "
    "channel_memberships resource_grants".split()
)


def merge_guidance(base: dict[str, Any], additions: dict[str, Any]) -> dict[str, Any]:
    """仅允许当前共享模型与租号连接覆盖早期配置，不混入历史执行记录。"""
    result = {name: {row["id"]: row for row in rows} for name, rows in base.items()}
    for name, rows in additions.items():
        current = result.setdefault(name, {})
        for row in rows:
            if row["id"] in current and name not in OVERRIDE_TABLES:
                raise ValueError(f"订单协助归档不能覆盖 {name}")
            current[row["id"]] = row
    return {name: sorted(rows.values(), key=lambda row: row["id"]) for name, rows in result.items()}


def validate_guidance(seed: dict[str, Any], base: dict[str, Any]) -> None:
    """确保空库恢复具备真实配置、完整依赖与原授权，不复制待执行任务。"""
    tables, source = seed["tables"], seed["source"]
    if set(tables) != GUIDANCE_TABLES or source["environment"] != "dev":
        raise ValueError("订单协助归档表清单或环境不正确")
    for name, rows in tables.items():
        if not rows or len({row["id"] for row in rows}) != len(rows):
            raise ValueError(f"{name} 归档为空或标识重复")
        for row in rows:
            values = typed_archive_values(metadata.tables[name], row)
            if set(values) != set(metadata.tables[name].c.keys()):
                raise ValueError(f"{name} 字段与模型不一致")
            validate_row(metadata.tables[name], values)
            if row["channel_id"] != source["channel_id"] or row.get("environment", "dev") != "dev":
                raise ValueError("订单协助归档不能混入其他渠道或环境")
    index = {
        name: {row["id"]: row for row in rows}
        for name, rows in merge_guidance(base, tables).items()
    }
    validate_workspace(tables, index, base)
    versions = index["resource_versions"]
    for row in tables["resource_versions"]:
        if not set(row["dependencies"]) <= versions.keys():
            raise ValueError("订单协助版本依赖缺失")
        if row["content_digest"] != digest(
            {"content": row["content"], "output_schema": row["output_schema"]}
        ):
            raise ValueError("订单协助版本摘要不一致")
    for row in tables["resource_references"]:
        if not {row["source_version_id"], row["target_version_id"]} <= versions.keys():
            raise ValueError("订单协助资源引用不完整")
    for row in tables["release_mappings"]:
        version = versions.get(row["version_id"], {})
        if (version.get("resource_id"), version.get("resource_type"), version.get("state")) != (
            row["resource_id"],
            row["resource_type"],
            "PUBLISHED",
        ):
            raise ValueError("订单协助发布映射不完整")
    agent = index["agents"][source["agent_id"]]
    for row in tables["resource_versions"]:
        if row["resource_type"] == "agent":
            definition = AgentDefinition.model_validate(row["content"])
            if not set(definition.bindings.ids()) <= versions.keys():
                raise ValueError("订单协助绑定缺失")
    schedules = tables["automation_schedules"]
    if len(schedules) != 1 or schedules[0]["id"] != source["schedule_id"]:
        raise ValueError("订单协助巡检计划缺失或重复")
    schedule = schedules[0]
    spec = ScheduleCreate.model_validate(schedule["spec"])
    identity = AuthContext.model_validate(schedule["identity"])
    if (
        spec.request.agent_code != agent["agent_code"]
        or spec.interval_seconds != 60
        or schedule["state"] != "ACTIVE"
        or schedule["owner_key"] != owner(identity)
        or identity.principal_type != "worker"
        or identity.actor_id not in index["platform_accounts"]
        or identity.scope != Scope(channel_id=source["channel_id"], environment="dev")
        or identity.session_id is not None
        or identity.token_digest is not None
        or identity.granted_actions
    ):
        raise ValueError("订单协助巡检身份或配置不一致")
    connection = index["mcp_connections"][source["mcp_connection_id"]]
    for archived_connection in tables["mcp_connections"]:
        validate_mcp_configuration(archived_connection, index)
    if len(tables["tools"]) != 7 or len(tables["mcp_imports"]) != 7:
        raise ValueError("订单协助七个工具必须完整归档")
    for tool in tables["tools"]:
        definition = ToolDefinition.model_validate(versions[tool["id"]]["content"])
        imported = index["mcp_imports"].get(definition.binding.adapter_key, {})
        discovery = index["mcp_discoveries"].get(imported.get("discovery_id"), {})
        if (
            imported.get("local_tool_id") != tool["id"]
            or imported.get("connection_id") != connection["id"]
            or definition.binding.connection_id != connection["id"]
            or imported.get("schema_hash") != definition.binding.implementation_version
            or discovery.get("connection_revision") != connection["configuration_revision"]
            or discovery.get("credential_revision") != connection["credential_revision"]
        ):
            raise ValueError("订单协助工具绑定或发现记录不完整")
        matches = [
            item
            for item in discovery["tool_definitions"]
            if item["name"] == imported["remote_tool_name"]
        ]
        if len(matches) != 1:
            raise ValueError("订单协助远端工具契约缺失或重复")
        remote = matches[0]
        # 按真实发现流程重新计算摘要，避免本地标题、旧快照和导入参数各自不一致。
        contract = remote_tool(
            Tool.model_validate(
                {
                    "name": remote["name"],
                    "inputSchema": remote["input_schema"],
                    "outputSchema": remote["output_schema"],
                    "annotations": remote["annotations"],
                    "_meta": {"creativity/purpose": remote["purpose"]},
                }
            )
        )
        if (
            remote["schema_hash"] != contract.schema_hash
            or discovery["schema_hashes"].get(remote["name"]) != contract.schema_hash
            or imported["schema_hash"] != contract.schema_hash
            or imported["input_schema"] != {**contract.input_schema, "additionalProperties": False}
            or definition.input_schema != imported["input_schema"]
            or definition.output_schema != contract.output_schema
            or versions[tool["id"]]["output_schema"] != definition.output_schema
        ):
            raise ValueError("订单协助工具契约、发现摘要与本地配置不一致")
        if definition.effect_type == "IDEMPOTENT_WRITE":
            policy = definition.write_policy
            if (
                policy is None
                or policy.authorization_mode != "preauthorized"
                or tuple(policy.allowed_agent_codes) != (agent["agent_code"],)
                or tuple(policy.allowed_principal_ids) != (identity.principal_id,)
                or policy.status_tool_version_id not in index["tools"]
                or policy.argument_constraints != definition.input_schema
            ):
                raise ValueError("订单协助写工具预授权不完整")
    validate_objects(seed, index)
    validate_configuration_references(index)


def validate_workspace(tables: dict[str, Any], index: dict[str, Any], base: dict[str, Any]) -> None:
    """恢复开发环境授权和完整删除屏障，避免业务配置依赖已移除的示例。"""
    scopes = {
        barrier_id(scope): scope
        for name, rows in tables.items()
        if name != "recovery_barriers" and "environment" in metadata.tables[name].c
        for row in rows
        if row.get("environment")
        for scope in [Scope(**{key: row.get(key) for key in Scope.model_fields})]
    }
    barriers = index["recovery_barriers"]
    if barriers.keys() != scopes.keys():
        raise ValueError("初始化内容范围的恢复屏障缺失或多余")
    for identifier, scope in scopes.items():
        row = barriers[identifier]
        if (
            any(row[key] != value for key, value in scope.model_dump().items())
            or row["state"] != "READY"
            or row["marker_digest"] != digest([])
            or row["verified_at"] is None
        ):
            raise ValueError("初始化恢复屏障范围、状态或删除摘要不正确")
    for row in tables["resource_grants"]:
        if row["environments"] != ["dev"] or row["resource_type"] == "run":
            raise ValueError("初始化授权只能开放开发环境配置，不能引用历史运行")
    admin = base["platform_accounts"][0]["id"]
    content_grants = [
        row
        for row in tables["resource_grants"]
        if row["resource_type"] in {"conversation", "memory"}
    ]
    if len(content_grants) != 2 or {row["resource_type"] for row in content_grants} != {
        "conversation",
        "memory",
    }:
        raise ValueError("初始管理员的会话与记忆原文授权缺失或重复")
    if any(
        row["grantee_type"] != "account"
        or row["grantee_id"] != admin
        or row["resource_id"] != "*"
        or row["allowed_actions"] != ["data:read_sensitive"]
        for row in content_grants
    ):
        raise ValueError("会话与记忆原文授权只能授予初始管理员及指定资源类型")


def validate_mcp_configuration(connection: dict[str, Any], index: dict[str, Any]) -> None:
    """连接、鉴权与发现配置须来自同一渠道环境和配置修订。"""
    McpCreate.model_validate({key: connection[key] for key in McpCreate.model_fields})
    authentication = McpAuthentication.model_validate(connection["authentication"])
    credential = index["credentials"].get(connection["credential_ref"], {})
    if (
        connection["status"] != "ENABLED"
        or connection["tested_revision"] != connection["configuration_revision"]
        or connection["discovered_revision"] != connection["configuration_revision"]
        or credential.get("revision") != connection["credential_revision"]
        or credential.get("purpose") != "mcp"
        or credential.get("state") != "ACTIVE"
        or credential.get("is_deleted") is not False
        or credential.get("channel_id") != connection["channel_id"]
        or credential.get("environment") != connection["environment"]
        or credential.get("ciphertext") is not None
        or credential.get("key_version") is not None
        or not credential.get("secret_value")
        or authentication.mode != "client_credentials"
    ):
        raise ValueError("归档 MCP 鉴权或验证修订不完整")
    McpAuthenticationInput.model_validate(
        {
            "revision": connection["revision"],
            "app_id": authentication.app_id,
            "token_endpoint": authentication.token_endpoint,
            "app_secret": credential["secret_value"],
        }
    )
    if not any(
        row["connection_id"] == connection["id"]
        and row["connection_revision"] == connection["configuration_revision"]
        and row["credential_revision"] == connection["credential_revision"]
        for row in index["mcp_discoveries"].values()
    ):
        raise ValueError("归档 MCP 缺少匹配当前配置的发现快照")


def validate_objects(seed: dict[str, Any], index: dict[str, Any]) -> None:
    """技能配置、索引和 ZIP 原文必须一致，避免只有数据库壳而无法加载。"""
    tables = seed["tables"]
    objects = {item["id"]: item for item in seed["objects"]}
    if len(objects) != len(seed["objects"]) or objects.keys() != {
        row["id"] for row in tables["artifacts"]
    }:
        raise ValueError("订单协助技能对象清单不完整")
    for skill in tables["skills"]:
        definition = SkillDefinition.model_validate(
            index["resource_versions"][skill["id"]]["content"]
        )
        item = objects.get(definition.artifact_id)
        artifact = index["artifacts"].get(definition.artifact_id)
        if (
            item is None
            or artifact is None
            or any(item[key] != artifact[key] for key in item if key != "base64")
        ):
            raise ValueError("订单协助技能对象元数据不一致")
        files = [row for row in tables["skill_files"] if row["version_id"] == skill["id"]]
        if {file.relative_path for file in definition.files} != {
            row["relative_path"] for row in files
        }:
            raise ValueError("订单协助技能文件清单不一致")
        with zipfile.ZipFile(io.BytesIO(object_bytes(item))) as package:
            for row in files:
                content = package.read(row["relative_path"])
                if (
                    row["artifact_id"] != item["id"]
                    or len(content) != row["size_bytes"]
                    or hashlib.sha256(content).hexdigest() != row["sha256"]
                ):
                    raise ValueError("订单协助技能文件摘要不一致")


def load_guidance(base: dict[str, Any]) -> dict[str, Any]:
    seed = json.loads(GUIDANCE_SEED.read_text(encoding="utf-8"))
    validate_guidance(seed, base)
    return seed
