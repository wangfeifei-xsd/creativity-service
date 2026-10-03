"""身份操作与变更同事务写入审计，只保存登记的脱敏字段。"""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, Concatenate, Protocol

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import AuthContext, ControlScope, Scope
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, new_id
from creativity_service.modules.iam.authorization import IamAuthorization, require_platform
from creativity_service.modules.iam.repositories import IdentityRepository, save
from creativity_service.modules.iam.roles import ACTION_NAMES
from creativity_service.modules.iam.schemas import AuditView

AUDIT_NAMES = {
    **ACTION_NAMES,
    "auth:login": "登录",
    "auth:external": "外部身份登录",
    "role:edit": "修改渠道角色",
    "usage:statement": "导入供应商账单",
    "integration:configure": "修改运行与事件配置",
    "auth:logout": "退出登录",
    "auth:password": "修改密码",
    "account:create": "创建账号",
    "account:update": "修改账号",
    "account:reset": "重置密码",
    "account:initialize": "初始化管理员",
    "membership:put": "设置渠道成员",
    "membership:remove": "移除渠道成员",
    "grant:put": "设置资源授权",
    "grant:revoke": "撤销资源授权",
    "auth:channel-context": "切换渠道工作区",
    "auth:platform-context": "返回平台工作区",
    "auth:token": "换取服务令牌",
    "channel:create": "开通渠道",
    "channel:update": "修改渠道",
    "channel:suspend": "暂停渠道",
    "channel:resume": "恢复渠道",
    "channel:archive": "归档渠道",
    "environment:create": "创建环境",
    "environment:update": "修改环境",
    "data_scope:create": "创建数据域",
    "data_scope:update": "修改数据域",
    "client:create": "登记接入服务",
    "client:update": "修改接入服务",
    "key:create": "创建接入 Key",
    "key:rotate": "轮换接入 Key",
    "key:revoke": "吊销接入 Key",
}
FIELD_NAMES = {
    "name": "名称",
    "owner": "负责人",
    "channel_code": "渠道编码",
    "business_type": "接入业务类型",
    "environment": "环境",
    "release_policy": "发布策略",
    "retention_policy": "保存策略",
    "external_scope_type": "外部数据域类型",
    "external_scope_id": "外部数据域编号",
    "client_id": "接入服务",
    "scopes": "可调用能力",
    "expires_at": "有效期",
    "display_name": "显示名称",
    "status": "状态",
    "state": "状态",
    "platform_roles": "平台角色",
    "password": "密码凭据",
    "roles": "渠道角色",
    "environments": "可用环境",
    "data_scopes": "数据范围",
    "allowed_actions": "授权动作",
    "resource": "授权资源",
}


class AuditedService(Protocol):
    repository: IdentityRepository


class AuditDenials:
    def __init__(self, action: str, target_type: str, target_index: int | None = None) -> None:
        self.action, self.target_type, self.target_index = action, target_type, target_index

    def __call__[S: AuditedService, **P, R](
        decorator,
        method: Callable[Concatenate[S, AdminSession, P], Awaitable[R]],
    ) -> Callable[Concatenate[S, AdminSession, P], Awaitable[R]]:
        action, target_type, target_index = (
            decorator.action,
            decorator.target_type,
            decorator.target_index,
        )

        @wraps(method)
        async def call(self: S, session: AdminSession, /, *args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return await method(self, session, *args, **kwargs)
            except ServiceError as exc:
                if exc.status not in {403, 404, 409, 422}:
                    raise
                target = (
                    args[target_index]
                    if target_index is not None and len(args) > target_index
                    else session.account.id
                )
                target_id = (
                    target
                    if isinstance(target, str) and 0 < len(target) <= 128
                    else "invalid_target"
                )
                event_id, scope = new_id("audit"), session.context.scope
                # 原业务事务已经回滚，拒绝结果单独留痕；不收录正文、密码或错误细节。
                async with transaction(
                    self.repository.engine,
                    scope,
                    [record_key(scope.channel_id, "audit_events", event_id)],
                ) as uow:
                    await append_event(
                        uow,
                        event_id,
                        session.account.id,
                        session.context.request_id,
                        action,
                        target_type,
                        target_id,
                        outcome="DENIED",
                    )
                raise

        return call


def audit_denials(action: str, target_type: str, target_index: int | None = None) -> AuditDenials:
    return AuditDenials(action, target_type, target_index)


def audit_ranges(*ranges: tuple[list[str], list[str]]) -> list[dict[str, str]]:
    return [
        {"environment": environment, "data_scope_id": data_scope}
        for environment, data_scope in sorted(
            {(e, d) for environments, domains in ranges for e in environments for d in domains}
        )
    ]


async def append_event(
    uow: UnitOfWork,
    event_id: str,
    actor_id: str,
    request_id: str,
    action: str,
    target_type: str,
    target_id: str,
    changed_fields: list[str] | None = None,
    outcome: str = "SUCCEEDED",
    affected_scopes: list[dict[str, str]] | None = None,
    channel_ids: list[str] | None = None,
) -> None:
    fields = changed_fields or []
    if set(fields) - FIELD_NAMES.keys() or outcome not in {"SUCCEEDED", "DENIED"}:
        raise ValueError("审计字段未登记")
    scope = uow.scope
    summary: dict[str, Any] = {"changed_fields": fields}
    if channel_ids is not None:
        if not isinstance(scope, ControlScope) or not channel_ids or "system" in channel_ids:
            raise ValueError("跨渠道统计审计须明确真实渠道范围")
        summary["channel_ids"] = channel_ids
    if affected_scopes is not None:
        if not isinstance(scope, Scope) or not affected_scopes:
            raise ValueError("渠道审计必须记录实际影响范围")
        for affected in affected_scopes:
            Scope.model_validate({"channel_id": scope.channel_id, **affected})
        summary["affected_scopes"] = affected_scopes
    await save(
        uow,
        "audit_events",
        event_id,
        {
            "environment": scope.environment if isinstance(scope, Scope) else "control",
            "data_scope_id": scope.data_scope_id if isinstance(scope, Scope) else None,
            "subject_type": scope.subject_type if isinstance(scope, Scope) else None,
            "subject_id": scope.subject_id if isinstance(scope, Scope) else None,
            "actor_id": actor_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "request_id": request_id,
            "outcome": outcome,
            "summary": summary,
        },
    )


class AuditService:
    def __init__(self, repository: IdentityRepository, authorization: IamAuthorization) -> None:
        self.repository, self.authorization = repository, authorization

    async def query(self, session: AdminSession, limit: int = 50) -> list[AuditView]:
        await self.authorization.authentication.revalidate_admin(session)
        if not 1 <= limit <= 200:
            raise ServiceError("VALIDATION_ERROR", "查询数量须在 1 至 200 之间", 422)
        context = session.context
        if isinstance(context, AuthContext):
            await self.authorization.boundary(
                context, "audit:read", "channel", context.scope.channel_id
            )
            scope: Scope | ControlScope = context.scope
        else:
            account = await self.authorization.authentication.active_account(session.account.id)
            require_platform(account, "audit:read")
            scope = ControlScope(purpose="audit", actor_id=account.id)
        output = []
        for row in await self.repository.audit_rows(scope, limit):
            actor = await self.repository.account(row["actor_id"])
            target_name = None
            if row["target_type"] == "account":
                target = await self.repository.account(row["target_id"])
                target_name = target.display_name if target else None
            elif isinstance(context, AuthContext) and self.authorization.resources:
                resource = await self.authorization.resources.read_current(
                    context, row["target_type"], row["target_id"]
                )
                if resource and resource.scope == context.scope:
                    target_name = resource.name
            summary: dict[str, Any] = row["summary"]
            output.append(
                AuditView(
                    event_id=row["id"],
                    actor_id=row["actor_id"],
                    actor_name=actor.display_name if actor else None,
                    action=row["action"],
                    action_name=AUDIT_NAMES.get(row["action"], "资源操作"),
                    target_type=row["target_type"],
                    target_id=row["target_id"],
                    target_name=target_name,
                    outcome=row["outcome"],
                    outcome_label="已完成" if row["outcome"] == "SUCCEEDED" else "已拒绝",
                    time=row["created_at"],
                    request_id=row["request_id"],
                    changed_fields=[
                        FIELD_NAMES[k]
                        for k in summary.get("changed_fields", [])
                        if k in FIELD_NAMES
                    ],
                )
            )
        return output
