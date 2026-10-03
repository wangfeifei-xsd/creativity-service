"""API 与 Worker 共用装配、资源授权读取和来源清理入口。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.auth.types import ResourceState, ResourceStateReader
from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.deletion import (
    CleanupHandler,
    CleanupRegistry,
    ContentRef,
)
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.services import AgentService
from creativity_service.modules.evaluations.repositories import keys, repository
from creativity_service.modules.evaluations.services import EvaluationService
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.releases.evaluation import StoredEvaluationGate
from creativity_service.modules.runs.services import RunService


class EvaluationResourceReader:
    def __init__(self, engine: AsyncEngine, fallback: ResourceStateReader | None) -> None:
        self.engine, self.fallback = engine, fallback

    async def read_current(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> ResourceState | None:
        if resource_type in {"evaluation", "content"}:
            async with self.engine.connect() as connection:
                tables = (
                    ("evaluation_datasets", "evaluations")
                    if resource_type == "evaluation"
                    else (
                        "evaluations",
                        "evaluation_cases",
                        "evaluation_fixtures",
                        "evaluation_results",
                        "evaluation_dataset_versions",
                    )
                )
                for table in tables:
                    row = await repository(table, context.scope).get(connection, resource_id)
                    if row:
                        return ResourceState(
                            scope=context.scope,
                            resource_type=resource_type,
                            resource_id=resource_id,
                            name=row.get("name", "评测内容"),
                            active=True,
                        )
            if resource_type == "evaluation":
                return None
        return (
            await self.fallback.read_current(context, resource_type, resource_id)
            if self.fallback
            else None
        )


def build_evaluation_service(
    engine: AsyncEngine,
    authorization: IamAuthorization,
    agents: AgentService,
    runs: RunService,
    cleanup: CleanupRegistry | None = None,
) -> EvaluationService:
    authorization.resources = EvaluationResourceReader(engine, authorization.resources)
    service = EvaluationService(engine, authorization, agents, runs)
    agents.evaluation = StoredEvaluationGate(service)

    async def guard(uow: UnitOfWork, run: dict[str, Any]) -> None:
        if run["purpose"] != "evaluation":
            return
        context = runs.context(run)
        entries = await repository("evaluation_results", context.scope).find(
            uow.connection, run_id=run["id"]
        )
        for entry in entries:
            case = await repository("evaluation_cases", context.scope).get(
                uow.connection, entry["case_id"]
            )
            if not case or not await service.valid_case(uow, context, case):
                raise ServiceError("CONTENT_DELETED", "评测来源已删除", 410)

    # 来源权限检查与授权撤销共用锁；运行内容事务提前声明所需资源。
    runs.content_guard_keys = lambda context: [
        policy_key(context.scope.channel_id),
        policy_key("system"),
    ]
    runs.content_guard = guard
    if cleanup:
        for kind, table in {
            "evaluation_case": "evaluation_cases",
            "evaluation_fixture": "evaluation_fixtures",
            "evaluation_result": "evaluation_results",
            "evaluation": "evaluations",
            "evaluation_dataset_version": "evaluation_dataset_versions",
        }.items():

            def handler(table_name: str) -> CleanupHandler:
                async def clear(context: AuthContext, ref: ContentRef) -> None:
                    await service.require(context, "content:cleanup")
                    async with transaction(
                        engine,
                        context.scope,
                        keys(
                            context,
                            [
                                (table_name, ref.resource_id),
                                ("evaluation_reports", ref.resource_id),
                            ],
                        ),
                    ) as uow:
                        await locked_require(uow, context, "content:cleanup", "evaluation", "scope")
                        from creativity_service.modules.evaluations.deletion import require_deleted

                        await require_deleted(service, uow, context, ref, table_name)
                        repo = repository(table_name, context.scope)
                        row = await repo.get(uow.connection, ref.resource_id)
                        if not row:
                            return
                        if table_name in {"evaluation_cases", "evaluation_fixtures"}:
                            values: dict[str, Any] = {"payload": None, "invalidated": True}
                            if table_name == "evaluation_cases":
                                values["title"] = "来源已删除的样本"
                                values["case_key"] = row["id"]
                        elif table_name == "evaluation_results":
                            values = {"judgment": None, "human_label": None, "state": "INVALID"}
                        elif table_name == "evaluations":
                            values = {"human_review": None}
                        else:
                            values = {}
                        await repo.change(uow, row["id"], row["revision"], values)
                        if table_name == "evaluations":
                            reports = repository("evaluation_reports", context.scope)
                            report = await reports.get(uow.connection, row["id"])
                            if report:
                                await reports.change(
                                    uow,
                                    row["id"],
                                    report["revision"],
                                    {"payload": {}, "reproducible": False},
                                )

                return clear

            cleanup.register(kind, handler(table))
    return service
