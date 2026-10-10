"""配置资源目录供渠道详情与授权选择器复用，先限定授权再分页。"""

from typing import Any

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.auth.types import ResourceState
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.primitives import Contract, ServiceError
from creativity_service.modules.channels.schemas import ResourceReference
from creativity_service.modules.iam.authorization import (
    IamAuthorization,
    ReadAuthorization,
    require_platform,
)
from creativity_service.modules.iam.roles import ACTION_NAMES
from creativity_service.modules.iam.schemas import DirectoryPage

CATALOG = {
    "model": ("models", "模型", "models", ("model:manage", "version:read")),
    "agent": ("agents", "智能体", "agents", ("agent:manage", "version:read", "run:create")),
    "prompt": ("prompts", "提示词", "prompts", ("prompt:manage", "version:read")),
    "tool": ("tools", "工具", "tools", ("tool:manage", "version:read")),
    "skill": ("skills", "技能", "skills", ("skill:manage", "version:read")),
    "mcp_connection": ("mcp_connections", "MCP 连接", "mcp-connections", ("mcp:manage",)),
}


class ResourceEntry(Contract):
    resource_type: str
    resource_type_name: str
    resource_id: str
    name: str
    status_label: str
    path: str
    actions: list[VisibleAction]


class ResourceCatalog:
    def __init__(self, engine: AsyncEngine, authorization: IamAuthorization) -> None:
        self.engine, self.authorization = engine, authorization

    @staticmethod
    def statement(
        channel_id: str,
        scope: Scope | None,
        policy: ReadAuthorization | None,
        search: str,
        kind: str | None,
    ) -> Any:
        from creativity_service.storage import metadata

        statements = []
        for resource_type, (name, _, _, permissions) in CATALOG.items():
            if kind and kind != resource_type:
                continue
            table = metadata.tables[name]
            predicates = [table.c.channel_id == channel_id]
            if scope and "environment" in table.c:
                predicates.append(table.c.environment == scope.environment)
            if scope and name == "models":
                connections = metadata.tables["model_connections"]
                predicates.append(
                    table.c.connection_id.in_(
                        active_rows(
                            select(connections.c.id).where(
                                connections.c.channel_id == channel_id,
                                connections.c.environment == scope.environment,
                            )
                        )
                    )
                )
            if policy:
                identifiers = [
                    policy.resource_ids(resource_type, permission) for permission in permissions
                ]
                if all(ids is not None for ids in identifiers):
                    permitted = set().union(*(ids for ids in identifiers if ids is not None))
                    if not permitted:
                        continue
                    predicates.append(table.c.id.in_(permitted))
            if search.strip():
                predicates.append(table.c.name.icontains(search.strip(), autoescape=True))
            statements.append(
                active_rows(
                    select(
                        literal(resource_type).label("kind"),
                        table.c.id,
                        table.c.name,
                        table.c.status,
                    ).where(*predicates)
                )
            )
        return union_all(*statements).subquery() if statements else None

    async def page(
        self,
        session: AdminSession,
        channel_id: str,
        *,
        search: str = "",
        kind: str | None = None,
        limit: int = 20,
        offset: int = 0,
        granting: bool = False,
    ) -> DirectoryPage[ResourceEntry]:
        if not 1 <= limit <= 200 or offset < 0 or (kind and kind not in CATALOG):
            raise ServiceError("VALIDATION_ERROR", "资源类型或分页条件不正确", 422)
        context = session.context
        scope, policy = None, None
        if isinstance(context, AuthContext):
            if context.scope.channel_id != channel_id:
                raise ServiceError("NOT_FOUND", "渠道不存在", 404)
            scope = context.scope
            policy = await self.authorization.read_policy(context)
            if granting and "grant:manage" not in policy.actions("channel", channel_id):
                raise ServiceError("FORBIDDEN", "无权管理资源授权", 403)
        elif granting:
            raise ServiceError("FORBIDDEN", "请进入渠道环境授予资源权限", 403)
        else:
            require_platform(session.account, "channel:govern")
        if channel_id == "system":
            raise ServiceError("NOT_FOUND", "渠道不存在", 404)
        query = self.statement(channel_id, scope, policy, search, kind)
        if query is None:
            return DirectoryPage(items=[], total=0, offset=offset, limit=limit)
        async with self.engine.connect() as connection:
            total = await connection.scalar(active_rows(select(func.count()).select_from(query)))
            records = (
                (
                    await connection.execute(
                        active_rows(
                            select(query)
                            .order_by(query.c.name, query.c.kind, query.c.id)
                            .offset(offset)
                            .limit(limit)
                        )
                    )
                )
                .mappings()
                .all()
            )
        items = []
        for row in records:
            actions: frozenset[str] = frozenset()
            if policy and scope:
                actions = policy.actions(
                    row["kind"],
                    row["id"],
                    ResourceState(
                        scope=scope,
                        resource_type=row["kind"],
                        resource_id=row["id"],
                        name=row["name"],
                        active=True,
                    ),
                )
            items.append(
                ResourceEntry(
                    resource_type=row["kind"],
                    resource_type_name=CATALOG[row["kind"]][1],
                    resource_id=row["id"],
                    name=row["name"],
                    path=f"/{CATALOG[row['kind']][2]}/{row['id']}",
                    status_label={
                        "ACTIVE": "启用",
                        "ENABLED": "启用",
                        "DISABLED": "停用",
                        "ARCHIVED": "已归档",
                        "DRAFT": "草稿",
                    }.get(row["status"], "状态未提供"),
                    actions=[
                        VisibleAction(action_key=a, label=ACTION_NAMES[a]) for a in sorted(actions)
                    ],
                )
            )
        return DirectoryPage(items=items, total=total or 0, offset=offset, limit=limit)

    async def references(self, session: AdminSession, channel_id: str) -> list[ResourceReference]:
        """按资源目录相同范围聚合一次，同环境共享的配置不重复计数。"""
        scope, policy = None, None
        if isinstance(session.context, AuthContext):
            scope = session.context.scope
            if scope.channel_id != channel_id:
                raise ServiceError("NOT_FOUND", "渠道不存在", 404)
            policy = await self.authorization.read_policy(session.context)
        else:
            require_platform(session.account, "channel:govern")
        query = self.statement(channel_id, scope, policy, "", None)
        if query is None:
            return []
        async with self.engine.connect() as connection:
            records = (
                await connection.execute(
                    active_rows(select(query.c.kind, func.count()).group_by(query.c.kind))
                )
            ).all()
        return [
            ResourceReference(
                resource_type=kind, resource_id="*", name=CATALOG[kind][1], count=count
            )
            for kind, count in records
        ]
