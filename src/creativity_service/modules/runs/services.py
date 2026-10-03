"""提供给会话和运行编排模块的统一服务入口。"""

from creativity_service.modules.runs.admission import AdmissionService
from creativity_service.modules.runs.interruptions import InterruptionOperations
from creativity_service.modules.runs.queries import QueryService


class RunService(AdmissionService, InterruptionOperations, QueryService):
    """共用同一状态机和事务锁集合，不提供绕过租约的状态改写接口。"""
