"""渠道页面入口、操作和字段选项的服务端组装。"""

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import NavigationItem, VisibleAction
from creativity_service.core.primitives import Contract
from creativity_service.modules.channels.schemas import ChannelView
from creativity_service.modules.channels.services import SERVICE_ACTIONS, ChannelService
from creativity_service.modules.iam.authorization import effective_actions
from creativity_service.modules.iam.presentation import NamedOption, account_options
from creativity_service.modules.iam.roles import ACTION_NAMES, INDEPENDENT_ACTIONS


class ChannelCreateOptions(Contract):
    accounts: list[NamedOption]
    environments: list[NamedOption]
    business_types: list[NamedOption]
    independent_actions: list[VisibleAction]


class ChannelPage(Contract):
    channel: ChannelView
    tabs: list[NavigationItem]
    actions: list[VisibleAction]
    service_actions: list[VisibleAction]


async def create_options(service: ChannelService, session: AdminSession) -> ChannelCreateOptions:
    return ChannelCreateOptions(
        accounts=await account_options(service.iam, session),
        environments=[
            NamedOption(value=k, label=v)
            for k, v in (("dev", "开发"), ("test", "测试"), ("fat", "验收"), ("prod", "生产"))
        ],
        business_types=[
            NamedOption(value="gamerental", label="租号"),
            NamedOption(value="playmate", label="陪玩"),
        ],
        independent_actions=[
            VisibleAction(action_key=a, label=ACTION_NAMES[a]) for a in sorted(INDEPENDENT_ACTIONS)
        ],
    )


async def page_view(service: ChannelService, session: AdminSession, channel_id: str) -> ChannelPage:
    channel = await service.detail(session, channel_id)
    governance = not isinstance(session.context, AuthContext)
    allowed = {a.action_key for a in channel.actions}
    effective: frozenset[str] = frozenset()
    if isinstance(session.context, AuthContext):
        scope = session.context.scope
        effective = effective_actions(
            await service.iam.authentication.active_member(session.context),
            await service.iam.authentication.identities.grants(channel_id),
            scope.environment,
            scope.data_scope_id or "",
            "channel",
            channel_id,
        )
    actions = []
    for key, label, required, credential in (
        ("channel:edit", "编辑渠道", "channel:manage", False),
        ("environment:create", "创建环境", "environment:manage", False),
        ("environment:edit", "编辑", "environment:manage", False),
        ("data_scope:create", "创建数据域", "data_scope:manage", False),
        ("data_scope:edit", "编辑", "data_scope:manage", False),
        ("client:create", "登记接入服务", "client:manage", True),
        ("client:edit", "编辑", "client:manage", False),
        ("key:create", "创建 Key", "key:manage", True),
        ("key:rotate", "轮换", "key:manage", True),
        ("key:revoke", "吊销", "key:manage", False),
    ):
        if (
            channel.status != "ARCHIVED"
            and (required in allowed or governance)
            and not (credential and governance)
        ):
            actions.append(VisibleAction(action_key=key, label=label))
    if "channel:manage" in allowed or governance:
        if channel.status == "ACTIVE":
            actions.append(VisibleAction(action_key="channel:suspend", label="暂停渠道"))
        if channel.status == "SUSPENDED":
            actions.append(VisibleAction(action_key="channel:resume", label="恢复渠道"))
        if channel.status != "ARCHIVED":
            actions.append(VisibleAction(action_key="channel:archive", label="归档渠道"))
    return ChannelPage(
        channel=channel,
        actions=actions,
        tabs=[
            NavigationItem(navigation_key=k, label=v)
            for k, v, required in (
                ("overview", "概览", "channel:manage"),
                ("environments", "环境", "environment:manage"),
                ("data-scopes", "业务数据域", "data_scope:manage"),
                ("clients", "接入服务", "client:manage"),
                ("keys", "接入 Key", "key:manage"),
                ("members", "成员与权限", "membership:read"),
                ("resources", "资源", "channel:manage"),
                ("usage", "用量", "usage:read"),
                ("audit", "审计", "audit:read"),
            )
            if required in allowed or (governance and k != "members")
        ],
        service_actions=[
            VisibleAction(action_key=a, label=ACTION_NAMES[a])
            for a in sorted(effective & SERVICE_ACTIONS)
        ],
    )
