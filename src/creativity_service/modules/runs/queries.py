"""运行快照与有界轨迹分页；内容和诊断权限分别复核。"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, tuple_

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import Artifact, BusinessResult, ResultEnvelope, RunEvent
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.iam.authorization import ReadAuthorization
from creativity_service.modules.iam.reading import (
    read_actions,
    read_policy,
    require_action,
    resource_state,
)
from creativity_service.modules.runs.base import RunKernel
from creativity_service.modules.runs.repositories import required, rows, verify_scope
from creativity_service.modules.runs.schemas import (
    LABELS,
    TERMINAL,
    ExecutionPolicy,
    RunSummary,
    TracePage,
)
from creativity_service.modules.runs.tables import metadata
from creativity_service.modules.tools.tables import metadata as tool_metadata
from creativity_service.modules.tools.validation import artifact_references
from creativity_service.modules.usage import repositories as usage_repo


class QueryService(RunKernel):
    async def filter_options(self, context: AuthContext) -> dict[str, Any]:
        from creativity_service.modules.channels.tables import metadata as channels

        policy = await read_policy(self.authorization, context)
        allowed = await read_actions(
            self.authorization, context, "run", "scope", ["run:read"], policy=policy
        )
        require_action(allowed, "run:read")
        table = metadata.tables["runs"]
        predicates = self.read_predicates(context, policy)
        async with self.engine.connect() as connection:
            # 选项只读取去重后的显示字段，不加载运行正文和整份执行策略。
            visible = [
                dict(r)
                for r in (
                    await connection.execute(
                        active_rows(
                            select(
                                table.c.agent_id,
                                table.c.agent_name,
                                table.c.key_id,
                                table.c.error["code"].as_string().label("error_code"),
                                table.c.error["message"].as_string().label("error_message"),
                            )
                            .where(*predicates)
                            .distinct()
                        )
                    )
                ).mappings()
            ]
            key_rows = await Repository(channels.tables["channel_keys"], context.scope).get_many(
                connection, [r["key_id"] for r in visible if r["key_id"]]
            )
            keys = [
                {"value": identifier, "label": key["name"]}
                for identifier, key in sorted(key_rows.items())
            ]
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
                        r["error_code"]: r["error_message"] for r in visible if r["error_code"]
                    }.items()
                )
            ],
        }

    @staticmethod
    def owner(context: AuthContext, row: dict[str, Any]) -> None:
        verify_scope(row, context.scope)
        if context.client_id and context.client_id != row["client_id"]:
            raise ServiceError("NOT_FOUND", "运行记录不存在", 404)

    @staticmethod
    def read_predicates(context: AuthContext, policy: ReadAuthorization | None) -> list[Any]:
        table = metadata.tables["runs"]
        scope = context.scope.model_dump()
        if context.principal_type == "management" and context.scope.subject_id is None:
            scope.pop("subject_type")
            scope.pop("subject_id")
        predicates = [table.c[k] == v for k, v in scope.items()]
        if context.client_id:
            predicates.append(table.c.client_id == context.client_id)
        if policy is not None:
            identifiers = policy.resource_ids("run", "run:read")
            if identifiers is not None:
                predicates.append(table.c.id.in_(identifiers))
        return predicates

    async def read_access(
        self, context: AuthContext, run_id: str
    ) -> tuple[AuthContext, ReadAuthorization | None, frozenset[str]]:
        context = await self.access_context(context, run_id)
        policy = await read_policy(self.authorization, context)
        permissions = await read_actions(
            self.authorization,
            context,
            "run",
            run_id,
            ["run:read", "run:content", "run:create"],
            policy=policy,
            state=resource_state(context, "run", {"id": run_id}),
        )
        require_action(permissions, "run:read")
        return context, policy, permissions

    async def get_run(self, context: AuthContext, run_id: str) -> ResultEnvelope:
        context, policy, permissions = await self.read_access(context, run_id)
        require_action(permissions, "run:content")
        return await self.read_result(context, run_id, policy)

    async def read_result(
        self, context: AuthContext, run_id: str, policy: ReadAuthorization | None
    ) -> ResultEnvelope:
        """调用方已经验证运行内容权限，复用读取和响应装配原子能力。"""
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
            artifact_rows = await Repository(
                core_metadata.tables["artifacts"], context.scope
            ).get_many(uow.connection, identifiers)
            blocked = await DeletionGuard(context.scope).blocked_refs(
                uow, [ContentRef("artifact", i) for i in artifact_rows]
            )
        artifacts = []
        for identifier, artifact in sorted(artifact_rows.items()):
            if artifact["state"] != "AVAILABLE" or artifact["expires_at"] <= utcnow():
                continue
            permissions = await read_actions(
                self.authorization,
                context,
                "artifact",
                identifier,
                ["artifact:download"],
                policy=policy,
                state=resource_state(context, "artifact", artifact),
            )
            if "artifact:download" not in permissions:
                continue
            if ContentRef("artifact", identifier) in blocked:
                raise ServiceError("CONTENT_DELETED", "内容或来源已删除", 410)
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
        context, policy, permissions = await self.read_access(context, run_id)
        content_allowed = "run:content" in permissions
        actions = []
        if "run:create" in permissions:
            actions = [
                {"action_key": "cancel", "label": "取消"},
                {"action_key": "rerun", "label": "重新执行"},
            ]
        scope_actions = await read_actions(
            self.authorization, context, "content", "scope", ["content:delete"], policy=policy
        )
        if "content:delete" in scope_actions:
            actions.append({"action_key": "delete", "label": "删除运行内容"})
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
                identifiers = [identifier for call in calls for identifier in call["evidence_ids"]]
                await DeletionGuard(context.scope).check(
                    uow, [ContentRef("evidence", i) for i in identifiers]
                )
                refs = await Repository(
                    tool_metadata.tables["evidence_refs"], context.scope
                ).get_many(uow.connection, identifiers)
                evidence = [
                    {
                        "title": ref["title"],
                        "source_version": ref["source_version"],
                        "observed_at": ref["observed_at"].isoformat(),
                        "location": ref["location"],
                    }
                    for identifier in identifiers
                    if (ref := refs.get(identifier))
                ]
            from creativity_service.modules.agents.repositories import repository as resource_repo
            from creativity_service.modules.resources.configuration import LABELS, TABLES

            uses = await resource_repo("resource_uses", context.scope).find(
                uow.connection, run_id=run_id
            )
            parents = {
                kind: await resource_repo(TABLES[kind], context.scope).get_many(
                    uow.connection, [r["resource_id"] for r in uses if r["resource_type"] == kind]
                )
                for kind in {r["resource_type"] for r in uses}
            }
            resource_uses = [
                {
                    "name": r["resource_name"],
                    "type": LABELS[r["resource_type"]],
                    "deleted": parents.get(r["resource_type"], {})
                    .get(r["resource_id"], {})
                    .get("status")
                    in {None, "DELETED"},
                    "used_at": r["created_at"].isoformat(),
                }
                for r in uses
            ]
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
                "resource_uses": resource_uses,
                "versions": [
                    {
                        "name": v.resource_name
                        or (
                            v.content.get("name")
                            if v.resource_type in {"model", "model_connection"}
                            else None
                        )
                        or v.version_label,
                        "type": v.resource_type,
                        "version_id": v.version_id,
                    }
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
            "result": (await self.read_result(context, run_id, policy)).model_dump(mode="json")
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
        policy = await read_policy(self.authorization, context)
        allowed = await read_actions(
            self.authorization, context, "run", "scope", ["run:read"], policy=policy
        )
        require_action(allowed, "run:read")
        if bool(subject_type) != bool(subject_id):
            raise ServiceError("FILTER_INVALID", "主体类型与编号须同时提供", 422)
        if any(value is not None and value.tzinfo is None for value in (start_at, end_at)):
            raise ServiceError("FILTER_INVALID", "查询时间必须包含时区", 422)
        if start_at and end_at and start_at >= end_at:
            raise ServiceError("FILTER_INVALID", "结束时间须晚于开始时间", 422)
        table = metadata.tables["runs"]
        predicates = self.read_predicates(context, policy)
        for field, value in (
            ("state", state),
            ("agent_id", agent_id),
            ("key_id", key_id),
            ("purpose", purpose),
            ("subject_type", subject_type),
            ("subject_id", subject_id),
        ):
            if value:
                predicates.append(table.c[field] == value)
        if start_at:
            predicates.append(table.c.created_at >= start_at)
        if end_at:
            predicates.append(table.c.created_at < end_at)
        if error_code:
            predicates.append(table.c.error["code"].as_string() == error_code)
        async with self.engine.connect() as connection:
            if after_id:
                cursors = (
                    await connection.execute(
                        active_rows(
                            select(table.c.created_at, table.c.id).where(
                                *predicates, table.c.id == after_id
                            )
                        )
                    )
                ).all()
                if len(cursors) != 1:
                    raise ServiceError("CURSOR_INVALID", "分页位置已失效", 422)
                predicates.append(tuple_(table.c.created_at, table.c.id) > tuple(cursors[0]))
            visible: list[dict[str, Any]] = []
            while len(visible) <= limit:
                batch = [
                    dict(r)
                    for r in (
                        await connection.execute(
                            active_rows(
                                select(table)
                                .where(*predicates)
                                .order_by(table.c.created_at, table.c.id)
                                .limit(limit + 1)
                            )
                        )
                    ).mappings()
                ]
                if not batch:
                    break
                for row in batch:
                    effective = self.row_context(context, row)
                    permissions = await read_actions(
                        self.authorization,
                        effective,
                        "run",
                        row["id"],
                        ["run:read"],
                        policy=policy,
                        state=resource_state(effective, "run", row),
                    )
                    if "run:read" in permissions:
                        visible.append(row)
                    if len(visible) > limit:
                        break
                if len(batch) < limit + 1:
                    break
                predicates.append(
                    tuple_(table.c.created_at, table.c.id)
                    > (batch[-1]["created_at"], batch[-1]["id"])
                )
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
            table = metadata.tables["run_events"]
            predicates = [
                Repository(table, context.scope).predicate(),
                table.c.run_id == run_id,
                table.c.sequence > after_sequence,
            ]
            if await uow.connection.scalar(
                active_rows(
                    select(table.c.id).where(*predicates, table.c.expires_at <= utcnow()).limit(1)
                )
            ):
                raise ServiceError("EVENTS_EXPIRED", "事件已过期，请查询运行结果", 410)
            remaining = [
                dict(r)
                for r in (
                    await uow.connection.execute(
                        active_rows(
                            select(table).where(*predicates).order_by(table.c.sequence).limit(limit)
                        )
                    )
                ).mappings()
            ]
            if after_sequence < row["event_sequence"] and (
                not remaining or remaining[0]["sequence"] != after_sequence + 1
            ):
                raise ServiceError("EVENTS_EXPIRED", "事件已过期，请查询运行结果", 410)
            result = []
            contents = await Repository(metadata.tables["run_contents"], context.scope).get_many(
                uow.connection, [e["payload_ref"] for e in remaining]
            )
            for event in remaining:
                payload = contents.get(event["payload_ref"])
                if payload is None or payload["run_id"] != run_id:
                    raise ServiceError("NOT_FOUND", "事件内容不存在", 404)
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
            table = metadata.tables["run_steps"]
            steps = [
                dict(r)
                for r in (
                    await uow.connection.execute(
                        active_rows(
                            select(table)
                            .where(
                                Repository(table, context.scope).predicate(),
                                table.c.run_id == run_id,
                                table.c.sequence > after_sequence,
                            )
                            .order_by(table.c.sequence)
                            .limit(limit + 1)
                        )
                    )
                ).mappings()
            ]
            attempt_rows = await Repository(metadata.tables["attempts"], context.scope).find_many(
                uow.connection, "step_id", [s["id"] for s in steps[:limit]], run_id=run_id
            )
            grouped: dict[str, list[dict[str, Any]]] = {}
            for attempt in attempt_rows:
                grouped.setdefault(attempt["step_id"], []).append(attempt)
            items = []
            for step in steps[:limit]:
                attempts = grouped.get(step["id"], [])
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
                                "kind_label": {
                                    "model": "模型调用",
                                    "tool": "工具调用",
                                    "compute": "固定计算",
                                }.get(a["kind"], "执行类型不可用"),
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
