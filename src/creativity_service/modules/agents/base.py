"""智能体服务公共边界与跨模块短事务锁。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import DisplayStatus, VisibleAction
from creativity_service.core.database import UnitOfWork
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.dependencies import DependencyResolver
from creativity_service.modules.agents.repositories import (
    RESOURCE_TABLES,
    dependency_rows,
    repository,
    required,
)
from creativity_service.modules.agents.schemas import (
    AgentDefinition,
    AgentDependency,
    AgentVersionView,
    AgentView,
)
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.memory.repositories import policy_key as memory_key
from creativity_service.modules.models.repositories import model_key
from creativity_service.modules.releases.ports import AgentDebugRunner, EvaluationGate
from creativity_service.modules.skills.services import SkillService
from creativity_service.modules.tools.services import ToolService
from creativity_service.modules.usage.repositories import ledger_key

ENVIRONMENTS = {"dev": "开发", "test": "测试", "fat": "验收", "prod": "生产"}


def status(value: str) -> DisplayStatus:
    return DisplayStatus(
        value=value,
        label={
            "ACTIVE": "已启用",
            "OFFLINE": "已下线",
            "EMERGENCY_STOP": "已紧急停用",
            "DRAFT": "草稿",
            "PUBLISHED": "已发布",
            "RETIRED": "已归档",
        }.get(value, "状态不可用"),
        tone="success" if value in {"ACTIVE", "PUBLISHED"} else "default",
    )


class AgentKernel:
    def __init__(
        self,
        engine: AsyncEngine,
        authorization: IamAuthorization,
        tools: ToolService,
        skills: SkillService,
        budgets: BudgetService,
        evaluation: EvaluationGate | None = None,
        runner: AgentDebugRunner | None = None,
    ) -> None:
        self.engine, self.authorization = engine, authorization
        self.dependencies = DependencyResolver(tools, skills)
        self.budgets, self.evaluation, self.runner = budgets, evaluation, runner

    async def require(self, context: AuthContext, action: str, agent_id: str) -> None:
        await self.authorization.boundary(context, action, "agent", agent_id)

    async def actions(
        self, context: AuthContext, agent_id: str, values: list[tuple[str, str, str]]
    ) -> list[VisibleAction]:
        return [
            VisibleAction(action_key=key, label=label)
            for key, label, action in values
            if (await self.authorization.check(context, action, "agent", agent_id)).allowed
        ]

    def keys(
        self, context: AuthContext, agent_id: str, *records: tuple[str, str]
    ) -> list[ResourceKey]:
        scope = context.scope
        return [
            content_key(scope),
            policy_key(scope.channel_id),
            policy_key("system"),
            model_key(scope.channel_id),
            memory_key(scope),
            ledger_key(scope.channel_id),
            record_key(scope.channel_id, "agents", agent_id),
            *(record_key(scope.channel_id, table, identifier) for table, identifier in records),
        ]

    async def dependency_keys(
        self, context: AuthContext, definition: AgentDefinition
    ) -> list[ResourceKey]:
        """先枚举受锁资源，锁下再解析；依赖闭包变化会在发布处拒绝。"""
        keys = []
        async with self.engine.connect() as connection:
            rows = await dependency_rows(connection, context.scope, definition.bindings.ids())
        for row in rows:
            keys.append(record_key(context.scope.channel_id, "resource_versions", row["id"]))
            if row["resource_type"] in RESOURCE_TABLES:
                keys.append(
                    record_key(
                        context.scope.channel_id,
                        RESOURCE_TABLES[row["resource_type"]],
                        row["resource_id"],
                    )
                )
            if row["resource_type"] == "tool":
                connection_id = row["content"].get("binding", {}).get("connection_id")
                if connection_id:
                    keys.append(
                        record_key(context.scope.channel_id, "mcp_connections", connection_id)
                    )
            if row["resource_type"] == "model_route":
                for model in row["content"].get("models", []):
                    keys.append(
                        record_key(
                            context.scope.channel_id, "credentials", model["provider_credential_id"]
                        )
                    )
        return keys

    async def raw(
        self, context: AuthContext, version_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        async with self.engine.connect() as connection:
            version = await required(connection, context.scope, "resource_versions", version_id)
            if version["resource_type"] != "agent":
                raise ServiceError("NOT_FOUND", "智能体版本不存在", 404)
            agent = await required(connection, context.scope, "agents", version["resource_id"])
        return agent, version

    async def locked_version(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        agent_id: str,
        version_id: str,
        revision: int,
        action: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        await locked_require(uow, context, action, "agent", agent_id)
        agent = await required(uow.connection, context.scope, "agents", agent_id)
        version = await required(uow.connection, context.scope, "resource_versions", version_id)
        if version["resource_type"] != "agent" or version["resource_id"] != agent_id:
            raise ServiceError("NOT_FOUND", "智能体版本不存在", 404)
        if version["revision"] != revision:
            raise ServiceError("REVISION_CONFLICT", "草稿已变更，请刷新后重新检查", 409)
        await DeletionGuard(context.scope).check(
            uow, [ContentRef("agent", agent_id), ContentRef("version", version_id)]
        )
        return agent, version

    async def agent_view(self, context: AuthContext, row: dict[str, Any]) -> AgentView:
        async with self.engine.connect() as connection:
            state = await repository("agent_environment_states", context.scope).get(
                connection, self.mapping_id(context, row["id"])
            )
        return AgentView(
            agent_id=row["id"],
            **{k: row[k] for k in ("agent_code", "name", "description", "owner", "revision")},
            status=status(state["status"] if state else row["status"]),
            actions=await self.actions(
                context,
                row["id"],
                [
                    ("edit", "编辑信息", "agent:manage"),
                    ("create_version", "新增草稿", "agent:manage"),
                    ("offline", "下线", "release:publish"),
                    ("emergency_stop", "紧急停用", "release:publish"),
                    ("enable", "启用", "release:publish"),
                ],
            ),
        )

    async def version_view(self, context: AuthContext, row: dict[str, Any]) -> AgentVersionView:
        actions = [
            ("validate", "校验", "agent:manage"),
            ("test", "调试", "run:create"),
            ("release", "发布", "release:publish"),
        ]
        if row["state"] == "DRAFT":
            actions.insert(0, ("edit", "编辑配置", "agent:manage"))
        return AgentVersionView(
            version_id=row["id"],
            version_label=row["version_label"],
            revision=row["revision"],
            status=status(row["state"]),
            definition=AgentDefinition.model_validate(row["content"]),
            content_digest=row["content_digest"],
            actions=await self.actions(context, row["resource_id"], actions),
        )

    async def dependency_view(
        self, uow: UnitOfWork, context: AuthContext, row: dict[str, Any]
    ) -> AgentDependency:
        resource = await required(
            uow.connection, context.scope, RESOURCE_TABLES[row["resource_type"]], row["resource_id"]
        )
        return AgentDependency(
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            version_id=row["id"],
            name=resource["name"],
            version_label=row["version_label"],
            revision=row["revision"],
            state=status(row["state"]),
            content_digest=row["content_digest"],
        )

    @staticmethod
    def mapping_id(context: AuthContext, agent_id: str) -> str:
        return digest([context.scope.channel_id, context.scope.environment, "agent", agent_id])
