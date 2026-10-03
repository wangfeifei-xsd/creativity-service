"""沿渠道来源图定位派生记录，额外覆盖运行所属的不可独立使用内容。"""

from typing import Any

from creativity_service.core.context import Scope
from creativity_service.core.database import Repository, UnitOfWork
from creativity_service.core.deletion.resources import CONTENT_TABLES
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.data_lifecycle.repository import rows, scope_of
from creativity_service.modules.evaluations.independent import surviving_sources
from creativity_service.storage import metadata

TARGETS = CONTENT_TABLES
LABELS = {
    "conversation": "会话",
    "message": "消息",
    "summary": "摘要",
    "context": "上下文",
    "memory": "记忆",
    "run": "运行内容",
    "artifact": "文件",
    "snapshot": "运行快照",
    "version": "资源版本",
    "tool_call": "工具调用",
    "evidence": "工具证据",
    "usage_export": "用量导出",
    "evaluation_case": "评测样本",
    "evaluation_fixture": "评测夹具",
    "evaluation_result": "评测结果",
    "evaluation": "评测报告",
    "evaluation_dataset_version": "样本版本",
    "prompt_sample": "提示词样本",
    "prompt_test": "提示词测试",
    "model_test": "模型测试",
    "skill_file": "技能文件",
    "skill_test": "技能测试",
    "agent_candidate": "智能体候选快照",
}


async def affected(
    uow: UnitOfWork,
    scope: Scope,
    kind: str,
    identifier: str,
    seeds: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    channel_id = scope.channel_id
    links = await rows(uow.connection, channel_id, "source_links")
    records: dict[tuple[str, str], dict[str, Any]] = {}
    edges: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for link in links:
        edges.setdefault((link["source_type"], link["source_id"]), set()).add(
            (link["derived_type"], link["derived_id"])
        )
    for target, name in TARGETS.items():
        for row in await rows(uow.connection, channel_id, name):
            key = (target, row["id"])
            records[key] = row
            for parent, field in (
                ("run", "run_id"),
                ("conversation", "conversation_id"),
                ("evaluation_case", "case_id"),
            ):
                if row.get(field) and key != (parent, row[field]):
                    edges.setdefault((parent, row[field]), set()).add(key)
            if target in {"message", "tool_call"} and row.get("run_id"):
                # 输入消息与工具返回已进入运行内容；删除其中一项也须清理运行副本。
                edges.setdefault(key, set()).add(("run", row["run_id"]))
            if target == "run":
                edges.setdefault(key, set()).add(("snapshot", row["release_snapshot_id"]))
            if target == "evaluation_result":
                if row.get("run_id"):
                    edges.setdefault(key, set()).add(("run", row["run_id"]))
                edges.setdefault(key, set()).add(("evaluation", row["evaluation_id"]))
    reached: set[tuple[str, str]] = set()
    pending = (
        [(kind, identifier)]
        if kind != "scope"
        else [
            key
            for key, row in records.items()
            if all(row.get(k) == v for k, v in scope.model_dump().items())
        ]
    )
    pending.extend(seeds or [])
    while pending:
        ref = pending.pop()
        if ref in reached:
            continue
        reached.add(ref)
        if len(reached) > 10000:
            raise ServiceError("SOURCE_GRAPH_LIMIT", "删除影响过多，请分批核查", 503)
        # 汇总报告的可复现性变化不等于删除其他独立样本及其子运行。
        if ref[0] != "evaluation" or ref == (kind, identifier):
            pending.extend(edges.get(ref, set()))
    result = []
    for target, record_id in sorted(reached):
        found = records.get((target, record_id))
        if found is None:
            if target not in TARGETS:
                raise ServiceError("CLEANUP_HANDLER_MISSING", "来源图存在未登记的清理类型", 503)
            continue
        own = scope_of(found) if "environment" in found else scope
        mode = (
            "UPDATE_SOURCES"
            if target == "memory" and (target, record_id) != (kind, identifier)
            else "DELETE"
        )
        if target in {"evaluation", "evaluation_dataset_version"} and (target, record_id) != (
            kind,
            identifier,
        ):
            mode = "REPORT_ONLY"
        if (
            target == "evaluation_case"
            and found.get("payload")
            and (target, record_id) != (kind, identifier)
        ):
            valid = await surviving_sources(
                UnitOfWork(uow.connection, own, uow.keys),
                own,
                found["payload"],
                [
                    link
                    for link in links
                    if link["derived_type"] == target and link["derived_id"] == record_id
                ],
            )
            if valid:
                mode = "UPDATE_SOURCES"
        result.append(
            {
                "resource_type": target,
                "resource_id": record_id,
                "scope": own.model_dump(),
                "mode": mode,
            }
        )
    return result


async def locate(uow: UnitOfWork, scope: Scope, kind: str, identifier: str) -> dict[str, Any]:
    name = TARGETS.get(kind)
    if name is None:
        raise ServiceError("VALIDATION_ERROR", "不支持此类内容删除", 422)
    row = await Repository(metadata.tables[name], scope).get(uow.connection, identifier)
    if row is None:
        raise ServiceError("NOT_FOUND", "当前范围没有此内容", 404)
    return row
