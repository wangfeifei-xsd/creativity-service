"""发布锁内重建评测事实，禁止信任缓存的通过标志或客户端报告摘要。"""

from typing import TYPE_CHECKING

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import ServiceError, digest, utcnow
from creativity_service.modules.agents.repositories import repository as agent_repository
from creativity_service.modules.evaluations.reports import build_report
from creativity_service.modules.evaluations.repositories import keys, required
from creativity_service.modules.releases.ports import EvaluationEvidence

if TYPE_CHECKING:
    from creativity_service.modules.evaluations.services import EvaluationService


class StoredEvaluationGate:
    def __init__(self, service: "EvaluationService") -> None:
        self.service = service

    def keys(self, context: AuthContext, refs: tuple[str, ...]) -> list[ResourceKey]:
        return keys(context)

    async def read(
        self, uow: UnitOfWork, context: AuthContext, refs: tuple[str, ...]
    ) -> EvaluationEvidence | None:
        if not refs or len(set(refs)) != len(refs):
            return None
        summaries = []
        reports = []
        expires = []
        for reference in refs:
            task = await required(uow.connection, context.scope, "evaluations", reference)
            await DeletionGuard(context.scope).check(uow, [ContentRef("evaluation", reference)])
            if task["state"] != "COMPLETED" or task["expires_at"] <= utcnow():
                raise ServiceError("EVALUATION_INCOMPLETE", "评测尚未完成或已过期", 422)
            report = await build_report(self.service, uow, context, task)
            eligible = [
                c
                for c in report["candidates"]
                if c["release_passed"] and c["candidate_id"] != task["baseline_candidate_id"]
            ]
            if len(eligible) != 1:
                raise ServiceError(
                    "EVALUATION_BLOCKED", "报告须有唯一通过门禁的发布候选，且无关键失败", 422
                )
            selected = eligible[0]
            mapping = await agent_repository("release_mappings", context.scope).get(
                uow.connection, self.service.agents.mapping_id(context, selected["agent_id"])
            )
            if mapping:
                current = await agent_repository("resource_versions", context.scope).get(
                    uow.connection, mapping["version_id"]
                )
                source = task
                if task["baseline_evaluation_id"]:
                    source = await required(
                        uow.connection, context.scope, "evaluations", task["baseline_evaluation_id"]
                    )
                baseline = next(
                    (
                        c
                        for c in source["candidate_snapshots"]
                        if c["snapshot_id"] == task["baseline_candidate_id"]
                    ),
                    None,
                )
                if (
                    not baseline
                    or not current
                    or baseline["content_digest"] != current["content_digest"]
                    or baseline["dependencies_digest"] != current["dependencies_digest"]
                ):
                    raise ServiceError(
                        "EVALUATION_BASELINE_REQUIRED",
                        "后续发布需要与当前发布版本在相同数据和标签下比较",
                        422,
                    )
            summaries.append(selected)
            reports.append(digest(report))
            expires.append(task["expires_at"])
        first = summaries[0]
        if any(
            (s["agent_id"], s["content_digest"], s["dependencies_digest"])
            != (first["agent_id"], first["content_digest"], first["dependencies_digest"])
            for s in summaries
        ):
            raise ServiceError("EVALUATION_STALE", "报告候选的内容或依赖摘要不一致", 422)
        return EvaluationEvidence(
            channel_id=context.scope.channel_id,
            agent_id=first["agent_id"],
            environment=context.scope.environment,
            content_digest=first["content_digest"],
            dependencies_digest=first["dependencies_digest"],
            report_digest=digest(reports),
            passed=True,
            expires_at=min(expires),
            report_ids=refs,
        )
