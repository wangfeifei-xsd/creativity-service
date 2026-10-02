"""运行快照与有界轨迹分页；内容和诊断权限分别复核。"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import BusinessResult, ResultEnvelope, RunEvent
from creativity_service.core.database import transaction
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.runs.base import RunKernel
from creativity_service.modules.runs.repositories import required, rows, verify_scope
from creativity_service.modules.runs.schemas import LABELS, RunSummary, TracePage
from creativity_service.modules.usage import repositories as usage_repo


class QueryService(RunKernel):
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
            return ResultEnvelope(
                channel_id=context.scope.channel_id,
                run_id=run_id,
                state=row["state"],
                state_label=LABELS[row["state"]],
                release_snapshot_id=row["release_snapshot_id"],
                result=result,
                partial_output=None,
                error=row["error"],
                artifacts=(),
                usage_summary={
                    "attempt_count": len(usages),
                    "pending_count": sum(u["state"] == "PENDING" for u in usages),
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
                    name=f"{r['agent_name']} {r['created_at'].isoformat()}",
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
                        "sequence": step["sequence"],
                        "state": step["state"],
                        "state_label": "结果待核实"
                        if step["state"] == "UNKNOWN"
                        else LABELS.get(step["state"]),
                        "attempts": [
                            {
                                "attempt_id": a["id"],
                                "state": a["state"],
                                "started_at": a["started_at"].isoformat(),
                                "finished_at": a["finished_at"].isoformat()
                                if a["finished_at"]
                                else None,
                                "usage_ref": a["usage_id"],
                            }
                            for a in attempts
                        ],
                    }
                )
            return TracePage(
                items=items,
                next_sequence=steps[limit - 1]["sequence"] if len(steps) > limit else None,
            )
