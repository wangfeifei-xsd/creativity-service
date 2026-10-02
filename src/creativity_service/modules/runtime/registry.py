"""服务端注册固定计算步骤，业务场景通过扩展注册表接入。"""

from collections.abc import Awaitable, Callable
from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.primitives import ServiceError

Compute = Callable[[AuthContext, dict[str, Any]], Awaitable[dict[str, Any]]]


class StepRegistry:
    def __init__(self) -> None:
        self.handlers: dict[tuple[str, str], Compute] = {}

    def register(self, entrypoint: str, node_key: str, handler: Compute) -> None:
        if (entrypoint, node_key) in self.handlers:
            raise ValueError("步骤实现不能重复登记")
        self.handlers[entrypoint, node_key] = handler

    def resolve(self, entrypoint: str, node_key: str) -> Compute:
        handler = self.handlers.get((entrypoint, node_key))
        if handler is None:
            raise ServiceError("STEP_NOT_REGISTERED", "流程步骤尚未登记执行实现", 503)
        return handler
