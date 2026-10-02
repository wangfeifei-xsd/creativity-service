"""旧 HTTP 适配器的版本兼容注册；新业务通过 MCP 配置，不新增业务专用实现。"""

from dataclasses import dataclass

from creativity_service.core.primitives import ServiceError
from creativity_service.integrations.business.base import (
    CONTRACT_VERSION,
    BusinessAdapter,
    Capability,
)


@dataclass(frozen=True)
class Registration:
    code: str
    name: str
    version: str
    adapter: BusinessAdapter
    capabilities: tuple[Capability, ...]
    contract_version: str = CONTRACT_VERSION


class BusinessRegistry:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], Registration] = {}

    def register(self, item: Registration) -> None:
        if (item.code, item.version) in self.items:
            raise ValueError("适配器版本已登记")
        if item.contract_version != CONTRACT_VERSION:
            raise ValueError("适配器标准契约版本不支持")
        operations = [c.operation for c in item.capabilities]
        if len(operations) != len(set(operations)):
            raise ValueError("适配器能力重复")
        for capability in item.capabilities:
            if not capability.required_actions or getattr(
                type(item.adapter), capability.operation
            ) is getattr(BusinessAdapter, capability.operation):
                raise ValueError("能力必须声明权限且具有对应实现")
        self.items[item.code, item.version] = item

    def resolve(self, code: str, version: str) -> Registration:
        item = self.items.get((code, version))
        if item is None:
            raise ServiceError("BUSINESS_UNSUPPORTED", "适配器版本尚未安装", 422)
        return item
