"""模型验证只提交冻结调试描述；执行、预算及尝试证据由运行时负责。"""

from typing import Any

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import AuthContext
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.modules.models.policy import (
    CAPABILITY_NAMES,
    CASE_CAPABILITIES,
    PROTOCOLS,
    configuration_digest,
)
from creativity_service.modules.models.repositories import model_key, repository, required
from creativity_service.modules.models.schemas import (
    CaseDefinition,
    DebugExecution,
    TestCase,
    TestCompletion,
    TestInput,
    TestView,
)
from creativity_service.modules.models.services import ModelService
from creativity_service.modules.models.vision import vision_case


def _capability_case(case: TestCase, prompt: str, **options: Any) -> CaseDefinition:
    capability = CASE_CAPABILITIES[case]
    return CaseDefinition(
        case=case,
        name=CAPABILITY_NAMES[capability],
        capability=capability,
        prompt=prompt,
        **options,
    )


CASES = {
    "embedding": _capability_case(case="embedding", prompt="查询用户的长期偏好"),
    "text": _capability_case(case="text", prompt="请只回复：验证完成"),
    "schema": _capability_case(
        case="schema",
        prompt="返回 ok 为 true 的对象",
        output_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean", "const": True}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    ),
    "tools": _capability_case(
        case="tools",
        prompt="调用 echo 工具，text 参数为验证完成",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "echo",
                    "description": "返回给定文字",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
    ),
    "stream_cancel": _capability_case(
        case="stream_cancel",
        prompt="逐个列出一到一百的数字",
        cancel_after_chunks=1,
    ),
    "usage": CaseDefinition(case="usage", name="用量口径", prompt="请只回复：计量验证"),
    "vision": _capability_case(
        case="vision",
        prompt=(
            '识别图片中从左到右的四个图形，只返回 JSON 对象，格式为 {"shapes":'
            '[{"color":"颜色","shape":"形状"}]}，数组必须有四项。'
            "颜色使用 red、green、blue、yellow；形状使用 circle、square、triangle、diamond。"
            "按实际图片填写，不要返回 Markdown 或额外说明。"
        ),
        output_mode="prompt",
    ),
}
STATE_LABELS = {
    "BLOCKED": "不可执行",
    "PENDING": "等待执行",
    "RUNNING": "执行中",
    "PASSED": "验证通过",
    "FAILED": "验证失败",
    "STALE": "配置已过期",
    "FIXTURE": "夹具验证",
}


class ModelTesting:
    def __init__(self, service: ModelService) -> None:
        self.service = service

    async def create(self, session: AdminSession, model_id: str, body: TestInput) -> TestView:
        service = self.service
        context = await service.context(session)
        await service.iam.authorization.boundary(context, "run:create", "model", model_id)
        if len(set(body.cases)) != len(body.cases):
            raise ServiceError("MODEL_TEST_INVALID", "验证用例不能重复", 422)
        test_id = new_id("modeltest")
        source_id = digest([test_id, model_id])
        # 在写事务前生成图片；冻结同一用例的图片和答案，重试时不重新出题。
        cases = [vision_case(CASES[c]) if c == "vision" else CASES[c] for c in body.cases]
        async with service.mutation(
            session,
            "model_tests",
            test_id,
            [record_key(context.scope.channel_id, "source_links", source_id)],
        ) as (uow, context):
            await service.locked_use(uow, session, [model_id])
            model = await required(uow.connection, context.scope, "models", model_id)
            conn = await required(
                uow.connection, context.scope, "model_connections", model["connection_id"]
            )
            await DeletionGuard(context.scope).check(
                uow,
                [ContentRef("model", model_id), ContentRef("version", model["current_version_id"])],
            )
            snapshot = service.snapshot(context, model, conn)
            execution = DebugExecution(test_id=test_id, configuration=snapshot, cases=cases)
            reason = None
            if not PROTOCOLS[conn["protocol"]].enabled:
                reason = "该协议尚未启用"
            elif model["status"] != "ACTIVE" or conn["status"] != "ACTIVE":
                reason = "模型或连接已停用"
            elif service.executor is None:
                reason = "调试服务暂不可用"
            row = await repository(context.scope, "model_tests").add(
                uow,
                test_id,
                {
                    "model_id": model_id,
                    "config_revision": model["revision"],
                    "config_digest": snapshot.config_digest,
                    "execution": execution.model_dump(mode="json"),
                    "cases": body.cases,
                    "results": [],
                    "latency_ms": None,
                    "attempt_ids": [],
                    "state": "BLOCKED" if reason else "PENDING",
                    "run_id": None,
                    "error_code": "MODEL_TEST_UNAVAILABLE" if reason else None,
                    "reason": reason,
                },
            )
            await DeletionGuard(context.scope).link(
                uow, source_id, ContentRef("model", model_id), ContentRef("model_test", test_id)
            )
        if not reason and service.executor:
            try:
                # 执行端口必须按 test_id 幂等；调用过程不持有数据库事务。
                run_id = await service.executor.submit(context, execution)
                async with service.mutation(session, "model_tests", test_id) as (uow, context):
                    latest = await required(uow.connection, context.scope, "model_tests", test_id)
                    if latest["state"] == "PENDING":
                        row = await repository(context.scope, "model_tests").change(
                            uow, test_id, latest["revision"], {"state": "RUNNING", "run_id": run_id}
                        )
                    else:
                        row = latest
            except Exception as exc:
                # 供应商或网络异常字符串可能包含凭据，只保存稳定类别及安全文案。
                async with service.mutation(session, "model_tests", test_id) as (uow, context):
                    latest = await required(uow.connection, context.scope, "model_tests", test_id)
                    row = await repository(context.scope, "model_tests").change(
                        uow,
                        test_id,
                        latest["revision"],
                        {
                            "state": "FAILED",
                            "error_code": exc.code
                            if isinstance(exc, ServiceError)
                            else "MODEL_TEST_SUBMISSION_FAILED",
                            "reason": "验证暂未受理，请稍后重试",
                        },
                    )
        return self.view(row)

    def view(self, row: dict[str, Any]) -> TestView:
        return TestView(
            **{k: row[k] for k in TestView.model_fields if k not in {"state_label", "model_name"}},
            state_label=STATE_LABELS[row["state"]],
            model_name=row["execution"]["configuration"]["model_name"],
        )

    async def get(self, session: AdminSession, test_id: str) -> TestView:
        context = await self.service.context(session)
        async with transaction(
            self.service.engine, context.scope, [content_key(context.scope)]
        ) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("model_test", test_id)])
            row = await required(uow.connection, context.scope, "model_tests", test_id)
        return self.view(row)

    async def list(self, session: AdminSession, model_id: str) -> list[TestView]:
        context = await self.service.context(session)
        async with transaction(
            self.service.engine, context.scope, [content_key(context.scope)]
        ) as uow:
            model = await required(uow.connection, context.scope, "models", model_id)
            await required(
                uow.connection, context.scope, "model_connections", model["connection_id"]
            )
            await DeletionGuard(context.scope).check(uow, [ContentRef("model", model_id)])
            rows = await repository(context.scope, "model_tests").find(
                uow.connection, model_id=model_id
            )
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("model_test", r["id"]) for r in rows]
            )
            return [self.view(row) for row in rows]

    async def complete(
        self, context: AuthContext, test_id: str, completion: TestCompletion
    ) -> TestView:
        """仅供 17 的已验证运行完成回调使用，不暴露管理 HTTP 写接口。"""
        service = self.service
        async with service.engine.connect() as database:
            initial = await required(database, context.scope, "model_tests", test_id)
        model_id = initial["model_id"]
        connection_id = initial["execution"]["configuration"]["connection_id"]
        keys = [
            model_key(context.scope.channel_id),
            content_key(context.scope),
            *[
                record_key(context.scope.channel_id, table, value)
                for table, value in [
                    ("model_tests", test_id),
                    ("models", model_id),
                    ("model_connections", connection_id),
                ]
            ],
        ]
        async with transaction(service.engine, context.scope, keys) as uow:
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("model_test", test_id), ContentRef("model", model_id)]
            )
            row = await required(uow.connection, context.scope, "model_tests", test_id)
            results = [r.model_dump(mode="json") for r in completion.results]
            # 运行回调只接收稳定诊断；不保存模型输出或原始异常文本。
            for item in results:
                item["reason"] = None if item["passed"] else "当前配置未通过该用例验证"
            if row["state"] not in {"PENDING", "RUNNING"}:
                if (
                    row["run_id"] == completion.run_id
                    and row["results"] == results
                    and row["config_digest"] == completion.config_digest
                ):
                    return self.view(row)
                raise ServiceError("MODEL_TEST_STATE_INVALID", "测试不处于可完成状态", 409)
            if (
                row["config_digest"] != completion.config_digest
                or row["run_id"] not in (None, completion.run_id)
                or {r.case for r in completion.results} != set(row["cases"])
                or len(completion.results) != len(row["cases"])
            ):
                raise ServiceError(
                    "MODEL_TEST_EVIDENCE_INVALID", "测试结果与冻结配置或用例不匹配", 422
                )
            model = await required(uow.connection, context.scope, "models", model_id)
            conn = await required(uow.connection, context.scope, "model_connections", connection_id)
            stale = configuration_digest(model, conn) != completion.config_digest
            passed = all(r.passed for r in completion.results)
            state = (
                "STALE"
                if stale
                else "FIXTURE"
                if completion.evidence == "fixture"
                else "PASSED"
                if passed
                else "FAILED"
            )
            now = utcnow()
            row = await repository(context.scope, "model_tests").change(
                uow,
                test_id,
                row["revision"],
                {
                    "state": state,
                    "run_id": completion.run_id,
                    "results": results,
                    "attempt_ids": list(
                        dict.fromkeys(a for r in completion.results for a in r.attempt_ids)
                    ),
                    "latency_ms": completion.latency_ms,
                    "error_code": None if passed else "MODEL_VERIFICATION_FAILED",
                    "reason": "配置已变更，请重新验证" if stale else None,
                },
            )
            if not stale and completion.evidence == "live":
                evidence = dict(model["capabilities"])
                for result in completion.results:
                    capability = CASE_CAPABILITIES.get(result.case)
                    if capability:
                        evidence[capability] = {
                            "state": "SUPPORTED" if result.passed else "UNSUPPORTED",
                            "config_digest": completion.config_digest,
                            "verified_at": now.isoformat(),
                            "reason": None if result.passed else "当前配置未通过该用例验证",
                            "evidence": "live",
                            "test_id": test_id,
                        }
                await repository(context.scope, "models").change(
                    uow, model_id, model["revision"], {"capabilities": evidence, "verified_at": now}
                )
                await repository(context.scope, "model_connections").change(
                    uow,
                    connection_id,
                    conn["revision"],
                    {
                        "health_status": "HEALTHY" if passed else "DEGRADED",
                        "health_reason": None if passed else "最近能力验证未通过",
                        "health_checked_at": now,
                    },
                )
        return self.view(row)
