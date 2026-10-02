"""确定性预置函数，仅通过发布代码登记。"""

from decimal import Decimal, InvalidOperation, localcontext

from creativity_service.core.primitives import utcnow
from creativity_service.integrations.tools import (
    AdapterRegistration,
    AdapterRegistry,
    AdapterRequest,
    AdapterResult,
    ToolAdapterError,
)


class DecimalSum:
    async def invoke(self, request: AdapterRequest) -> AdapterResult:
        values = request.arguments.get("values")
        if not isinstance(values, list) or len(values) > 1000:
            raise ToolAdapterError("TOOL_INPUT_INVALID", "数值列表不正确")
        try:
            with localcontext() as context:
                context.prec = 64
                numbers = [
                    Decimal(value)
                    for value in values
                    if isinstance(value, str) and len(value) <= 64
                ]
                if len(numbers) != len(values):
                    raise ValueError()
                for number in numbers:
                    exponent = number.as_tuple().exponent
                    if (
                        not number.is_finite()
                        or not isinstance(exponent, int)
                        or abs(exponent) > 24
                        or abs(number.adjusted()) > 24
                    ):
                        raise ValueError()
                total = format(sum(numbers, Decimal(0)), "f")
        except (InvalidOperation, ValueError):
            raise ToolAdapterError(
                "TOOL_INPUT_INVALID", "请输入有效且在范围内的十进制数值"
            ) from None
        return AdapterResult(
            data={"sum": total},
            source_request_id=request.attempt_id,
            source_version="decimal-sum-v1",
            observed_at=utcnow(),
        )


def register_builtins(registry: AdapterRegistry) -> None:
    registry.register(
        AdapterRegistration(
            key="decimal_sum",
            name="十进制求和",
            source_type="builtin",
            implementation_version="1",
            actual_effect="READ_ONLY",
            adapter=DecimalSum(),
            sensitive=False,
        )
    )
