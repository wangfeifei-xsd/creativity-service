"""持久化异步导出，逐次复核当前权限，文件只经受控接口交付。"""

import csv
import io
from datetime import datetime, timedelta
from typing import Any

from creativity_service.core.artifacts import MAX_FILE_BYTES, ObjectStore
from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import transaction
from creativity_service.core.deletion import CleanupRegistry, ContentRef, DeletionGuard, content_key
from creativity_service.core.primitives import ServiceError, new_id, unavailable, utcnow
from creativity_service.core.security.keys import object_path
from creativity_service.modules.usage.management import UsageManagement
from creativity_service.modules.usage.query import totals, view
from creativity_service.modules.usage.repositories import ledger_key, required, rows, save
from creativity_service.modules.usage.schemas import CurrencyTotal, ExportView, UsageFilter

EXPORT_LABELS = {
    "QUEUED": "等待导出",
    "RUNNING": "正在导出",
    "SUCCEEDED": "可下载",
    "FAILED": "导出失败",
    "DELETED": "已删除",
}


def csv_cell(value: Any) -> str:
    result = "" if value is None else str(value)
    return "'" + result if result.lstrip().startswith(("=", "+", "-", "@")) else result


class UsageExports:
    def __init__(self, management: UsageManagement, store: ObjectStore | None = None) -> None:
        self.management, self.engine, self.store = management, management.engine, store

    @staticmethod
    def view(row: dict[str, Any]) -> ExportView:
        prefix = "platform/" if row["channel_id"] == "system" else ""
        return ExportView(
            id=row["id"],
            state=row["state"],
            state_label=EXPORT_LABELS[row["state"]],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            download_path=f"/admin/v1/{prefix}usage/exports/{row['id']}/content"
            if row["state"] == "SUCCEEDED"
            else None,
            metadata={k: v for k, v in row["metadata"].items() if k not in {"lease", "worker"}},
            error_message=row["error_message"],
        )

    async def create(self, session: AdminSession, query: UsageFilter) -> ExportView:
        context = await self.management.context(session, "data:export")
        scopes = await self.management.scopes(session)
        export_scopes = await self.management.scopes(session, "data:export")
        scopes = [s for s in scopes if s in export_scopes]
        if not scopes:
            raise ServiceError("FORBIDDEN", "没有允许导出的用量范围", 403)
        from creativity_service.modules.usage.query import validate_filter

        validate_filter(query)
        export_id = new_id("usage_export")
        async with transaction(
            self.engine,
            context.scope,
            [ledger_key(context.scope.channel_id), content_key(context.scope)],
        ) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("usage_export", export_id)])
            row = await save(
                uow,
                "usage_exports",
                export_id,
                {
                    **context.scope.model_dump(exclude={"channel_id"}),
                    "requested_by": session.account.id,
                    "filters": query.model_dump(mode="json"),
                    "timezone": query.timezone,
                    "channel_range": [context.scope.channel_id],
                    "state": "QUEUED",
                    "artifact_id": None,
                    "scope_snapshot": {"scopes": [s.model_dump(mode="json") for s in scopes]},
                    "object_key": None,
                    "metadata": {
                        "format": "CSV",
                        "basis": "实际尝试及最新核算版本",
                        "requested_by": session.account.display_name,
                    },
                    "error_message": None,
                    "expires_at": utcnow() + timedelta(days=7),
                },
            )
        return self.view(row)

    async def list_exports(self, session: AdminSession) -> list[ExportView]:
        context = await self.management.context(session, "data:export")
        async with self.engine.connect() as connection:
            result = await rows(
                connection,
                "usage_exports",
                context.scope.channel_id,
                requested_by=session.account.id,
            )
        return [self.view(r) for r in sorted(result, key=lambda r: r["created_at"], reverse=True)]

    async def authorize_job(self, row: dict[str, Any]) -> list[Scope]:
        scopes = [Scope.model_validate(s) for s in row["scope_snapshot"]["scopes"]]
        if (
            not scopes
            or row["channel_range"] != [row["channel_id"]]
            or any(s.channel_id != row["channel_id"] for s in scopes)
        ):
            raise ServiceError("SCOPE_MISMATCH", "导出任务范围无效", 403)
        authorization = self.management.channels.iam.authorization
        for scope in scopes:
            context = AuthContext(
                scope=scope,
                principal_type="worker",
                principal_id=row["requested_by"],
                actor_id=row["requested_by"],
                request_id=row["id"],
            )
            for action in ("usage:read", "data:export"):
                await authorization.boundary(context, action, "channel", scope.channel_id)
            async with transaction(self.engine, scope, [content_key(scope)]) as uow:
                await DeletionGuard(scope).check(uow, [ContentRef("usage_export", row["id"])])
        return scopes

    async def process(self, channel_id: str, export_id: str) -> None:
        if channel_id == "system":
            await self.process_platform(export_id)
            return
        if self.store is None:
            raise unavailable("用量导出文件服务")
        async with self.engine.connect() as connection:
            initial = await required(connection, "usage_exports", channel_id, id=export_id)
        scope = Scope.model_validate({k: initial[k] for k in Scope.model_fields})
        worker = new_id("export_worker")
        async with transaction(
            self.engine, scope, [ledger_key(channel_id), content_key(scope)]
        ) as uow:
            row = await required(uow.connection, "usage_exports", channel_id, id=export_id)
            if row["state"] not in {"QUEUED", "RUNNING"} or row["expires_at"] <= utcnow():
                return
            lease = row["metadata"].get("lease")
            if row["state"] == "RUNNING" and lease and datetime.fromisoformat(lease) > utcnow():
                return
            await DeletionGuard(scope).check(uow, [ContentRef("usage_export", export_id)])
            row = await save(
                uow,
                "usage_exports",
                export_id,
                {
                    "state": "RUNNING",
                    "metadata": {
                        **row["metadata"],
                        "worker": worker,
                        "lease": (utcnow() + timedelta(minutes=5)).isoformat(),
                    },
                },
            )
        key = object_path(scope, worker)
        try:
            scopes = await self.authorize_job(row)
            query = UsageFilter.model_validate(row["filters"])
            async with self.engine.connect() as connection:
                async with connection.begin():
                    await connection.exec_driver_sql(
                        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                    )
                    records = await self.management.queries.select(
                        connection, channel_id, scopes, query
                    )
                    admissions = await self.management.queries.select(
                        connection, channel_id, scopes, query, "admissions"
                    )
            summary = totals(records, admissions, query.timezone)
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(
                [
                    "时间",
                    "模型",
                    "智能体",
                    "用途",
                    "输入 Token",
                    "输出 Token",
                    "用量状态",
                    "计价状态",
                    "金额",
                    "币种",
                    "结算状态",
                ]
            )
            from creativity_service.modules.usage.pricing import timezone

            for record in records:
                value = view(record)
                writer.writerow(
                    [
                        csv_cell(v)
                        for v in (
                            record["created_at"]
                            .astimezone(timezone(query.timezone))
                            .strftime("%Y-%m-%d %H:%M:%S %z"),
                            value.names.get("model"),
                            value.names.get("agent"),
                            value.purpose_label,
                            value.input_tokens,
                            value.output_tokens,
                            value.usage_label,
                            value.pricing_label,
                            value.amount,
                            value.currency,
                            value.state_label,
                        )
                    ]
                )
            data = b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")
            if len(data) > MAX_FILE_BYTES:
                raise ServiceError("EXPORT_TOO_LARGE", "导出超过 20 MiB，请缩小时间或筛选范围", 422)
            await self.store.put(key, data, "text/csv")
            await self.authorize_job(row)
            async with transaction(
                self.engine, scope, [ledger_key(channel_id), content_key(scope)]
            ) as uow:
                current = await required(uow.connection, "usage_exports", channel_id, id=export_id)
                await DeletionGuard(scope).check(uow, [ContentRef("usage_export", export_id)])
                if current["state"] != "RUNNING" or current["metadata"].get("worker") != worker:
                    raise ServiceError("EXPORT_REPLACED", "导出任务已由新执行接管", 409)
                await save(
                    uow,
                    "usage_exports",
                    export_id,
                    {
                        "state": "SUCCEEDED",
                        "object_key": key,
                        "metadata": {
                            **self.view(row).metadata,
                            "filters": query.model_dump(mode="json"),
                            "timezone": query.timezone,
                            "price_complete": summary.price_complete,
                            "unpriced": summary.unpriced,
                            "aggregate_updated_at": summary.aggregate_updated_at.isoformat(),
                            "size_bytes": len(data),
                            "records": len(records),
                        },
                    },
                )
        except Exception as exc:
            await self.store.delete(key)
            async with transaction(self.engine, scope, [ledger_key(channel_id)]) as uow:
                current = await required(uow.connection, "usage_exports", channel_id, id=export_id)
                if current["state"] == "RUNNING" and current["metadata"].get("worker") == worker:
                    await save(
                        uow,
                        "usage_exports",
                        export_id,
                        {
                            "state": "FAILED",
                            "error_message": exc.message
                            if isinstance(exc, ServiceError)
                            else "导出处理失败，请重试",
                        },
                    )
            if not isinstance(exc, ServiceError):
                raise

    async def download(self, session: AdminSession, export_id: str) -> bytes:
        if self.store is None:
            raise unavailable("用量导出文件服务")
        context = await self.management.context(session, "data:export")
        async with self.engine.connect() as connection:
            row = await required(
                connection, "usage_exports", context.scope.channel_id, id=export_id
            )
        if row["requested_by"] != session.account.id:
            raise ServiceError("NOT_FOUND", "请求导出不存在", 404)
        if row["expires_at"] <= utcnow() or row["state"] != "SUCCEEDED":
            raise ServiceError("EXPORT_UNAVAILABLE", "导出尚未完成或已失效", 409)
        await self.authorize_job(row)
        data = await self.store.get(row["object_key"], MAX_FILE_BYTES)
        await self.management.context(session, "data:export")
        await self.authorize_job(row)
        return data

    def register_cleanup(self, registry: CleanupRegistry) -> None:
        registry.register("usage_export", self.clean)

    async def clean(self, context: AuthContext, ref: ContentRef) -> None:
        await self.management.channels.iam.authorization.require(
            context, "content:cleanup", ref.resource_id
        )
        async with transaction(
            self.engine,
            context.scope,
            [ledger_key(context.scope.channel_id), content_key(context.scope)],
        ) as uow:
            try:
                await DeletionGuard(context.scope).check(uow, [ref])
            except ServiceError as exc:
                if exc.code != "CONTENT_DELETED":
                    raise
            else:
                raise ServiceError("DELETION_MARKER_REQUIRED", "清理前必须登记删除标记", 409)
            row = await required(
                uow.connection,
                "usage_exports",
                context.scope.channel_id,
                id=ref.resource_id,
                include_deleted=True,
            )
            await save(
                uow,
                "usage_exports",
                row["id"],
                {
                    "is_deleted": True,
                    "state": "DELETED",
                    "filters": {},
                    "metadata": {},
                    "scope_snapshot": {"scopes": []},
                },
                include_deleted=True,
            )
        if row["object_key"] and self.store:
            await self.store.delete(row["object_key"])

    async def create_platform(
        self, session: AdminSession, channel_ids: list[str], query: UsageFilter
    ) -> ExportView:
        from creativity_service.core.context import ControlScope
        from creativity_service.modules.iam.authorization import require_platform
        from creativity_service.modules.usage.query import validate_filter

        await self.management.channels.iam.authentication.revalidate_admin(session, governance=True)
        if isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "平台导出须使用平台管理会话", 403)
        require_platform(session.account, "usage:platform")
        validate_filter(query)
        if not channel_ids or "system" in channel_ids or len(set(channel_ids)) != len(channel_ids):
            raise ServiceError("VALIDATION_ERROR", "须明确选择不重复的业务渠道", 422)
        if any(
            value is not None
            for key, value in query.model_dump().items()
            if key not in {"start_at", "end_at", "timezone", "target_currency"}
        ):
            raise ServiceError("VALIDATION_ERROR", "平台导出仅支持渠道和时间汇总", 422)
        # 专用治理入口逐渠道验证范围，并留下真实渠道集合的查询审计。
        await self.management.channels.platform_usage(
            session, channel_ids, query.start_at.isoformat(), query.end_at.isoformat()
        )
        export_id = new_id("usage_export")
        scope = ControlScope(purpose="audit", actor_id=session.account.id)
        async with transaction(self.engine, scope, [ledger_key("system")]) as uow:
            row = await save(
                uow,
                "usage_exports",
                export_id,
                {
                    "environment": "control",
                    "subject_type": None,
                    "subject_id": None,
                    "requested_by": session.account.id,
                    "filters": query.model_dump(mode="json"),
                    "timezone": query.timezone,
                    "channel_range": sorted(channel_ids),
                    "state": "QUEUED",
                    "artifact_id": None,
                    "scope_snapshot": {"kind": "platform_summary"},
                    "object_key": None,
                    "metadata": {
                        "format": "CSV",
                        "basis": "获授权渠道汇总",
                        "requested_by": session.account.display_name,
                    },
                    "error_message": None,
                    "expires_at": utcnow() + timedelta(days=7),
                },
            )
        return self.view(row)

    async def authorize_platform_job(self, row: dict[str, Any]) -> None:
        from creativity_service.modules.iam.authorization import require_platform

        account = await self.management.channels.iam.authentication.active_account(
            row["requested_by"]
        )
        require_platform(account, "usage:platform")
        require_platform(account, "channel:govern")
        async with self.engine.connect() as connection:
            available = set(await self.management.channels.repository.directory(connection))
        covered = row["channel_range"]
        if not covered or len(set(covered)) != len(covered) or not set(covered) <= available:
            raise ServiceError("FORBIDDEN", "平台导出包含不可访问的渠道", 403)

    async def process_platform(self, export_id: str) -> None:
        from creativity_service.core.context import ControlScope
        from creativity_service.modules.channels.repositories import required as channel_required
        from creativity_service.modules.channels.repositories import rows as channel_rows

        if self.store is None:
            raise unavailable("用量导出文件服务")
        scope = ControlScope(purpose="audit", actor_id="usage_export_worker")
        worker = new_id("export_worker")
        async with transaction(self.engine, scope, [ledger_key("system")]) as uow:
            row = await required(uow.connection, "usage_exports", "system", id=export_id)
            lease = row["metadata"].get("lease")
            if (
                row["state"] not in {"QUEUED", "RUNNING"}
                or row["expires_at"] <= utcnow()
                or (
                    row["state"] == "RUNNING" and lease and datetime.fromisoformat(lease) > utcnow()
                )
            ):
                return
            row = await save(
                uow,
                "usage_exports",
                export_id,
                {
                    "state": "RUNNING",
                    "metadata": {
                        **row["metadata"],
                        "worker": worker,
                        "lease": (utcnow() + timedelta(minutes=5)).isoformat(),
                    },
                },
            )
        key = f"channels/system/usage_exports/{export_id}/{worker}"
        try:
            await self.authorize_platform_job(row)
            query = UsageFilter.model_validate(row["filters"])
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(
                [
                    "渠道",
                    "请求数",
                    "实际尝试数",
                    "币种",
                    "已计价费用",
                    "暂估费用",
                    "未定价数量",
                    "用量缺失数量",
                    "聚合更新时间",
                ]
            )
            complete = True
            for channel_id in row["channel_range"]:
                async with self.engine.connect() as connection:
                    channel = await channel_required(
                        connection, "channels", channel_id, id=channel_id
                    )
                    environments = await channel_rows(
                        connection, "channel_environments", channel_id
                    )
                scopes = [
                    Scope(channel_id=channel_id, environment=d["environment"]) for d in environments
                ]
                summary = await self.management.queries.summary(channel_id, scopes, query)
                complete = complete and summary.price_complete
                currency_rows: list[CurrencyTotal | None] = list(summary.costs) or [None]
                for cost in currency_rows:
                    writer.writerow(
                        [
                            csv_cell(v)
                            for v in (
                                channel["name"],
                                summary.requests,
                                summary.attempts,
                                cost.currency if cost else None,
                                cost.priced if cost else None,
                                cost.provisional if cost else None,
                                summary.unpriced,
                                summary.missing_usage,
                                summary.aggregate_updated_at.isoformat(),
                            )
                        ]
                    )
            data = b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")
            await self.store.put(key, data, "text/csv")
            await self.authorize_platform_job(row)
            async with transaction(self.engine, scope, [ledger_key("system")]) as uow:
                current = await required(uow.connection, "usage_exports", "system", id=export_id)
                if current["state"] != "RUNNING" or current["metadata"].get("worker") != worker:
                    raise ServiceError("EXPORT_REPLACED", "平台导出已由新执行接管", 409)
                await save(
                    uow,
                    "usage_exports",
                    export_id,
                    {
                        "state": "SUCCEEDED",
                        "object_key": key,
                        "metadata": {
                            **self.view(row).metadata,
                            "price_complete": complete,
                            "filters": row["filters"],
                            "channel_range": row["channel_range"],
                            "timezone": row["timezone"],
                            "size_bytes": len(data),
                        },
                    },
                )
        except Exception as exc:
            await self.store.delete(key)
            async with transaction(self.engine, scope, [ledger_key("system")]) as uow:
                current = await required(uow.connection, "usage_exports", "system", id=export_id)
                if current["state"] == "RUNNING" and current["metadata"].get("worker") == worker:
                    await save(
                        uow,
                        "usage_exports",
                        export_id,
                        {
                            "state": "FAILED",
                            "error_message": exc.message
                            if isinstance(exc, ServiceError)
                            else "导出处理失败，请重试",
                        },
                    )
            if not isinstance(exc, ServiceError):
                raise

    async def platform_list(self, session: AdminSession) -> list[ExportView]:
        from creativity_service.modules.iam.authorization import require_platform

        await self.management.channels.iam.authentication.revalidate_admin(session, governance=True)
        if isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "平台导出须使用平台管理会话", 403)
        require_platform(session.account, "usage:platform")
        async with self.engine.connect() as connection:
            records = await rows(
                connection, "usage_exports", "system", requested_by=session.account.id
            )
        return [self.view(r) for r in records]

    async def platform_download(self, session: AdminSession, export_id: str) -> bytes:
        if self.store is None:
            raise unavailable("用量导出文件服务")
        visible = await self.platform_list(session)
        if export_id not in {r.id for r in visible}:
            raise ServiceError("NOT_FOUND", "请求导出不存在", 404)
        async with self.engine.connect() as connection:
            row = await required(connection, "usage_exports", "system", id=export_id)
        if row["state"] != "SUCCEEDED" or row["expires_at"] <= utcnow():
            raise ServiceError("EXPORT_UNAVAILABLE", "导出尚未完成或已失效", 409)
        await self.authorize_platform_job(row)
        data = await self.store.get(row["object_key"], MAX_FILE_BYTES)
        await self.platform_list(session)
        await self.authorize_platform_job(row)
        return data
