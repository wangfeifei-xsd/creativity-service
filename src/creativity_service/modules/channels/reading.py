"""当前渠道读取所需的成员、授权和名称数据，只在调用方连接内加载一次。"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.auth.types import GrantState, MembershipState
from creativity_service.core.context import AuthContext
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.channels.repositories import management_scope_id, rows
from creativity_service.modules.iam.authorization import effective_actions
from creativity_service.modules.iam.repositories import membership_state, one, to_state
from creativity_service.modules.iam.repositories import rows as identity_rows
from creativity_service.modules.iam.roles import GOVERNANCE_ACTIONS


@dataclass(frozen=True)
class ChannelReadData:
    channel_id: str
    member: MembershipState | None
    grants: list[GrantState]
    environments: dict[str, dict[str, Any]]
    domains: dict[str, dict[str, Any]]
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
                to_state(GrantState, r)
                for r in await identity_rows(connection, "resource_grants", channel_id)
            ]
        return cls(
            channel_id,
            member,
            grants,
            {
                r["environment"]: r
                for r in await rows(connection, "channel_environments", channel_id)
            },
            {r["id"]: r for r in await rows(connection, "data_scopes", channel_id)},
            {r["id"]: r for r in await rows(connection, "service_clients", channel_id)},
            platform,
            session.context.scope.environment if isinstance(session.context, AuthContext) else None,
        )

    def visible(
        self,
        environment: str,
        domains: list[str] | None = None,
        *,
        action: str | None = None,
        delegated: list[str] | None = None,
    ) -> bool:
        if self.platform:
            return delegated is None
        if environment != self.current_environment:
            return False
        member = self.member
        if member is None or member.status != "ACTIVE" or environment not in member.environments:
            return False
        management_id = management_scope_id(self.channel_id, environment)
        if (
            domains is not None
            and action in GOVERNANCE_ACTIONS
            and delegated is None
            and management_id in member.data_scopes
        ):
            existing = {
                domain["id"]
                for domain in self.domains.values()
                if domain["environment"] == environment
            }
            return set(domains) <= existing and action in effective_actions(
                member, self.grants, environment, management_id, "channel", self.channel_id
            )
        targets = (
            domains
            if domains is not None
            else (
                [management_scope_id(self.channel_id, environment)]
                if management_scope_id(self.channel_id, environment) in member.data_scopes
                else [d["id"] for d in self.domains.values() if d["environment"] == environment]
            )
        )
        if not targets or not set(targets) <= set(member.data_scopes):
            return False
        needed = set(delegated or []) | ({action} if action else set())
        return all(
            needed
            <= effective_actions(member, self.grants, environment, d, "channel", self.channel_id)
            for d in targets
        )
