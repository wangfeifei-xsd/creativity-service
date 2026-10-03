"""跨主体来源的清理前置检查，25 沿渠道来源图读取派生记录自身范围。"""

from typing import TYPE_CHECKING

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, UnitOfWork
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.evaluations.repositories import required

if TYPE_CHECKING:
    from creativity_service.modules.evaluations.services import EvaluationService


async def require_deleted(
    service: "EvaluationService", uow: UnitOfWork, context: AuthContext, ref: ContentRef, table: str
) -> None:
    try:
        await DeletionGuard(context.scope).check(uow, [ref])
        row = await required(uow.connection, context.scope, table, ref.resource_id)
        cases = []
        if table == "evaluation_cases":
            cases = [row]
        elif table == "evaluation_results":
            cases = [
                await required(uow.connection, context.scope, "evaluation_cases", row["case_id"])
            ]
        elif table in {"evaluations", "evaluation_dataset_versions"}:
            version = (
                row
                if table == "evaluation_dataset_versions"
                else await required(
                    uow.connection,
                    context.scope,
                    "evaluation_dataset_versions",
                    row["dataset_version_id"],
                )
            )
            cases = [
                await required(uow.connection, context.scope, "evaluation_cases", identifier)
                for identifier in version["case_ids"]
            ]
        elif table == "evaluation_fixtures":
            links = await Repository(metadata.tables["source_links"], context.scope).find(
                uow.connection, derived_type="evaluation_fixture", derived_id=ref.resource_id
            )
            await service.source_guard(
                uow,
                context,
                {
                    "source_refs": [
                        {"resource_type": link["source_type"], "resource_id": link["source_id"]}
                        for link in links
                    ]
                },
            )
        for case in cases:
            if not await service.valid_case(uow, context, case):
                return
    except ServiceError as exc:
        if exc.code != "CONTENT_DELETED":
            raise
        return
    raise ServiceError("DELETION_MARKER_REQUIRED", "清理需要有效的来源删除标记")
