"""版本冻结的旧 HTTP 业务协议；这些能力不限制 MCP 工具名称或 schema。"""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import AwareDatetime, Field, JsonValue

from creativity_service.core.context import AuthContext, Environment
from creativity_service.core.primitives import Contract, Identifier, Money, ServiceError

CONTRACT_VERSION: Literal["1.0.0"] = "1.0.0"
Operation = Literal[
    "dictionary",
    "candidates",
    "quote",
    "recheck",
    "risk_facts",
    "policies",
    "metric_catalog",
    "metric_results",
]
OPERATION_NAMES: dict[str, str] = {
    "dictionary": "业务字典",
    "candidates": "候选查询",
    "quote": "报价",
    "recheck": "详情复核",
    "risk_facts": "风险事实",
    "policies": "政策引用",
    "metric_catalog": "指标目录",
    "metric_results": "指标结果",
}
ERRORS: dict[str, tuple[str, int]] = {
    "BUSINESS_UNSUPPORTED": ("业务能力尚不支持", 422),
    "BUSINESS_FORBIDDEN": ("无权访问业务数据", 403),
    "BUSINESS_TIMEOUT": ("业务服务响应超时", 504),
    "BUSINESS_NO_DATA": ("业务服务暂无数据", 404),
    "BUSINESS_UNLISTED": ("业务对象已下架", 410),
    "BUSINESS_NOT_FOUND": ("业务对象不存在", 404),
    "BUSINESS_CONTRACT_CHANGED": ("业务数据契约已变化", 502),
    "BUSINESS_UNAVAILABLE": ("业务服务暂不可用", 503),
}


def business_error(code: str) -> ServiceError:
    message, status = ERRORS[code]
    return ServiceError(code, message, status)


class EntityRef(Contract):
    channel_id: Identifier
    environment: Environment
    data_scope_id: Identifier
    source_type: Identifier
    source_id: Identifier


class BusinessEvidence(Contract):
    reference: EntityRef
    title: str = Field(min_length=1, max_length=256)
    source_version: str = Field(min_length=1, max_length=128)
    observed_at: AwareDatetime


class DictionaryEntry(Contract):
    code: Identifier
    name: str = Field(min_length=1, max_length=256)
    unit: str | None = Field(default=None, min_length=1, max_length=64)


class PriceOption(Contract):
    code: Identifier
    name: str = Field(min_length=1, max_length=256)
    price: Money
    billing_unit: str = Field(min_length=1, max_length=64)


class Candidate(Contract):
    entity_ref: EntityRef
    entity_type: Identifier
    title: str = Field(min_length=1, max_length=256)
    attributes: dict[str, JsonValue]
    price_options: list[PriceOption]
    availability: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"]
    availability_name: str = Field(min_length=1, max_length=128)
    observed_at: AwareDatetime
    source_version: str = Field(min_length=1, max_length=128)
    evidence_refs: list[BusinessEvidence]


class RiskFact(Contract):
    fact_code: Identifier
    name: str = Field(min_length=1, max_length=256)
    value: JsonValue
    unit: str | None = None
    source: EntityRef
    observed_at: AwareDatetime
    validity: Literal["CURRENT", "STALE", "UNKNOWN"]
    evidence_ref: BusinessEvidence


class PolicyReference(Contract):
    policy_code: Identifier
    name: str = Field(min_length=1, max_length=256)
    rule_version: str = Field(min_length=1, max_length=128)
    evidence_ref: BusinessEvidence


class MetricDefinition(Contract):
    metric_code: Identifier
    name: str = Field(min_length=1, max_length=256)
    definition_version: str = Field(min_length=1, max_length=128)
    unit: str = Field(min_length=1, max_length=64)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    supported_periods: list[str] = Field(min_length=1)
    timezone: str = Field(min_length=1, max_length=64)


class MetricResult(Contract):
    metric_code: Identifier
    name: str = Field(min_length=1, max_length=256)
    definition_version: str = Field(min_length=1, max_length=128)
    unit: str = Field(min_length=1, max_length=64)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    period: str = Field(min_length=1, max_length=128)
    timezone: str = Field(min_length=1, max_length=64)
    scope: EntityRef
    completeness: Literal["COMPLETE", "PARTIAL", "UNKNOWN"]
    values: dict[str, str | None]
    source_ref: BusinessEvidence


class BusinessResult(Contract):
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    operation: Operation
    source_request_id: str = Field(min_length=1, max_length=128)
    source_version: str = Field(min_length=1, max_length=128)
    observed_at: AwareDatetime
    items: list[dict[str, Any]]
    has_more: bool
    cursor: str | None = None
    coverage: str = Field(min_length=1, max_length=256)


RESULT_TYPES: dict[str, type[Contract]] = {
    "dictionary": DictionaryEntry,
    "candidates": Candidate,
    "quote": Candidate,
    "recheck": Candidate,
    "risk_facts": RiskFact,
    "policies": PolicyReference,
    "metric_catalog": MetricDefinition,
    "metric_results": MetricResult,
}


class Capability(Contract):
    operation: Operation
    name: str
    public: bool = False
    required_actions: list[str]


@dataclass(frozen=True)
class BusinessCall:
    context: AuthContext
    run_id: str
    operation: Operation
    arguments: dict[str, JsonValue]
    connection: dict[str, Any]


class BusinessAdapter:
    """每个实现仅覆盖自己的能力；未实现时显式拒绝，不能退回通用 SQL 或 URL。"""

    async def dictionary(self, call: BusinessCall) -> BusinessResult:
        raise business_error("BUSINESS_UNSUPPORTED")

    async def candidates(self, call: BusinessCall) -> BusinessResult:
        raise business_error("BUSINESS_UNSUPPORTED")

    async def quote(self, call: BusinessCall) -> BusinessResult:
        raise business_error("BUSINESS_UNSUPPORTED")

    async def recheck(self, call: BusinessCall) -> BusinessResult:
        raise business_error("BUSINESS_UNSUPPORTED")

    async def risk_facts(self, call: BusinessCall) -> BusinessResult:
        raise business_error("BUSINESS_UNSUPPORTED")

    async def policies(self, call: BusinessCall) -> BusinessResult:
        raise business_error("BUSINESS_UNSUPPORTED")

    async def metric_catalog(self, call: BusinessCall) -> BusinessResult:
        raise business_error("BUSINESS_UNSUPPORTED")

    async def metric_results(self, call: BusinessCall) -> BusinessResult:
        raise business_error("BUSINESS_UNSUPPORTED")
