"""运行快照与有界轨迹分页；内容和诊断权限分别复核。"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import Artifact, BusinessResult, ResultEnvelope, RunEvent
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.runs.base import RunKernel
from creativity_service.modules.runs.repositories import required, rows, verify_scope
from creativity_service.modules.runs.schemas import (
    LABELS,
    TERMINAL,
    ExecutionPolicy,
    RunSummary,
    TracePage,
)
from creativity_service.modules.tools.tables import metadata as tool_metadata
from creativity_service.modules.tools.validation import artifact_references
from creativity_service.modules.usage import repositories as usage_repo


class QueryService(RunKernel):
    async def filter_options(self, context: AuthContext) -> dict[str, Any]:
        from creativity_service.modules.channels.tables import metadata as channels

        await self.authorization.require(context, "run:read", "scope")
        filters = context.scope.model_dump(exclude={"channel_id"})
        if context.principal_type == "management" and context.scope.subject_id is None:
            filters.pop("subject_type", None)
            filters.pop("subject_id", None)
        async with self.engine.connect() as connection:
            found = await rows(connection, "runs", context.scope.channel_id, **filters)
        visible = []
        for row in found:
            try:
                effective = await self.access_context(context, row["id"])
                await self.authorization.require(effective, "run:read", row["id"])
            except ServiceError as exc:
                if exc.status in {403, 404}:
                    continue
                raise
            visible.append(row)
        async with self.engine.connect() as connection:
            keys = []
            for identifier in sorted({r["key_id"] for r in visible if r["key_id"]}):
                key = await Repository(channels.tables["channel_keys"], context.scope).get(
                    connection, identifier
                )
                if key:
                    keys.append({"value": identifier, "label": key["name"]})
        return {
            "agents": [
                {"value": key, "label": name}
                for key, name in sorted(
                    {r["agent_id"]: r["agent_name"] for r in visible}.items(), key=lambda v: v[1]
                )
            ],
            "keys": keys,
            "errors": [
                {"value": code, "label": message}
                for code, message in sorted(
                    {
                        r["error"]["code"]: r["error"]["message"] for r in visible if r["error"]
                    }.items()
                )
            ],
        }

    @staticmethod
    def owner(context: AuthContext, row: dict[str, Any]) -> None:
        verify_scope(row, context.scope)
        if context.client_id and context.client_id != row["client_id"]:
            raise ServiceError("NOT_FOUND", "运行记录不存在", 404)

    async def get_run(self, context: AuthContext, run_id: str) -> ResultEnvelope:
        context = await self.access_context(context, run_id)
        await self.authorization.require(context, "run:read", run_id)
        await self.authorization.require(context, "run:content", run_id)
        async with transaction(self.engine, context.scope, self.keys(context, run_id)) as uow:
            row = await self.locked_run(uow, run_id)
            self.owner(context, row)
            await self.guard(uow, row)
            result = None
            partial = None
            if row["partial_output_ref"]:
                partial = (
                    await required(
                        uow.connection,
                        "run_contents",
                        context.scope.channel_id,
                        id=row["partial_output_ref"],
                        run_id=run_id,
                    )
                )["payload"]
            if row["result_ref"]:
                content = await required(
                    uow.connection,
                    "run_contents",
                    context.scope.channel_id,
                    id=row["result_ref"],
                    run_id=run_id,
                )
                result = BusinessResult.model_validate(content["payload"])
            usages = await usage_repo.rows(
                uow.connection, "usage_records", context.scope.channel_id, run_id=run_id
            )
            envelope = ResultEnvelope(
                channel_id=context.scope.channel_id,
                run_id=run_id,
                state=row["state"],
                state_label=LABELS[row["state"]],
                release_snapshot_id=row["release_snapshot_id"],
                result=result,
                partial_output=partial if row["state"] != "SUCCEEDED" else None,
                error=row["error"],
                artifacts=(),
                usage_summary={
                    "attempt_count": len(usages),
                    "pending_count": sum(u["state"] == "PENDING" for u in usages),
                    "missing_count": sum(u["usage_status"] == "MISSING" for u in usages),
                    "unpriced_count": sum(u["pricing_status"] == "UNPRICED" for u in usages),
                    "complete": all(u["state"] == "SETTLED" for u in usages),
                    "input_tokens": sum(u["input_tokens"] for u in usages)
                    if usages and all(u["input_tokens"] is not None for u in usages)
                    else None,
                    "output_tokens": sum(u["output_tokens"] for u in usages)
                    if usages and all(u["output_tokens"] is not None for u in usages)
                    else None,
                    "currencies": sorted({u["currency"] for u in usages if u["currency"]}),
                    "amounts": {
                        currency: {
                            "reported_amount": str(
                                sum(
                                    (
                                        u["amount"]
                                        for u in usages
                                        if u["currency"] == currency and u["amount"] is not None
                                    ),
                                    Decimal(0),
                                )
                            )
                            if any(
                                u["currency"] == currency and u["amount"] is not None
                                for u in usages
                            )
                            else None,
                            "complete": all(
                                u["amount"] is not None and u["state"] == "SETTLED"
                                for u in usages
                                if u["currency"] == currency
                            ),
                        }
                        for currency in sorted({u["currency"] for u in usages if u["currency"]})
                    },
                },
            )
            identifiers = artifact_references(result.data) if result else set()
            links = await Repository(core_metadata.tables["source_links"], context.scope).find(
                uow.connection, source_type="run", source_id=run_id, derived_type="artifact"
            )
            identifiers.update(link["derived_id"] for link in links)
        artifacts = []
        for identifier in sorted(identifiers):
            try:
                await self.authorization.require(context, "artifact:download", identifier)
            except ServiceError as exc:
                if exc.status in {403, 404}:
                    continue
                raise
            async with transaction(self.engine, context.scope, self.keys(context, run_id)) as uow:
                await self.guard(uow, await self.locked_run(uow, run_id))
                await DeletionGuard(context.scope).check(uow, [ContentRef("artifact", identifier)])
                artifact = await Repository(core_metadata.tables["artifacts"], context.scope).get(
                    uow.connection, identifier
                )
                if (
                    not artifact
                    or artifact["state"] != "AVAILABLE"
                    or artifact["expires_at"] <= utcnow()
                ):
                    continue
                prefix = "admin" if context.principal_type == "management" else "api"
                artifacts.append(
                    Artifact(
                        artifact_id=identifier,
                        scope=context.scope,
                        name=artifact["name"],
                        content_type=artifact["content_type"],
                        size_bytes=artifact["size_bytes"],
                        sha256=artifact["sha256"],
                        state=artifact["state"],
                        expires_at=artifact["expires_at"],
                        download_path=f"/{prefix}/v1/artifacts/{identifier}/content",
                    )
                )
        return envelope.model_copy(update={"artifacts": tuple(artifacts)})

    async def detail(self, context: AuthContext, run_id: str) -> dict[str, Any]:
        context = await self.access_context(context, run_id)
        await self.authorization.require(context, "run:read", run_id)
        content_allowed = True
        try:
            await self.authorization.require(context, "run:content", run_id)
        except ServiceError as exc:
            if exc.status not in {403, 404}:
                raise
            content_allowed = False
        actions = []
        try:
            await self.authorization.require(context, "run:create", run_id)
            actions = [
                {"action_key": "cancel", "label": "取消"},
                {"action_key": "rerun", "label": "重新执行"},
            ]
        except ServiceError as exc:
            if exc.status not in {403, 404}:
                raise
        try:
            await self.authorization.require(context, "content:delete", "scope")
            actions.append({"action_key": "delete", "label": "删除运行内容"})
        except ServiceError as exc:
            if exc.status not in {403, 404}:
                raise
        async with transaction(self.engine, context.scope, self.keys(context, run_id)) as uow:
            row = await self.locked_run(uow, run_id)
            self.owner(context, row)
            input_value = None
            snapshot = None
            actual_inputs = []
            evidence = []
            if content_allowed:
                snapshot = await self.snapshot(uow, row)
                input_value = (
                    await required(
                        uow.connection,
                        "run_contents",
                        context.scope.channel_id,
                        id=row["input_ref"],
                        run_id=run_id,
                    )
                )["payload"]
                actual_inputs = [
                    {
                        "name": next(
                            (
                                s.name
                                for s in ExecutionPolicy.model_validate(
                                    row["execution_policy"]
                                ).steps
                                if s.node_key
                                == c["kind"].removeprefix("inputs:").split(".attempt")[0]
                            ),
                            None,
                        )
                        or "结果来源",
                        "value": c["payload"],
                    }
                    for c in await rows(
                        uow.connection, "run_contents", context.scope.channel_id, run_id=run_id
                    )
                    if c["kind"].startswith("inputs:")
                ]
                calls = await Repository(tool_metadata.tables["tool_calls"], context.scope).find(
                    uow.connection, run_id=run_id
                )
                for call in calls:
                    for identifier in call["evidence_ids"]:
                        await DeletionGuard(context.scope).check(
                            uow, [ContentRef("evidence", identifier)]
                        )
                        ref = await Repository(
                            tool_metadata.tables["evidence_refs"], context.scope
                        ).get(uow.connection, identifier)
                        if ref:
                            evidence.append(
                                {
                                    "title": ref["title"],
                                    "source_version": ref["source_version"],
                                    "observed_at": ref["observed_at"].isoformat(),
                                    "location": ref["location"],
                                }
                            )
            return_value = {
                **self.receipt(row).model_dump(mode="json"),
                "name": row["agent_name"],
                "purpose": row["purpose"],
                "purpose_label": {"production": "正式调用", "debug": "调试", "evaluation": "评测"}[
                    row["purpose"]
                ],
                "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
                "conversation_id": row["conversation_id"],
                "parent_run_id": row["parent_run_id"],
                "content_allowed": content_allowed,
                "input": input_value,
                "actual_inputs": actual_inputs,
                "evidence": evidence,
                "error": row["error"],
                "versions": [
                    {"name": v.version_label, "type": v.resource_type, "version_id": v.version_id}
                    for v in snapshot.versions
                ]
                if snapshot
                else [],
                "actions": [
                    a
                    for a in actions
                    if (a["action_key"] == "cancel" and row["state"] not in TERMINAL)
                    or (a["action_key"] == "rerun" and row["state"] in TERMINAL and content_allowed)
                    or a["action_key"] == "delete"
                ],
            }
        return {
            **return_value,
            "result": (await self.get_run(context, run_id)).model_dump(mode="json")
            if content_allowed
            else None,
        }

    async def list_runs(
        self,
        context: AuthContext,
        *,
        after_id: str | None = None,
        limit: int = 50,
        state: str | None = None,
        agent_id: str | None = None,
        key_id: str | None = None,
        purpose: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 200:
            raise ServiceError("PAGE_INVALID", "分页数量超出范围", 422)
        await self.authorization.require(context, "run:read", "scope")
        filters = context.scope.model_dump(exclude={"channel_id"})
        if context.principal_type == "management" and context.scope.subject_id is None:
            filters.pop("subject_type", None)
            filters.pop("subject_id", None)
        if bool(subject_type) != bool(subject_id):
            raise ServiceError("FILTER_INVALID", "主体类型与编号须同时提供", 422)
        if any(value is not None and value.tzinfo is None for value in (start_at, end_at)):
            raise ServiceError("FILTER_INVALID", "查询时间必须包含时区", 422)
        if start_at and end_at and start_at >= end_at:
            raise ServiceError("FILTER_INVALID", "结束时间须晚于开始时间", 422)
        async with self.engine.connect() as connection:
            found = await rows(
                connection,
                "runs",
                context.scope.channel_id,
                **filters,
            )
        selected = sorted(
            (
                r
                for r in found
                if (not state or r["state"] == state)
                and (not agent_id or r["agent_id"] == agent_id)
                and (not key_id or r["key_id"] == key_id)
                and (not purpose or r["purpose"] == purpose)
                and (not start_at or r["created_at"] >= start_at)
                and (not end_at or r["created_at"] < end_at)
                and (
                    not subject_id
                    or (r["subject_type"], r["subject_id"]) == (subject_type, subject_id)
                )
                and (not error_code or (r["error"] or {}).get("code") == error_code)
                and (not context.client_id or r["client_id"] == context.client_id)
            ),
            key=lambda r: (r["created_at"], r["id"]),
        )
        if after_id:
            cursor = next((r for r in selected if r["id"] == after_id), None)
            if cursor is None:
                raise ServiceError("CURSOR_INVALID", "分页位置已失效", 422)
            selected = [
                r
                for r in selected
                if (r["created_at"], r["id"]) > (cursor["created_at"], cursor["id"])
            ]
        visible = []
        for row in selected:
            try:
                effective = await self.access_context(context, row["id"])
                await self.authorization.require(effective, "run:read", row["id"])
            except ServiceError as exc:
                if exc.status in {403, 404}:
                    continue
                raise
            visible.append(row)
            if len(visible) > limit:
                break
        return {
            "items": [
                RunSummary(
                    **self.receipt(r).model_dump(),
                    name=r["agent_name"],
                    parent_run_id=r["parent_run_id"],
                    release_snapshot_id=r["release_snapshot_id"],
                    error=r["error"],
                ).model_dump(mode="json")
                for r in visible[:limit]
            ],
            "next_cursor": visible[limit - 1]["id"] if len(visible) > limit else None,
        }

    async def events(
        self, context: AuthContext, run_id: str, *, after_sequence: int = 0, limit: int = 100
    ) -> list[RunEvent]:
        if after_sequence < 0 or not 1 <= limit <= 200:
            raise ServiceError("CURSOR_INVALID", "事件游标或分页数量无效", 422)
        context = await self.access_context(context, run_id)
        await self.authorization.require(context, "run:content", run_id)
        async with transaction(self.engine, context.scope, self.keys(context, run_id)) as uow:
            row = await self.locked_run(uow, run_id)
            self.owner(context, row)
            await self.guard(uow, row)
            if after_sequence > row["event_sequence"]:
                raise ServiceError("CURSOR_INVALID", "事件游标超出当前进度", 422)
            events = sorted(
                await rows(uow.connection, "run_events", context.scope.channel_id, run_id=run_id),
                key=lambda e: e["sequence"],
            )
            if any(e["sequence"] > after_sequence and e["expires_at"] <= utcnow() for e in events):
                raise ServiceError("EVENTS_EXPIRED", "事件已过期，请查询运行结果", 410)
            remaining = [e for e in events if e["sequence"] > after_sequence]
            if after_sequence < row["event_sequence"] and (
                not remaining or remaining[0]["sequence"] != after_sequence + 1
            ):
                raise ServiceError("EVENTS_EXPIRED", "事件已过期，请查询运行结果", 410)
            result = []
            for event in remaining[:limit]:
                payload = await required(
                    uow.connection,
                    "run_contents",
                    context.scope.channel_id,
                    id=event["payload_ref"],
                    run_id=run_id,
                )
                result.append(
                    RunEvent(
                        scope=context.scope,
                        event_id=event["id"],
                        run_id=run_id,
                        sequence=event["sequence"],
                        event_type=event["event_type"],
                        payload=payload["payload"],
                        occurred_at=event["created_at"],
                        expires_at=event["expires_at"],
                    )
                )
            return result

    async def trace(
        self, context: AuthContext, run_id: str, *, after_sequence: int = 0, limit: int = 50
    ) -> TracePage:
        if after_sequence < 0 or not 1 <= limit <= 200:
            raise ServiceError("CURSOR_INVALID", "轨迹游标或分页数量无效", 422)
        context = await self.access_context(context, run_id)
        await self.authorization.require(context, "run:read", run_id)
        async with transaction(self.engine, context.scope, self.keys(context, run_id)) as uow:
            row = await self.locked_run(uow, run_id)
            self.owner(context, row)
            steps = sorted(
                (
                    s
                    for s in await rows(
                        uow.connection, "run_steps", context.scope.channel_id, run_id=run_id
                    )
                    if s["sequence"] > after_sequence
                ),
                key=lambda s: s["sequence"],
            )
            items = []
            for step in steps[:limit]:
                attempts = await rows(
                    uow.connection,
                    "attempts",
                    context.scope.channel_id,
                    run_id=run_id,
                    step_id=step["id"],
                )
                items.append(
                    {
                        "step_id": step["id"],
                        "node_key": step["node_key"],
                        "name": next(
                            (
                                p.name
                                for p in ExecutionPolicy.model_validate(
                                    row["execution_policy"]
                                ).steps
                                if p.node_key == step["node_key"]
                            ),
                            None,
                        )
                        or "执行步骤",
                        "sequence": step["sequence"],
                        "state": step["state"],
                        "state_label": "结果待核实"
                        if step["state"] == "UNKNOWN"
                        else LABELS.get(step["state"]),
                        "attempts": [
                            {
                                "attempt_id": a["id"],
                                "kind": a["kind"],
                                "state": a["state"],
                                "started_at": a["started_at"].isoformat(),
                                "finished_at": a["finished_at"].isoformat()
                                if a["finished_at"]
                                else None,
                                "usage_ref": a["usage_id"],
                                "error": a["error"],
                                "kind_label": "模型调用" if a["kind"] == "model" else "工具调用",
                                "state_label": {"STARTED": "调用中", "UNKNOWN": "结果待核实"}.get(
                                    a["state"], LABELS.get(a["state"], "状态不可用")
                                ),
                            }
                            for a in attempts
                        ],
                    }
                )
            return TracePage(
                items=items,
                next_sequence=steps[limit - 1]["sequence"] if len(steps) > limit else None,
            )
