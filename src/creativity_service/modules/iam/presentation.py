"""管理页面的授权选项，仅组装名称和操作，不替代写服务校验。"""

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.auth.types import WorkspaceOption
from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import NavigationItem, VisibleAction
from creativity_service.core.primitives import Contract, ServiceError
from creativity_service.modules.iam.authorization import effective_actions, require_platform
from creativity_service.modules.iam.repositories import rows
from creativity_service.modules.iam.roles import ACTION_NAMES, ROLE_NAMES
from creativity_service.modules.iam.schemas import RoleView
from creativity_service.modules.iam.services import IamServices


class NamedOption(Contract):
    value: str
    label: str


class ResourceOption(Contract):
    resource_type: str
    resource_id: str
    label: str


class AccessOptions(Contract):
    tabs: list[NavigationItem]
    accounts: list[NamedOption]
    member_accounts: list[NamedOption]
    roles: list[RoleView]
    grantee_roles: list[NamedOption]
    workspaces: list[WorkspaceOption]
    resources: list[ResourceOption]
    grant_actions: list[VisibleAction]
    actions: list[VisibleAction]


async def account_options(
    iam: IamServices, session: AdminSession, channel_id: str | None = None
) -> list[NamedOption]:
    """只向可授予成员的身份提供账号名称，不返回密码状态或平台角色。"""
    if channel_id is not None:
        await iam.access.context(session, channel_id, "membership:manage")
    else:
        await iam.authentication.revalidate_admin(session, governance=True)
        require_platform(
            await iam.authentication.active_account(session.account.id), "channel:create"
        )
    async with iam.accounts.repository.engine.connect() as connection:
        accounts = await rows(connection, "platform_accounts", "system", status="ACTIVE")
    return [
        NamedOption(value=row["id"], label=f"{row['display_name']}（{row['login_name']}）")
        for row in sorted(accounts, key=lambda row: row["login_name"])
    ]


async def access_options(iam: IamServices, session: AdminSession, channel_id: str) -> AccessOptions:
    if not isinstance(session.context, AuthContext):
        raise ServiceError("FORBIDDEN", "请先进入获授权的渠道工作区", 403)
    context = session.context
    if context.scope.channel_id != channel_id:
        raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
    await iam.authentication.revalidate_admin(session)
    member = await iam.authentication.active_member(context)
    grants = await iam.authentication.identities.grants(channel_id)
    scope = context.scope
    allowed = effective_actions(
        member, grants, scope.environment, scope.data_scope_id or "", "channel", channel_id
    )
    if not allowed & {"membership:read", "grant:read"}:
        raise ServiceError("FORBIDDEN", "无权查看成员与授权", 403)
    options = [o for o in await iam.sessions.channels(session) if o.channel_id == channel_id]
    resources = (
        [
            ResourceOption(
                resource_type="channel", resource_id=channel_id, label=options[0].channel_name
            )
        ]
        if options
        else []
    )
    # 现有明确资源经原授权读取器解析；未接入的名称不以内部标识替代。
    if "grant:read" in allowed:
        for grant in await iam.access.list_grants(session, channel_id):
            if grant.resource_name and not any(
                r.resource_type == grant.resource_type and r.resource_id == grant.resource_id
                for r in resources
            ):
                resources.append(
                    ResourceOption(
                        resource_type=grant.resource_type,
                        resource_id=grant.resource_id,
                        label=grant.resource_name,
                    )
                )
    resource_types = {
        "evaluation": ("评测资源", "evaluation:manage"),
        "model": ("模型", "model:manage"),
        "prompt": ("提示词", "prompt:manage"),
        "tool": ("工具", "tool:manage"),
        "mcp_connection": ("MCP 连接", "mcp:manage"),
        "agent": ("智能体", "agent:manage"),
        "skill": ("技能", "skill:manage"),
        "conversation": ("会话", "conversation:write"),
        "memory": ("记忆", "memory:write"),
    }
    for kind, (name, action) in resource_types.items():
        if action in allowed and not any(
            r.resource_type == kind and r.resource_id == "*" for r in resources
        ):
            resources.append(
                ResourceOption(resource_type=kind, resource_id="*", label=f"全部{name}")
            )
    member_accounts = []
    if "grant:manage" in allowed:
        async with iam.accounts.repository.engine.connect() as connection:
            members = await rows(connection, "channel_memberships", channel_id, status="ACTIVE")
        for target in members:
            if set(target["environments"]) <= set(member.environments) and set(
                target["data_scopes"]
            ) <= set(member.data_scopes):
                account = await iam.accounts.repository.account(target["user_id"])
                if account and account.status == "ACTIVE":
                    member_accounts.append(
                        NamedOption(value=account.id, label=account.display_name)
                    )
    for workspace in options:
        if not any(
            r.resource_type == "data_scope" and r.resource_id == workspace.data_scope_id
            for r in resources
        ):
            resources.append(
                ResourceOption(
                    resource_type="data_scope",
                    resource_id=workspace.data_scope_id,
                    label=f"{workspace.environment_name} · {workspace.data_scope_name}",
                )
            )
    return AccessOptions(
        tabs=[
            NavigationItem(navigation_key=key, label=label)
            for key, label, action in (
                ("members", "成员", "membership:read"),
                ("grants", "资源授权", "grant:read"),
            )
            if action in allowed
        ],
        member_accounts=member_accounts,
        accounts=await account_options(iam, session, channel_id)
        if "membership:manage" in allowed
        else [],
        roles=await iam.access.roles(session) if "membership:manage" in allowed else [],
        grantee_roles=[
            NamedOption(value=k, label=v) for k, v in ROLE_NAMES.items() if k != "platform_admin"
        ],
        workspaces=options,
        resources=resources,
        grant_actions=[VisibleAction(action_key=a, label=ACTION_NAMES[a]) for a in sorted(allowed)]
        if "grant:manage" in allowed
        else [],
        actions=[
            VisibleAction(action_key=k, label=v)
            for k, v, required in (
                ("member:create", "添加成员", "membership:manage"),
                ("member:edit", "编辑", "membership:manage"),
                ("member:remove", "移除成员", "membership:manage"),
                ("grant:create", "添加授权", "grant:manage"),
                ("grant:edit", "编辑", "grant:manage"),
                ("grant:revoke", "撤销授权", "grant:manage"),
            )
            if required in allowed
        ],
    )
