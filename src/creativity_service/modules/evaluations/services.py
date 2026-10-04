"""评测调度只暂停新派发；所有子运行继续使用统一状态、预算与用量账本。"""

from datetime import timedelta
from decimal import Decimal
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import DisplayStatus
from creativity_service.core.database import Repository, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError, new_id, utcnow
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.services import AgentService
from creativity_service.modules.evaluations.admission import dispatch
from creativity_service.modules.evaluations.datasets import DatasetService
from creativity_service.modules.evaluations.judges import judge
from creativity_service.modules.evaluations.reports import FINAL_RESULTS, build_report, save_report
from creativity_service.modules.evaluations.repositories import (
    keys,
    link_id,
    link_keys,
    repository,
    required,
)
from creativity_service.modules.evaluations.schemas import (
    CaseInput,
    ComparisonReport,
    EvaluationControl,
    EvaluationCreate,
    EvaluationList,
    EvaluationReview,
    EvaluationView,
    RerunInput,
)
from creativity_service.modules.evaluations.tables import metadata
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.iam.reading import require_action, resource_state, visible_actions
from creativity_service.modules.runs.schemas import TERMINAL
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.runs.tables import metadata as run_metadata
from creativity_service.modules.tools.tables import metadata as tool_metadata

TASK_LABELS = {
    "RUNNING": "评测中",
    "PAUSED": "已暂停派发",
    "CANCELLING": "取消中",
    "CANCELLED": "已取消",
    "COMPLETED": "已完成",
    "FAILED": "调度失败",
}


class EvaluationService(DatasetService):
    def __init__(
        self,
        engine: AsyncEngine,
        authorization: IamAuthorization,
        agents: AgentService,
        runs: RunService,
    ) -> None:
        self.engine, self.authorization, self.agents, self.runs = (
            engine,
            authorization,
            agents,
            runs,
        )

    async def create(self, context: AuthContext, body: EvaluationCreate) -> EvaluationView:
        await self.require(context, "evaluation:manage", "new")
        await self.require(context, "evaluation:content")
        async with self.engine.connect() as connection:
            version = await required(
                connection, context.scope, "evaluation_dataset_versions", body.dataset_version_id
            )
        await self.require(context, "evaluation:read", version["dataset_id"])
        if len({c.version_id for c in body.candidates}) != len(body.candidates):
            raise ServiceError("CANDIDATE_DUPLICATE", "候选版本不能重复", 422)
        if body.release_target and body.execution_mode != "fixture":
            raise ServiceError("FIXED_DATA_REQUIRED", "发布评测必须使用固定数据", 422)
        if body.baseline_evaluation_id and body.baseline_candidate_version_id is None:
            raise ServiceError("BASELINE_REQUIRED", "历史基线需要指定候选版本", 422)
        candidates: list[dict[str, Any]] = []
        experiment_definition: dict[str, Any] | None = None
        experiment_versions: set[str] = set()
        if body.experiment_prompt_id and (
            len(body.candidates) < 2
            or body.execution_mode != "fixture"
            or body.release_target
            or not body.baseline_candidate_version_id
            or body.baseline_evaluation_id
        ):
            raise ServiceError(
                "EXPERIMENT_INVALID",
                "提示词实验需要至少两个候选、同批基线及固定样本，不能自动用于发布",
                422,
            )
        for value in body.candidates:
            spec = await self.agents.freeze_candidate(
                context,
                value.version_id,
                value.revision,
                "evaluation",
                published_dependencies=body.release_target,
            )
            if body.experiment_prompt_id:
                prompt = next(
                    (
                        v
                        for v in spec.versions
                        if v.version_id == spec.definition.bindings.prompt_version
                    ),
                    None,
                )
                if (
                    not prompt
                    or prompt.resource_type != "prompt"
                    or prompt.resource_id != body.experiment_prompt_id
                ):
                    raise ServiceError(
                        "EXPERIMENT_PROMPT_MISMATCH", "候选须绑定所选提示词的版本", 422
                    )
                definition = spec.definition.model_dump(mode="json")
                definition["bindings"]["prompt_version"] = None
                if experiment_definition is not None and definition != experiment_definition:
                    raise ServiceError(
                        "EXPERIMENT_NOT_COMPARABLE", "提示词实验的其他流程、模型和策略必须相同", 422
                    )
                experiment_definition = definition
                experiment_versions.add(prompt.version_id)
            candidates.append(
                {
                    "snapshot_id": spec.snapshot_id,
                    "version_id": value.version_id,
                    "version_label": spec.versions[0].version_label,
                    "agent_id": spec.agent_id,
                    "agent_name": spec.agent_name,
                    "content_digest": spec.content_digest,
                    "dependencies_digest": spec.dependencies_digest,
                    "published_dependencies": all(
                        v.state == "PUBLISHED" for v in spec.versions[1:]
                    ),
                    "dependencies": [
                        {
                            "resource_type": v.resource_type,
                            "version_id": v.version_id,
                            "version_label": v.version_label,
                            "content_digest": v.content_digest,
                        }
                        for v in spec.versions[1:]
                    ],
                    "limits": spec.definition.limits.model_dump(mode="json"),
                }
            )
        if body.experiment_prompt_id and len(experiment_versions) != len(body.candidates):
            raise ServiceError("EXPERIMENT_DUPLICATE", "提示词实验候选必须采用不同提示词版本", 422)
        cost_limit = body.budget.cost_limit
        if cost_limit and (
            cost_limit.amount <= 0
            or any(
                not c["limits"]["cost_limit"]
                or c["limits"]["cost_limit"]["currency"] != cost_limit.currency
                for c in candidates
            )
        ):
            raise ServiceError("EVALUATION_BUDGET", "金额预算需要每个候选具备同币种运行硬上限", 422)
        baseline_id = None
        if body.baseline_candidate_version_id:
            baseline_candidates = candidates
            if body.baseline_evaluation_id:
                await self.require(context, "evaluation:read", body.baseline_evaluation_id)
                async with self.engine.connect() as connection:
                    baseline = await required(
                        connection, context.scope, "evaluations", body.baseline_evaluation_id
                    )
                if (
                    baseline["dataset_digest"] != version["content_digest"]
                    or baseline["execution_mode"] != body.execution_mode
                    or baseline["state"] != "COMPLETED"
                ):
                    raise ServiceError(
                        "BASELINE_INCOMPARABLE", "基线必须已完成且使用相同数据、标签及模式", 422
                    )
                baseline_candidates = baseline["candidate_snapshots"]
            selected = [
                c
                for c in baseline_candidates
                if c["version_id"] == body.baseline_candidate_version_id
            ]
            if len(selected) != 1:
                raise ServiceError("BASELINE_INVALID", "基线候选不在指定评测中", 422)
            baseline_id = selected[0]["snapshot_id"]
        identifier = new_id("evaluation")
        entries = [
            (new_id("evaluation_result"), case, c["snapshot_id"])
            for c in candidates
            for case in version["case_ids"]
        ]
        if body.budget.max_runs < len(entries):
            raise ServiceError("EVALUATION_BUDGET", "运行次数预算不足以覆盖全部候选样本", 422)
        links = [
            (
                ContentRef("evaluation_dataset_version", version["id"]),
                ContentRef("evaluation", identifier),
            )
        ]
        links += [
            (ContentRef("agent_candidate", c["snapshot_id"]), ContentRef("evaluation", identifier))
            for c in candidates
        ]
        for result_id, case_id, _ in entries:
            links += [
                (
                    ContentRef("evaluation_case", case_id),
                    ContentRef("evaluation_result", result_id),
                ),
                (ContentRef("evaluation", identifier), ContentRef("evaluation_result", result_id)),
            ]
        records = [
            ("evaluations", identifier),
            ("evaluation_reports", identifier),
            *[("evaluation_results", i) for i, _, _ in entries],
        ]
        async with transaction(
            self.engine, context.scope, keys(context, records) + link_keys(context, links)
        ) as uow:
            await locked_require(uow, context, "evaluation:manage", "evaluation", "new")
            await locked_require(
                uow, context, "evaluation:content", "evaluation", version["dataset_id"]
            )
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("evaluation_dataset_version", version["id"])]
            )
            identity = context.model_copy(
                update={
                    "principal_type": "worker",
                    "session_id": None,
                    "token_digest": None,
                    "granted_actions": frozenset(),
                }
            )
            task = await repository("evaluations", context.scope).add(
                uow,
                identifier,
                {
                    "name": body.name,
                    "dataset_version_id": version["id"],
                    "dataset_digest": version["content_digest"],
                    "candidate_snapshots": candidates,
                    "baseline_evaluation_id": body.baseline_evaluation_id,
                    "baseline_candidate_id": baseline_id,
                    "execution_mode": body.execution_mode,
                    "config": body.model_dump(
                        mode="json",
                        include={
                            "concurrency",
                            "budget",
                            "minimum_pass_rate",
                            "maximum_regression",
                            "release_target",
                            "experiment_prompt_id",
                        },
                    ),
                    "identity": identity.model_dump(mode="json"),
                    "state": "RUNNING",
                    "human_review": None,
                    "expires_at": utcnow() + timedelta(days=body.expires_days),
                },
            )
            for result_id, case_id, candidate_id in entries:
                await repository("evaluation_results", context.scope).add(
                    uow, result_id, self.entry_values(identifier, case_id, candidate_id, 1)
                )
            for source, derived in links:
                await DeletionGuard(context.scope).link(
                    uow, link_id(source, derived), source, derived
                )
            await save_report(self, uow, context, task)
        return await self.detail(context, identifier)

    @staticmethod
    def entry_values(
        evaluation_id: str, case_id: str, candidate_id: str, attempt: int
    ) -> dict[str, Any]:
        return {
            "evaluation_id": evaluation_id,
            "case_id": case_id,
            "candidate_id": candidate_id,
            "attempt_number": attempt,
            "run_id": None,
            "state": "PENDING",
            "judgment": None,
            "human_label": None,
            "claimed_at": None,
        }

    async def detail(self, context: AuthContext, identifier: str) -> EvaluationView:
        policy = await self.authorization.read_policy(context)
        async with self.engine.connect() as connection:
            task = await required(connection, context.scope, "evaluations", identifier)
            version = await required(
                connection, context.scope, "evaluation_dataset_versions", task["dataset_version_id"]
            )
            dataset = await required(
                connection, context.scope, "evaluation_datasets", version["dataset_id"]
            )
        permissions = policy.actions(
            "evaluation", identifier, resource_state(context, "evaluation", task)
        )
        require_action(permissions, "evaluation:read")
        content_allowed = "evaluation:content" in permissions
        review = None
        if content_allowed and task["human_review"]:
            async with transaction(self.engine, context.scope, keys(context)) as uow:
                review = (await build_report(self, uow, context, task))["human_review"]
        return self.evaluation_view(task, version, dataset, permissions, review)

    @staticmethod
    def evaluation_view(
        task: dict[str, Any],
        version: dict[str, Any],
        dataset: dict[str, Any],
        permissions: frozenset[str],
        review: dict[str, Any] | None = None,
    ) -> EvaluationView:
        content_allowed = "evaluation:content" in permissions
        actions = [("refresh", "更新报告", "evaluation:read")]
        if content_allowed:
            actions.append(("review_result", "复核样本结果", "evaluation:review"))
        if task["state"] not in {"CANCELLING", "CANCELLED"}:
            actions.append(("rerun", "重跑样本", "evaluation:manage"))
        if task["state"] == "RUNNING":
            actions.append(("pause", "暂停派发", "evaluation:manage"))
        if task["state"] == "PAUSED":
            actions.append(("resume", "继续派发", "evaluation:manage"))
        if task["state"] in {"RUNNING", "PAUSED", "CANCELLING"}:
            actions.append(("cancel", "取消评测", "evaluation:manage"))
        if task["state"] == "COMPLETED":
            actions.append(("review", "审阅报告", "evaluation:review"))
        return EvaluationView(
            evaluation_id=task["id"],
            name=task["name"],
            dataset_name=dataset["name"],
            dataset_version_label=version["version_label"],
            revision=task["revision"],
            state=DisplayStatus(
                value=task["state"],
                label=TASK_LABELS[task["state"]],
                tone="success" if task["state"] == "COMPLETED" else "default",
            ),
            execution_mode=task["execution_mode"],
            execution_mode_label="固定数据评测"
            if task["execution_mode"] == "fixture"
            else "实时只读评测",
            external_data_may_change=task["execution_mode"] == "live_readonly",
            candidates=task["candidate_snapshots"],
            config=task["config"],
            human_review=review,
            created_at=task["created_at"],
            actions=visible_actions(permissions, actions),
        )

    async def list_evaluations(self, context: AuthContext) -> EvaluationList:
        policy = await self.authorization.read_policy(context)
        require_action(policy.actions("evaluation", "scope"), "evaluation:read")
        async with self.engine.connect() as connection:
            rows = await repository("evaluations", context.scope).find(connection)
            rows = [
                r
                for r in rows
                if "evaluation:read"
                in policy.actions("evaluation", r["id"], resource_state(context, "evaluation", r))
            ]
            versions = await repository("evaluation_dataset_versions", context.scope).get_many(
                connection, [r["dataset_version_id"] for r in rows]
            )
            datasets = await repository("evaluation_datasets", context.scope).get_many(
                connection, [v["dataset_id"] for v in versions.values()]
            )
        items = []
        for row in rows:
            version = versions.get(row["dataset_version_id"])
            dataset = datasets.get(version["dataset_id"]) if version else None
            if not version or not dataset:
                raise ServiceError("NOT_FOUND", "评测样本集不存在", 404)
            items.append(self.evaluation_view(row, version, dataset, frozenset()))
        return EvaluationList(
            items=items,
            actions=visible_actions(
                policy.actions("evaluation", "new"), [("create", "发起评测", "evaluation:manage")]
            ),
        )

    async def comparison(self, context: AuthContext, identifier: str) -> ComparisonReport:
        await self.require(context, "evaluation:read", identifier)
        content = await self.allowed(context, "evaluation:content", identifier)
        async with transaction(self.engine, context.scope, keys(context)) as uow:
            task = await required(uow.connection, context.scope, "evaluations", identifier)
            payload = await build_report(self, uow, context, task)
        from creativity_service.core.primitives import digest

        payload["report_digest"] = digest(payload)
        if not content:
            for result in payload["results"]:
                result["judgment"] = result["human_label"] = None
            payload["human_review"] = None
        return ComparisonReport.model_validate(payload)

    async def control(
        self, context: AuthContext, identifier: str, body: EvaluationControl
    ) -> EvaluationView:
        await self.require(context, "evaluation:manage", identifier)
        async with transaction(
            self.engine,
            context.scope,
            keys(context, [("evaluations", identifier), ("evaluation_reports", identifier)]),
        ) as uow:
            await locked_require(uow, context, "evaluation:manage", "evaluation", identifier)
            task = await required(uow.connection, context.scope, "evaluations", identifier)
            allowed = {
                "pause": {"RUNNING"},
                "resume": {"PAUSED"},
                "cancel": {"RUNNING", "PAUSED", "CANCELLING"},
            }
            if task["state"] not in allowed[body.operation]:
                raise ServiceError("EVALUATION_STATE", "当前状态不允许此操作", 409)
            task = await repository("evaluations", context.scope).change(
                uow,
                identifier,
                body.revision,
                {
                    "state": {"pause": "PAUSED", "resume": "RUNNING", "cancel": "CANCELLING"}[
                        body.operation
                    ]
                },
            )
            await save_report(self, uow, context, task)
        await self.tick(context, identifier)
        return await self.detail(context, identifier)

    async def review(
        self, context: AuthContext, identifier: str, body: EvaluationReview
    ) -> EvaluationView:
        await self.require(context, "evaluation:review", identifier)
        await self.require(context, "evaluation:content", identifier)
        async with transaction(
            self.engine,
            context.scope,
            keys(context, [("evaluations", identifier), ("evaluation_reports", identifier)]),
        ) as uow:
            await locked_require(uow, context, "evaluation:review", "evaluation", identifier)
            await locked_require(uow, context, "evaluation:content", "evaluation", identifier)
            task = await required(uow.connection, context.scope, "evaluations", identifier)
            if task["state"] != "COMPLETED":
                raise ServiceError("EVALUATION_INCOMPLETE", "请在全部样本完成后审阅报告", 409)
            await DeletionGuard(context.scope).check(uow, [ContentRef("evaluation", identifier)])
            if (
                not (await build_report(self, uow, context, task))["reproducible"]
                and task["execution_mode"] == "fixture"
            ):
                raise ServiceError("CONTENT_DELETED", "固定评测来源已失效，不能审阅", 410)
            task = await repository("evaluations", context.scope).change(
                uow,
                identifier,
                body.revision,
                {
                    "human_review": {
                        **body.label.model_dump(),
                        "reviewer": context.actor_id,
                        "reviewed_at": utcnow().isoformat(),
                    }
                },
            )
            await save_report(self, uow, context, task)
        return await self.detail(context, identifier)

    async def review_result(
        self, context: AuthContext, result_id: str, body: EvaluationReview
    ) -> EvaluationView:
        async with self.engine.connect() as connection:
            entry = await required(connection, context.scope, "evaluation_results", result_id)
        identifier = entry["evaluation_id"]
        await self.require(context, "evaluation:review", identifier)
        await self.require(context, "evaluation:content", identifier)
        async with transaction(
            self.engine,
            context.scope,
            keys(
                context,
                [
                    ("evaluations", identifier),
                    ("evaluation_results", result_id),
                    ("evaluation_reports", identifier),
                ],
            ),
        ) as uow:
            await locked_require(uow, context, "evaluation:review", "evaluation", identifier)
            await locked_require(uow, context, "evaluation:content", "evaluation", identifier)
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("evaluation_result", result_id)]
            )
            entry = await required(uow.connection, context.scope, "evaluation_results", result_id)
            task = await required(uow.connection, context.scope, "evaluations", identifier)
            case = await required(
                uow.connection, context.scope, "evaluation_cases", entry["case_id"]
            )
            if not await self.valid_case(uow, context, case):
                raise ServiceError("CONTENT_DELETED", "样本来源已删除，不能标注", 410)
            if entry["state"] not in {"PASSED", "FAILED"}:
                raise ServiceError("EVALUATION_INCOMPLETE", "只能标注已完成运行的结果", 409)
            judgment = entry["judgment"] or {}
            manual = judgment.get("human_required", False)
            state = (
                "PASSED"
                if manual and body.label.decision == "approved"
                else "FAILED"
                if manual
                else entry["state"]
            )
            await repository("evaluation_results", context.scope).change(
                uow,
                result_id,
                body.revision,
                {
                    "human_label": {
                        **body.label.model_dump(),
                        "reviewer": context.actor_id,
                        "reviewed_at": utcnow().isoformat(),
                    },
                    "state": state,
                },
            )
            task = await repository("evaluations", context.scope).change(
                uow, identifier, task["revision"], {"human_review": None}
            )
            await save_report(self, uow, context, task)
        return await self.detail(context, identifier)

    async def rerun(self, context: AuthContext, result_id: str, body: RerunInput) -> EvaluationView:
        async with self.engine.connect() as connection:
            result = await required(connection, context.scope, "evaluation_results", result_id)
        identifier, new_result = result["evaluation_id"], new_id("evaluation_result")
        await self.require(context, "evaluation:manage", identifier)
        links = [
            (
                ContentRef("evaluation_case", result["case_id"]),
                ContentRef("evaluation_result", new_result),
            ),
            (ContentRef("evaluation", identifier), ContentRef("evaluation_result", new_result)),
        ]
        async with transaction(
            self.engine,
            context.scope,
            keys(
                context,
                [
                    ("evaluations", identifier),
                    ("evaluation_results", new_result),
                    ("evaluation_reports", identifier),
                ],
            )
            + link_keys(context, links),
        ) as uow:
            await locked_require(uow, context, "evaluation:manage", "evaluation", identifier)
            task = await required(uow.connection, context.scope, "evaluations", identifier)
            rows = await repository("evaluation_results", context.scope).find(
                uow.connection, evaluation_id=identifier
            )
            if (
                task["state"] in {"CANCELLING", "CANCELLED"}
                or result["state"] not in FINAL_RESULTS
                or any(
                    r["candidate_id"] == result["candidate_id"]
                    and r["case_id"] == result["case_id"]
                    and r["attempt_number"] > result["attempt_number"]
                    for r in rows
                )
            ):
                raise ServiceError("EVALUATION_STATE", "只能重跑最新的已结束单例", 409)
            if len(rows) >= task["config"]["budget"]["max_runs"]:
                raise ServiceError("EVALUATION_BUDGET", "评测运行次数预算已用尽", 429)
            task = await repository("evaluations", context.scope).change(
                uow, identifier, body.revision, {"state": "RUNNING", "human_review": None}
            )
            await repository("evaluation_results", context.scope).add(
                uow,
                new_result,
                self.entry_values(
                    identifier,
                    result["case_id"],
                    result["candidate_id"],
                    result["attempt_number"] + 1,
                ),
            )
            for source, derived in links:
                await DeletionGuard(context.scope).link(
                    uow, link_id(source, derived), source, derived
                )
            await save_report(self, uow, context, task)
        return await self.detail(context, identifier)

    async def tick(self, context: AuthContext, identifier: str) -> None:
        await self.require(context, "evaluation:manage", identifier)
        async with self.engine.connect() as connection:
            entries = await repository("evaluation_results", context.scope).find(
                connection, evaluation_id=identifier
            )
        records = [
            ("evaluations", identifier),
            ("evaluation_reports", identifier),
            *[("evaluation_results", r["id"]) for r in entries],
        ]
        claims, cancellations = [], []
        async with transaction(self.engine, context.scope, keys(context, records)) as uow:
            await locked_require(uow, context, "evaluation:manage", "evaluation", identifier)
            task = await required(uow.connection, context.scope, "evaluations", identifier)
            if task["state"] not in {"RUNNING", "PAUSED", "CANCELLING"}:
                return
            entries = await repository("evaluation_results", context.scope).find(
                uow.connection, evaluation_id=identifier
            )
            if any(("evaluation_results", r["id"]) not in records for r in entries):
                return
            sources_valid = True
            try:
                await DeletionGuard(context.scope).check(
                    uow, [ContentRef("evaluation", identifier)]
                )
            except ServiceError as exc:
                if exc.code != "CONTENT_DELETED":
                    raise
                sources_valid = False
            repo = repository("evaluation_results", context.scope)
            candidates = {c["snapshot_id"]: c for c in task["candidate_snapshots"]}
            for entry in entries:
                if entry["state"] in FINAL_RESULTS:
                    continue
                case = await required(
                    uow.connection, context.scope, "evaluation_cases", entry["case_id"]
                )
                valid = await self.valid_case(uow, context, case)
                if not valid or not sources_valid:
                    if entry["run_id"]:
                        cancellations.append(entry["run_id"])
                    changed = await repo.change(
                        uow,
                        entry["id"],
                        entry["revision"],
                        {"state": "INVALID", "judgment": None, "human_label": None},
                    )
                    entry.update(changed)
                    continue
                if entry["run_id"]:
                    run = await Repository(run_metadata.tables["runs"], context.scope).get(
                        uow.connection, entry["run_id"]
                    )
                    if run is None:
                        raise ServiceError("EVALUATION_RUN_MISSING", "评测子运行缺失", 503)
                    if run["state"] in TERMINAL:
                        output = None
                        try:
                            await DeletionGuard(context.scope).check(
                                uow, [ContentRef("run", run["id"])]
                            )
                            if run["result_ref"]:
                                stored = await Repository(
                                    run_metadata.tables["run_contents"], context.scope
                                ).get(uow.connection, run["result_ref"])
                                output = stored["payload"] if stored else None
                            evidence = await Repository(
                                tool_metadata.tables["tool_calls"], context.scope
                            ).find(uow.connection, run_id=run["id"])
                            judgment = judge(
                                CaseInput.model_validate(case["payload"]),
                                output,
                                (run["error"] or {}).get("code"),
                                {i for e in evidence for i in e["evidence_ids"]},
                            )
                            state = (
                                "CANCELLED"
                                if run["state"] == "CANCELLED"
                                else "PASSED"
                                if judgment["passed"]
                                else "FAILED"
                            )
                        except ServiceError as exc:
                            if exc.code != "CONTENT_DELETED":
                                raise
                            state, judgment = "INVALID", None
                        entry.update(
                            await repo.change(
                                uow,
                                entry["id"],
                                entry["revision"],
                                {"state": state, "judgment": judgment},
                            )
                        )
                    elif task["state"] == "CANCELLING":
                        cancellations.append(run["id"])
                elif task["state"] == "CANCELLING":
                    entry.update(
                        await repo.change(
                            uow, entry["id"], entry["revision"], {"state": "UNEXECUTED"}
                        )
                    )
                elif entry["state"] == "DISPATCHING" and entry["claimed_at"] < utcnow() - timedelta(
                    seconds=30
                ):
                    entry.update(
                        await repo.change(
                            uow,
                            entry["id"],
                            entry["revision"],
                            {"state": "PENDING", "claimed_at": None},
                        )
                    )
            active = sum(r["state"] in {"RUNNING", "DISPATCHING"} for r in entries)
            # 按每个候选的运行硬上限保守预留，未知用量也不会腾出额度。
            reserved = sum(
                candidates[r["candidate_id"]]["limits"]["token_limit"]
                for r in entries
                if r["run_id"] or r["state"] == "DISPATCHING"
            )
            cost_budget = task["config"]["budget"].get("cost_limit")
            cost_reserved = sum(
                (
                    Decimal(candidates[r["candidate_id"]]["limits"]["cost_limit"]["amount"])
                    for r in entries
                    if (r["run_id"] or r["state"] == "DISPATCHING") and cost_budget
                ),
                Decimal(0),
            )
            for entry in entries:
                if task["state"] != "RUNNING" or active >= task["config"]["concurrency"]:
                    break
                if entry["state"] != "PENDING":
                    continue
                candidate = candidates[entry["candidate_id"]]
                ceiling = candidate["limits"]["token_limit"]
                case = await required(
                    uow.connection, context.scope, "evaluation_cases", entry["case_id"]
                )
                snapshot = await self.agents_candidate(uow, context, candidate["snapshot_id"])
                valid_input = Draft202012Validator(snapshot["definition"]["input_schema"]).is_valid(
                    {**case["payload"]["context"], **case["payload"]["input"]}
                )
                candidate_cost = (
                    Decimal(candidate["limits"]["cost_limit"]["amount"])
                    if cost_budget
                    else Decimal(0)
                )
                exhausted = reserved + ceiling > task["config"]["budget"]["token_limit"] or bool(
                    cost_budget and cost_reserved + candidate_cost > Decimal(cost_budget["amount"])
                )
                if not valid_input or exhausted:
                    entry.update(
                        await repo.change(
                            uow,
                            entry["id"],
                            entry["revision"],
                            {
                                "state": "INVALID" if not valid_input else "UNEXECUTED",
                                "judgment": {
                                    "passed": False,
                                    "checks": [],
                                    "violations": [],
                                    "critical": False,
                                    "reason": "输入契约不匹配"
                                    if not valid_input
                                    else "评测预算不足",
                                    "semantic": None,
                                },
                            },
                        )
                    )
                    continue
                entry.update(
                    await repo.change(
                        uow,
                        entry["id"],
                        entry["revision"],
                        {"state": "DISPATCHING", "claimed_at": utcnow()},
                    )
                )
                claims.append((dict(entry), candidate, case))
                reserved += ceiling
                cost_reserved += candidate_cost
                active += 1
            if all(r["state"] in FINAL_RESULTS for r in entries):
                state = "CANCELLED" if task["state"] == "CANCELLING" else "COMPLETED"
                task = await repository("evaluations", context.scope).change(
                    uow, identifier, task["revision"], {"state": state}
                )
            await save_report(self, uow, context, task)
        for run_id in cancellations:
            await self.runs.cancel(context, run_id)
        for entry, candidate, case in claims:
            try:
                await dispatch(self, context, entry, candidate, case)
            except ServiceError as exc:
                async with transaction(
                    self.engine, context.scope, keys(context, [("evaluation_results", entry["id"])])
                ) as uow:
                    current = await required(
                        uow.connection, context.scope, "evaluation_results", entry["id"]
                    )
                    if not current["run_id"] and current["state"] == "DISPATCHING":
                        state = (
                            "PENDING"
                            if exc.code == "EVALUATION_DISPATCH_STOPPED"
                            else "INVALID"
                            if exc.code == "CONTENT_DELETED"
                            else "FAILED"
                        )
                        judgment = (
                            None
                            if state != "FAILED"
                            else {
                                "passed": False,
                                "checks": [],
                                "violations": [
                                    {"name": exc.message, "category": "quality", "passed": False}
                                ],
                                "critical": False,
                                "semantic": None,
                            }
                        )
                        await repository("evaluation_results", context.scope).change(
                            uow,
                            current["id"],
                            current["revision"],
                            {"state": state, "judgment": judgment, "claimed_at": None},
                        )

    async def agents_candidate(
        self, uow: Any, context: AuthContext, identifier: str
    ) -> dict[str, Any]:
        import json

        from creativity_service.modules.agents.repositories import required as agent_required

        row = await agent_required(uow.connection, context.scope, "agent_candidates", identifier)
        await DeletionGuard(context.scope).check(uow, [ContentRef("agent_candidate", identifier)])
        return dict(json.loads(row["spec"]["payload_json"]))

    async def sweep(self, channel_id: str) -> None:
        table = metadata.tables["evaluations"]
        async with self.engine.connect() as connection:
            tasks = [
                dict(r)
                for r in (
                    await connection.execute(
                        select(table).where(
                            table.c.channel_id == channel_id,
                            table.c.state.in_(["RUNNING", "PAUSED", "CANCELLING"]),
                        )
                    )
                ).mappings()
            ]
        for task in tasks:
            context = AuthContext.model_validate(task["identity"])
            if context.scope.channel_id != channel_id:
                raise ServiceError("SCOPE_MISMATCH", "评测调度渠道不匹配", 403)
            try:
                await self.tick(context, task["id"])
            except ServiceError as exc:
                # 永久授权失败结束调度；临时故障保留可诊断状态，由下一轮补偿。
                async with transaction(
                    self.engine, context.scope, keys(context, [("evaluations", task["id"])])
                ) as uow:
                    current = await required(
                        uow.connection, context.scope, "evaluations", task["id"]
                    )
                    if current["state"] not in {"RUNNING", "PAUSED", "CANCELLING"}:
                        continue
                    config = {
                        **current["config"],
                        "dispatch_error": {
                            "code": exc.code,
                            "message": "派发授权已失效"
                            if exc.status in {401, 403}
                            else "派发依赖暂不可用",
                        },
                    }
                    await repository("evaluations", context.scope).change(
                        uow,
                        task["id"],
                        current["revision"],
                        {
                            "config": config,
                            "state": "FAILED" if exc.status in {401, 403} else current["state"],
                        },
                    )
