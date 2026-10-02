"""预算管理路由与用量接口在同一模块装配，避免产生第二份计价服务。"""

from creativity_service.modules.usage.api import router

__all__ = ["router"]
