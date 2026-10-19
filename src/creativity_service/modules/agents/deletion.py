"""智能体删除条件按渠道批量读取，保留历史记录并阻止当前业务引用悬空。"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import UnitOfWork
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.deletion import content_key
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.agents.access import LockedAuthorization
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.iam.authorization import (
    ReadAuthorization,
    action_allowed,
    effective_actions,
)
from creativity_service.modules.iam.schemas import AccessAction
from creativity_service.modules.integrations.automation_tables import metadata as automation_tables
from creativity_service.modules.runs.schemas import TERMINAL
from creativity_service.modules.runs.tables import metadata as run_tables


@dataclass
class DeletionCheck:
    environments: set[str] = field(default_factory=set)
    blocker: tuple[str, str] | None = None


async def deletion_checks(
    connection: AsyncConnection, scope: Scope, agents: list[dict[str, Any]]
) -> dict[str, DeletionCheck]:
    """删除整条渠道资源，显式检查全部环境与主体，仅返回本批智能体的引用事实。"""
    checks = {agent["id"]: DeletionCheck({scope.environment}) for agent in agents}
    if not checks:
        return checks
    states = repository("agent_environment_states", scope).table
    state_rows = (
        await connection.execute(
            active_rows(
                select(states).where(
                    states.c.channel_id == scope.channel_id, states.c.agent_id.in_(checks)
                )
            )
        )
    ).mappings()
    for row in state_rows:
        check = checks[row["agent_id"]]
        check.environments.add(row["environment"])
        if row["is_online"] and row["status"] == "ACTIVE":
            check.blocker = ("AGENT_NOT_STOPPED", "请先将智能体全部下线或停用，再删除")
    codes = {agent["agent_code"]: agent["id"] for agent in agents}
    schedules = automation_tables.tables["automation_schedules"]
    schedule_code = schedules.c.spec["request"]["agent_code"].as_string()
    referenced = await connection.scalars(
        active_rows(
            select(schedule_code)
            .where(schedules.c.channel_id == scope.channel_id, schedule_code.in_(codes))
            .distinct()
        )
    )
    for code in referenced:
        checks[codes[code]].blocker = (
            "AGENT_IN_USE",
            "智能体仍关联定时计划，请先删除计划或更换智能体（暂停的计划也需解除关联）",
        )
    items = automation_tables.tables["automation_items"]
    item_code = items.c.request["agent_code"].as_string()
    pending = await connection.scalars(
        active_rows(
            select(item_code)
            .where(
                items.c.channel_id == scope.channel_id,
                item_code.in_(codes),
                items.c.state.in_(["PENDING", "CLAIMED"]),
            )
            .distinct()
        )
    )
    for code in pending:
        checks[codes[code]].blocker = checks[codes[code]].blocker or (
            "AGENT_IN_USE",
            "智能体仍有待派发的业务任务，请先取消或等待处理完成",
        )
    runs = run_tables.tables["runs"]
    unfinished = await connection.scalars(
        active_rows(
            select(runs.c.agent_id)
            .where(
                runs.c.channel_id == scope.channel_id,
                runs.c.agent_id.in_(checks),
                runs.c.state.not_in(TERMINAL),
            )
            .distinct()
        )
    )
    for identifier in unfinished:
        checks[identifier].blocker = checks[identifier].blocker or (
            "AGENT_RUNNING",
            "智能体仍有未结束的运行，请先取消或等待运行结束",
        )
    return checks


def deletion_blocker(
    policy: ReadAuthorization | LockedAuthorization,
    identifier: str,
    check: DeletionCheck,
) -> tuple[str, str] | None:
    if policy.member is None or any(
        not action_allowed(
            effective_actions(policy.member, list(policy.grants), env, "agent", identifier),
            "agent:manage",
        )
        for env in check.environments
    ):
        return "FORBIDDEN", "删除需要该智能体全部配置环境的管理权限"
    return check.blocker


def deletion_action(
    policy: ReadAuthorization, identifier: str, check: DeletionCheck
) -> AccessAction:
    blocker = deletion_blocker(policy, identifier, check)
    return AccessAction(
        action_key="delete",
        label="删除",
        enabled=blocker is None,
        disabled_reason=blocker[1] if blocker else None,
    )


async def require_undeleted_agents(uow: UnitOfWork, context: AuthContext, codes: list[str]) -> None:
    """批量受理和重试与删除共用内容图锁，删除后的调用编码不能重新建立待派发引用。"""
    uow.require_lock(content_key(context.scope))
    agents = await repository("agents", context.scope, include_deleted=True).find_many(
        uow.connection, "agent_code", codes
    )
    if any(agent["is_deleted"] for agent in agents):
        raise ServiceError("AGENT_DELETED", "请求中的智能体已删除，请重新选择", 409)
