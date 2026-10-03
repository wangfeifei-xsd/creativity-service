"""完整等价运行可作为独立样本依据；混合片段与工具夹具保守按全部来源处理。"""

from typing import Any

from creativity_service.core.context import Scope
from creativity_service.core.database import UnitOfWork
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.evaluations.judges import judge
from creativity_service.modules.evaluations.redaction import redact
from creativity_service.modules.evaluations.schemas import CaseInput
from creativity_service.modules.runs.repositories import one, required


async def source_payload(
    uow: UnitOfWork, channel_id: str, identifier: str
) -> tuple[Scope, Any, Any, str]:
    run = await required(uow.connection, "runs", channel_id, id=identifier)
    scope = Scope.model_validate({k: run[k] for k in Scope.model_fields})
    source_uow = UnitOfWork(uow.connection, scope, uow.keys)
    await DeletionGuard(scope).check(source_uow, [ContentRef("run", identifier)])
    input_row = await one(uow.connection, "run_contents", channel_id, id=run["input_ref"])
    result = await one(uow.connection, "run_contents", channel_id, id=run["result_ref"])
    if not input_row or not result or run["state"] != "SUCCEEDED":
        raise ServiceError("INDEPENDENT_SOURCE_INVALID", "独立依据需要完整成功运行", 422)
    return (
        scope,
        redact(input_row["payload"]),
        redact(result["payload"]),
        digest([input_row["payload"], result["payload"]]),
    )


async def validate_independent(
    uow: UnitOfWork, scope: Scope, payload: dict[str, Any]
) -> str | None:
    if payload.get("source_mode", "all") != "independent":
        return None
    refs = payload["source_refs"]
    if (
        not refs
        or not payload["assertions"]
        or payload.get("expected_error")
        or any(a["kind"] not in {"equal", "numeric"} for a in payload["assertions"])
        or payload["fixture"]
        or payload["context"]
        or any(r["resource_type"] != "run" for r in refs)
    ):
        raise ServiceError(
            "INDEPENDENT_SOURCE_INVALID",
            "独立依据需要完整等价成功运行及等值或数值断言，不支持混合上下文或夹具",
            422,
        )
    hashes = set()
    for ref in refs:
        own, source_input, output, value = await source_payload(
            uow, scope.channel_id, ref["resource_id"]
        )
        if (
            own.environment != scope.environment
            or own.data_scope_id != scope.data_scope_id
            or source_input != payload["input"]
            or not judge(CaseInput.model_validate(payload), output, None, set())["passed"]
        ):
            raise ServiceError(
                "INDEPENDENT_SOURCE_INVALID", "每个独立来源必须支持完整样本输入和确定性预期", 422
            )
        hashes.add(value)
    if len(hashes) != 1:
        raise ServiceError("INDEPENDENT_SOURCE_INVALID", "独立运行的完整输入输出不等价", 422)
    return "independent:" + hashes.pop()


async def surviving_sources(
    uow: UnitOfWork, scope: Scope, payload: dict[str, Any], links: list[dict[str, Any]]
) -> list[dict[str, str]]:
    if payload.get("source_mode") != "independent":
        return []
    surviving = []
    for source in payload["source_refs"]:
        matches = [
            link
            for link in links
            if link["source_type"] == source["resource_type"]
            and link["source_id"] == source["resource_id"]
        ]
        if not matches or not str(matches[0]["source_version"]).startswith("independent:"):
            continue
        try:
            own, source_input, _, value = await source_payload(
                uow, scope.channel_id, source["resource_id"]
            )
        except ServiceError as exc:
            if exc.code not in {"CONTENT_DELETED", "NOT_FOUND", "INDEPENDENT_SOURCE_INVALID"}:
                raise
            continue
        if (
            own.environment == scope.environment
            and own.data_scope_id == scope.data_scope_id
            and source_input == payload["input"]
            and matches[0]["source_version"] == "independent:" + value
        ):
            surviving.append(source)
    return surviving
