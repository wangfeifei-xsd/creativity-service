"""当前页渠道的配置完成情况；按有效关联批量统计，不代替接口鉴权。"""

from collections.abc import Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.types import GrantState, MembershipState
from creativity_service.core.context import Scope
from creativity_service.core.primitives import utcnow
from creativity_service.modules.channels.schemas import ChannelConfigurationStatus
from creativity_service.modules.channels.tables import metadata
from creativity_service.modules.iam.authorization import effective_actions

CONFIGURATION_CHECKS = (
    ("environments", "环境", "environment:manage", "个启用环境", "没有启用环境"),
    (
        "data-scopes",
        "数据域",
        "data_scope:manage",
        "个启用且所属环境启用的数据域",
        "没有启用且所属环境启用的数据域",
    ),
    (
        "clients",
        "接入服务",
        "client:manage",
        "个启用且数据范围有效的接入服务",
        "没有启用且数据范围有效的接入服务",
    ),
    (
        "keys",
        "接入 Key",
        "key:manage",
        "个未过期且接入服务、可调用权限有效的 Key",
        "没有未过期且接入服务、可调用权限有效的 Key",
    ),
)


async def configuration_counts(
    connection: AsyncConnection,
    channel_ids: Sequence[str],
    *,
    scope: Scope | None = None,
    member: MembershipState | None = None,
    grants: Sequence[GrantState] = (),
) -> dict[str, dict[str, int]]:
    """只聚合当前页；渠道模式同时限定环境和各操作可见的数据范围。"""
    result: dict[str, dict[str, int]] = {channel_id: {} for channel_id in channel_ids}
    if not channel_ids:
        return result
    environments, domains, clients, keys = (
        metadata.tables[name]
        for name in ("channel_environments", "data_scopes", "service_clients", "channel_keys")
    )
    environment_filters = [
        environments.c.channel_id.in_(channel_ids),
        environments.c.status == "ACTIVE",
    ]
    if scope:
        environment_filters.append(environments.c.environment == scope.environment)
    enabled_environments = (
        select(environments.c.channel_id, environments.c.environment)
        .where(*environment_filters)
        .cte("configured_environments")
    )
    enabled_domains = (
        select(domains.c.channel_id, domains.c.environment, domains.c.id)
        .join(
            enabled_environments,
            and_(
                domains.c.channel_id == enabled_environments.c.channel_id,
                domains.c.environment == enabled_environments.c.environment,
            ),
        )
        .where(domains.c.status == "ACTIVE")
        .cte("configured_domains")
    )
    domain_groups = (
        select(
            enabled_domains.c.channel_id,
            enabled_domains.c.environment,
            func.jsonb_agg(enabled_domains.c.id).label("ids"),
        )
        .group_by(enabled_domains.c.channel_id, enabled_domains.c.environment)
        .cte("configured_domain_groups")
    )
    client_filters = [
        clients.c.status == "ACTIVE",
        clients.c.scopes != [],
        clients.c.data_scopes != [],
        clients.c.data_scopes.contained_by(domain_groups.c.ids),
    ]
    key_actions = func.jsonb_array_elements_text(keys.c.scopes).table_valued("value")
    key_filters = [
        keys.c.status == "ACTIVE",
        keys.c.expires_at > utcnow(),
        keys.c.environment == clients.c.environment,
        # 服务授权缩减后，Key 与服务须仍有交集，避免失去所有能力却显示完成。
        select(1)
        .select_from(key_actions)
        .where(clients.c.scopes.contains(func.jsonb_build_array(key_actions.c.value)))
        .correlate(keys, clients)
        .exists(),
    ]
    domain_filters = []
    if scope:
        # 成员与授权由调用服务传入，当前调用不重复查询或把其他范围计入状态。
        available_grants = list(grants)
        permitted: dict[str, list[str]] = {
            action: [] for _, _, action, _, _ in CONFIGURATION_CHECKS
        }
        if member is not None:
            for domain_id in member.data_scopes:
                actions = effective_actions(
                    member,
                    available_grants,
                    scope.environment,
                    domain_id,
                    "channel",
                    scope.channel_id,
                )
                for action in permitted:
                    if action in actions:
                        permitted[action].append(domain_id)
        domain_filters.append(enabled_domains.c.id.in_(permitted["data_scope:manage"]))
        client_filters.append(clients.c.data_scopes.contained_by(permitted["client:manage"]))
        key_filters.append(clients.c.data_scopes.contained_by(permitted["key:manage"]))
    enabled_clients = (
        select(clients.c.channel_id, clients.c.id, clients.c.environment, clients.c.data_scopes)
        .join(
            domain_groups,
            and_(
                clients.c.channel_id == domain_groups.c.channel_id,
                clients.c.environment == domain_groups.c.environment,
            ),
        )
        .where(*client_filters)
        .cte("configured_clients")
    )
    # Key 可单独具备读取权限，因此不借用接入服务页面的权限过滤结果。
    key_source = keys.join(
        clients,
        and_(keys.c.channel_id == clients.c.channel_id, keys.c.client_id == clients.c.id),
    ).join(
        domain_groups,
        and_(
            clients.c.channel_id == domain_groups.c.channel_id,
            clients.c.environment == domain_groups.c.environment,
        ),
    )
    queries = {
        "environments": select(enabled_environments.c.channel_id, func.count()).group_by(
            enabled_environments.c.channel_id
        ),
        "data-scopes": select(enabled_domains.c.channel_id, func.count())
        .where(*domain_filters)
        .group_by(enabled_domains.c.channel_id),
        "clients": select(enabled_clients.c.channel_id, func.count()).group_by(
            enabled_clients.c.channel_id
        ),
        "keys": select(keys.c.channel_id, func.count())
        .select_from(key_source)
        .where(
            clients.c.status == "ACTIVE",
            clients.c.data_scopes != [],
            clients.c.data_scopes.contained_by(domain_groups.c.ids),
            *key_filters,
        )
        .group_by(keys.c.channel_id),
    }
    for kind, statement in queries.items():
        for channel_id, count in (await connection.execute(statement)).all():
            result[channel_id][kind] = count
    return result


def configuration_status(
    channel_id: str, counts: dict[str, int], allowed: set[str]
) -> list[ChannelConfigurationStatus]:
    """状态和跳转入口使用服务端操作范围，不让页面推导角色权限。"""
    return [
        ChannelConfigurationStatus(
            key=kind,
            label=label,
            completed=counts.get(kind, 0) > 0,
            message=(
                f"已配置：{counts[kind]} {unit}" if counts.get(kind, 0) else f"未完成：{missing}"
            ),
            path=f"/channels/{channel_id}?tab={kind}",
        )
        for kind, label, action, unit, missing in CONFIGURATION_CHECKS
        if "channel:govern" in allowed or action in allowed
    ]
