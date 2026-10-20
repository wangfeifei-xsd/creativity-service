"""离线校验销售推荐配置及文件 URL 技能包，保留实际发布状态。"""

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from mcp.types import Tool

from creativity_service.core.context import Scope
from creativity_service.core.database import validate_row
from creativity_service.core.deletion import barrier_id
from creativity_service.core.primitives import digest
from creativity_service.modules.agents.schemas import AgentDefinition
from creativity_service.modules.agents.validation import static_issues, validate_prompt_dependencies
from creativity_service.modules.mcp.transport import remote_tool
from creativity_service.modules.prompts.schemas import PromptContent
from creativity_service.modules.skills.packages import unpack
from creativity_service.modules.skills.schemas import SkillDefinition
from creativity_service.modules.tools.schemas import ToolDefinition
from creativity_service.storage import metadata
from scripts.render_archive import (
    object_bytes,
    typed_archive_values,
    validate_configuration_references,
)
from scripts.render_order_guidance_seed import validate_mcp_configuration
from scripts.render_sales_access import ACCESS_TABLES, validate_sales_access

SALES_SEED = Path(__file__).resolve().parents[1] / "sql/sales_recommendation_data.json"
SALES_TABLES = frozenset(
    "agent_environment_states "
    "agents prompts skills tools skill_files resource_versions resource_references "
    "release_mappings source_links mcp_imports".split()
) | ACCESS_TABLES
RESOURCE_TABLES = {"agent": "agents", "prompt": "prompts", "skill": "skills", "tool": "tools"}


def merge_sales(base: dict[str, Any], additions: dict[str, Any]) -> dict[str, Any]:
    """只追加独立销售配置，共享路由和 MCP 由已有归档提供。"""
    result = {name: {row["id"]: row for row in rows} for name, rows in base.items()}
    for name, rows in additions.items():
        current = result.setdefault(name, {})
        for row in rows:
            if row["id"] in current:
                raise ValueError(f"销售推荐归档不能覆盖 {name}")
            current[row["id"]] = row
    return {name: sorted(rows.values(), key=lambda row: row["id"]) for name, rows in result.items()}


def validate_url_objects(items: list[dict[str, Any]], index: dict[str, Any]) -> None:
    """同时核对技能定义、文件索引及 ZIP 内容，拒绝仅保留开发机链接。"""
    versions = index["resource_versions"]
    expected = {
        row["id"]
        for row in versions.values()
        if row["resource_type"] == "skill" and row["content"].get("package_url")
    }
    if len({item["id"] for item in items}) != len(items) or {i["id"] for i in items} != expected:
        raise ValueError("销售推荐技能对象清单不完整")
    for item in items:
        version = versions[item["id"]]
        definition = SkillDefinition.model_validate(version["content"])
        key = item["object_key"]
        url = urlsplit(item["package_url"])
        if (
            item["kind"] != "skill_package"
            or item["channel_id"] != version["channel_id"]
            or item["environment"] != "dev"
            or item["package_url"] != definition.package_url
            or item["content_type"] != "application/zip"
            or not key.startswith(f"channels/{item['channel_id']}/files/")
            or any(part in {"", ".", ".."} for part in key.split("/"))
            or "\\" in key
            or url.scheme != "https"
            or not url.netloc
            or url.query
            or url.fragment
            or not unquote(url.path).endswith("/" + key)
        ):
            raise ValueError("销售推荐技能对象归属或文件地址不一致")
        package = unpack(object_bytes(item), definition)
        if (
            package.package_hash != definition.package_hash
            or package.manifest != definition.files
            or package.metadata != definition.metadata
        ):
            raise ValueError("销售推荐技能包正文或文件摘要不一致")
        files = [r for r in index["skill_files"].values() if r["version_id"] == version["id"]]
        if len(files) != len(definition.files) or {r["relative_path"] for r in files} != {
            f.relative_path for f in definition.files
        }:
            raise ValueError("销售推荐技能文件索引不完整")
        manifest = {f.relative_path: f for f in definition.files}
        for row in files:
            expected_file = manifest[row["relative_path"]].model_dump()
            if (
                row["artifact_id"] is not None
                or row["package_url"] != definition.package_url
                or row["channel_id"] != item["channel_id"]
                or any(row[k] != v for k, v in expected_file.items())
            ):
                raise ValueError("销售推荐技能文件索引与正文不一致")


def validate_sales(seed: dict[str, Any], base: dict[str, Any]) -> None:
    """配置白名单、发布映射和依赖闭合均须通过，运行历史不能进入预置。"""
    tables, source = seed["tables"], seed["source"]
    if set(tables) != SALES_TABLES or source["environment"] != "dev":
        raise ValueError("销售推荐归档表清单或环境不正确")
    scope = Scope(channel_id=source["channel_id"], environment="dev")
    for name, rows in tables.items():
        if len({row["id"] for row in rows}) != len(rows):
            raise ValueError(f"{name} 标识重复")
        for row in rows:
            if set(row) != set(metadata.tables[name].c.keys()):
                raise ValueError(f"{name} 字段与模型不一致")
            validate_row(metadata.tables[name], typed_archive_values(metadata.tables[name], row))
            expected_channel = "system" if name == "key_identity_index" else scope.channel_id
            if (
                row["channel_id"] != expected_channel
                or row.get("environment", "dev") != "dev"
                or row.get("subject_type") is not None
                or row.get("subject_id") is not None
                or row["is_deleted"]
            ):
                raise ValueError("销售推荐只能归档同渠道开发环境的未删除配置")
    index = {name: {r["id"]: r for r in rows} for name, rows in merge_sales(base, tables).items()}
    barrier = index["recovery_barriers"].get(barrier_id(scope), {})
    if (
        barrier.get("state") != "READY"
        or barrier.get("marker_digest") != digest([])
        or barrier.get("verified_at") is None
    ):
        raise ValueError("销售推荐恢复屏障缺失或未核验")
    versions = index["resource_versions"]
    expected_versions = {
        source["version_id"],
        *(r["id"] for name in ("prompts", "skills", "tools") for r in tables[name]),
    }
    if {r["id"] for r in tables["resource_versions"]} != expected_versions:
        raise ValueError("销售推荐配置版本不完整或包含历史版本")
    for row in tables["resource_versions"]:
        kind = row["resource_type"]
        if (
            kind not in RESOURCE_TABLES
            or row["resource_id"] not in {r["id"] for r in tables[RESOURCE_TABLES[kind]]}
            or row["state"] != ("CURRENT" if kind == "agent" else "PUBLISHED")
            or not set(row["dependencies"]) <= versions.keys()
            or row["content_digest"]
            != digest({"content": row["content"], "output_schema": row["output_schema"]})
            or row["dependencies_digest"] != digest(sorted(set(row["dependencies"])))
        ):
            raise ValueError("销售推荐配置状态、摘要或依赖不一致")
    if len(tables["agents"]) != 1 or tables["agents"][0]["id"] != source["agent_id"]:
        raise ValueError("销售推荐智能体主档缺失或重复")
    states = tables["agent_environment_states"]
    if (
        tables["agents"][0]["status"] != "ACTIVE"
        or len(states) != 1
        or states[0]["agent_id"] != source["agent_id"]
        or states[0]["is_online"] is not True
    ):
        raise ValueError("销售推荐闭环初始化要求智能体已上线且启用")
    agent_version = versions[source["version_id"]]
    definition = AgentDefinition.model_validate(agent_version["content"])
    if (
        agent_version["resource_id"] != source["agent_id"]
        or agent_version["output_schema"] != definition.output_schema
        or set(agent_version["dependencies"]) != set(definition.dependency_ids())
        or static_issues(definition)
    ):
        raise ValueError("销售推荐流程或节点依赖不完整")
    for row in tables["prompts"]:
        validate_prompt_dependencies(
            PromptContent.model_validate(versions[row["id"]]["content"]), definition
        )
    expected_releases = expected_versions - {source["version_id"]}
    if {r["version_id"] for r in tables["release_mappings"]} != expected_releases or len(
        tables["release_mappings"]
    ) != len(expected_releases):
        raise ValueError("销售推荐依赖发布映射缺失，智能体使用单份当前配置")
    for row in tables["release_mappings"]:
        version = versions[row["version_id"]]
        if (row["resource_type"], row["resource_id"]) != (
            version["resource_type"],
            version["resource_id"],
        ):
            raise ValueError("销售推荐发布映射与资源不一致")
    connection = index["mcp_connections"][source["mcp_connection_id"]]
    validate_mcp_configuration(connection, index)
    if len(tables["tools"]) != 1 or len(tables["mcp_imports"]) != 1:
        raise ValueError("销售推荐目录工具或 MCP 导入缺失")
    tool = ToolDefinition.model_validate(versions[tables["tools"][0]["id"]]["content"])
    imported = index["mcp_imports"].get(tool.binding.adapter_key, {})
    discovery = index["mcp_discoveries"].get(imported.get("discovery_id"), {})
    remote = [
        r
        for r in discovery.get("tool_definitions", [])
        if r["name"] == imported.get("remote_tool_name")
    ]
    if (
        imported.get("local_tool_id") != tables["tools"][0]["id"]
        or imported.get("connection_id") != connection["id"]
        or tool.binding.connection_id != connection["id"]
        or discovery.get("connection_revision") != connection["configuration_revision"]
        or discovery.get("credential_revision") != connection["credential_revision"]
        or len(remote) != 1
    ):
        raise ValueError("销售推荐目录工具与 MCP 连接或发现不一致")
    contract = remote_tool(
        Tool.model_validate(
            {
                "name": remote[0]["name"],
                "inputSchema": remote[0]["input_schema"],
                "outputSchema": remote[0]["output_schema"],
                "annotations": remote[0]["annotations"],
                "_meta": {"creativity/purpose": remote[0]["purpose"]},
            }
        )
    )
    if (
        tool.effect_type != "READ_ONLY"
        or tool.subject_requirements.required
        or tool.input_schema != imported.get("input_schema")
        or tool.input_schema != {**contract.input_schema, "additionalProperties": False}
        or tool.output_schema != contract.output_schema
        or versions[tables["tools"][0]["id"]]["output_schema"] != tool.output_schema
        or any(
            value != contract.schema_hash
            for value in (
                remote[0]["schema_hash"],
                discovery["schema_hashes"].get(remote[0]["name"]),
                imported.get("schema_hash"),
                tool.binding.implementation_version,
            )
        )
    ):
        raise ValueError("销售推荐目录工具契约、只读或主体要求不一致")
    validate_url_objects(seed["objects"], index)
    validate_configuration_references(index)
    validate_sales_access(seed, index)


def load_sales(base: dict[str, Any]) -> dict[str, Any]:
    seed: dict[str, Any] = json.loads(SALES_SEED.read_text(encoding="utf-8"))
    validate_sales(seed, base)
    return seed
