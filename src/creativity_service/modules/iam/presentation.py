"""管理页面的授权选项，仅组装名称和操作，不替代写服务校验。"""

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.auth.types import WorkspaceOption
from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import NavigationItem, VisibleAction
from creativity_service.core.primitives import Contract, ServiceError
from creativity_service.modules.iam.authorization import require_platform
from creativity_service.modules.iam.display import resource_names
from creativity_service.modules.iam.repositories import role_catalog, rows
from creativity_service.modules.iam.roles import (
    ACTION_NAMES,
    PLATFORM_ASSIGNED_CHANNEL_ROLES,
)
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
    policy = await iam.authorization.read_policy(context)
    member = policy.member
    if member is None:
        raise ServiceError("FORBIDDEN", "此操作需要渠道管理身份", 403)
    allowed = policy.actions("channel", channel_id)
    if not allowed & {"membership:read", "grant:read"}:
        raise ServiceError("FORBIDDEN", "无权查看成员与授权", 403)
    from creativity_service.modules.channels.state import ChannelDirectory

    directory = iam.sessions.directory
    options = (
        [
            o
            for o in await directory.authorized_for(session.account.id)
            if o.channel_id == channel_id
        ]
        if isinstance(directory, ChannelDirectory)
        else [o for o in await iam.sessions.channels(session) if o.channel_id == channel_id]
    )
    resources = (
        [
            ResourceOption(
                resource_type="channel", resource_id=channel_id, label=options[0].channel_name
            )
        ]
        if options
        else []
    )
    if "grant:read" in allowed:
        refs = [
            (g.resource_type, g.resource_id)
            for g in policy.grants
            if g.allowed_actions
            and set(g.environments) <= set(member.environments)
            and set(g.data_scopes) <= set(member.data_scopes)
            and g.resource_type != "channel"
        ]
        async with iam.accounts.repository.engine.connect() as connection:
            names = await resource_names(
                connection,
                iam.authorization.resources,
                context,
                [ref for ref in refs if ref[1] != "*"],
            )
        for kind, identifier in dict.fromkeys(refs):
            name = "该类全部资源" if identifier == "*" else names.get((kind, identifier))
            if name:
                resources.append(
                    ResourceOption(resource_type=kind, resource_id=identifier, label=name)
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
        accounts = await iam.accounts.repository.accounts([t["user_id"] for t in members])
        for target in members:
            if set(target["environments"]) <= set(member.environments) and set(
                target["data_scopes"]
            ) <= set(member.data_scopes):
                account = accounts.get(target["user_id"])
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
    async with iam.accounts.repository.engine.connect() as connection:
        catalog = await role_catalog(connection, channel_id)
        account_rows = (
            await rows(connection, "platform_accounts", "system", status="ACTIVE")
            if "membership:manage" in allowed
            else []
        )
    role_views = [
        RoleView(
            role_code=code,
            name=value["name"],
            grant_scope="channel",
            grant_scope_name="渠道",
            actions=[
                VisibleAction(action_key=a, label=ACTION_NAMES[a]) for a in value["allowed_actions"]
            ],
        )
        for code, value in catalog.items()
        if value["state"] == "ACTIVE"
        and "membership:manage" in allowed
        and code not in PLATFORM_ASSIGNED_CHANNEL_ROLES
        and set(value["allowed_actions"]) <= allowed
    ]
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
        accounts=[
            NamedOption(value=r["id"], label=f"{r['display_name']}（{r['login_name']}）")
            for r in sorted(account_rows, key=lambda r: r["login_name"])
        ],
        roles=role_views,
        grantee_roles=[
            NamedOption(value=k, label=v["name"])
            for k, v in catalog.items()
            if v["state"] == "ACTIVE" and k not in PLATFORM_ASSIGNED_CHANNEL_ROLES
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
