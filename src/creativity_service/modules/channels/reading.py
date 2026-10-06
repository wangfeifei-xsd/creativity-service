"""当前渠道读取所需的成员、授权和名称数据，只在调用方连接内加载一次。"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.auth.types import GrantState, MembershipState
from creativity_service.core.context import AuthContext
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.channels.repositories import rows
from creativity_service.modules.iam.authorization import effective_actions
from creativity_service.modules.iam.repositories import membership_state, one, to_state
from creativity_service.modules.iam.repositories import rows as identity_rows


@dataclass(frozen=True)
class ChannelReadData:
    channel_id: str
    member: MembershipState | None
    grants: list[GrantState]
    environments: dict[str, dict[str, Any]]
    clients: dict[str, dict[str, Any]]
    platform: bool
    current_environment: str | None

    @classmethod
    async def load(
        cls, connection: AsyncConnection, session: AdminSession, channel_id: str
    ) -> "ChannelReadData":
        platform = not isinstance(session.context, AuthContext)
        if not platform and session.context.scope.channel_id != channel_id:
            raise ServiceError("NOT_FOUND", "请求资源不在授权范围内", 404)
        member = None
        grants = []
        if not platform:
            row = await one(
                connection, "channel_memberships", channel_id, user_id=session.account.id
            )
            if row:
                member = await membership_state(connection, row)
            grants = [
                to_state(GrantState, row)
                for row in await identity_rows(connection, "resource_grants", channel_id)
            ]
        return cls(
            channel_id,
            member,
            grants,
            {
                row["environment"]: row
                for row in await rows(connection, "channel_environments", channel_id)
            },
            {row["id"]: row for row in await rows(connection, "service_clients", channel_id)},
            platform,
            session.context.scope.environment if isinstance(session.context, AuthContext) else None,
        )

    def visible(
        self, environment: str, *, action: str | None = None, delegated: list[str] | None = None
    ) -> bool:
        if self.platform:
            return delegated is None
        if environment != self.current_environment and action != "environment:manage":
            return False
        return self.authorized(environment, action=action, delegated=delegated)

    def authorized(
        self, environment: str, *, action: str | None = None, delegated: list[str] | None = None
    ) -> bool:
        """校验指定环境授权；全渠道治理使用此方法逐一覆盖全部环境。"""
        if self.platform:
            return delegated is None
        if self.member is None:
            return False
        needed = set(delegated or []) | ({action} if action else set())
        return bool(
            self.member.status == "ACTIVE" and environment in self.member.environments
        ) and needed <= effective_actions(
            self.member, self.grants, environment, "channel", self.channel_id
        )
