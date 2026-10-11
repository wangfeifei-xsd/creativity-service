"""审计列表在授权范围内分页、筛选并批量解析名称。"""

from typing import Any

from sqlalchemy import and_, func, or_, select

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import AuthContext
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.audit import AUDIT_NAMES, FIELD_NAMES, AuditService
from creativity_service.modules.iam.authorization import require_platform
from creativity_service.modules.iam.repositories import TABLES
from creativity_service.modules.iam.schemas import AuditFilter, AuditView, DirectoryPage


async def audit_page(
    service: AuditService, session: AdminSession, query: AuditFilter, channel_id: str | None = None
) -> DirectoryPage[AuditView]:
    await service.authorization.authentication.revalidate_admin(session)
    context = session.context
    target = channel_id or context.scope.channel_id
    table = TABLES["audit_events"]
    conditions = [table.c.channel_id == target]
    if isinstance(context, AuthContext):
        if target != context.scope.channel_id:
            raise ServiceError("NOT_FOUND", "渠道不存在", 404)
        await service.authorization.boundary(context, "audit:read", "channel", target)
        scope = context.scope
        conditions.append(
            or_(
                table.c.summary["affected_scopes"].contains([{"environment": scope.environment}]),
                and_(
                    ~table.c.summary.has_key("affected_scopes"),
                    table.c.environment == scope.environment,
                ),
            )
        )
    else:
        account = await service.authorization.authentication.active_account(session.account.id)
        require_platform(account, "audit:read")
        if target != "system":
            require_platform(account, "channel:govern")
    if query.start_at and query.end_at and query.end_at <= query.start_at:
        raise ServiceError("VALIDATION_ERROR", "结束时间须晚于开始时间", 422)
    if any(value and value.tzinfo is None for value in (query.start_at, query.end_at)):
        raise ServiceError("VALIDATION_ERROR", "时间筛选须包含时区", 422)
    for date, column, is_start in (
        (query.start_at, table.c.created_at, True),
        (query.end_at, table.c.created_at, False),
    ):
        if date:
            conditions.append(column >= date if is_start else column < date)
    if query.outcome:
        conditions.append(table.c.outcome == query.outcome)
    if query.request_id.strip():
        conditions.append(table.c.request_id == query.request_id.strip())
    if query.search.strip():
        text = query.search.strip()
        account_table = TABLES["platform_accounts"]
        actor_ids = active_rows(
            select(account_table.c.id).where(
                account_table.c.channel_id == "system",
                or_(
                    account_table.c.display_name.icontains(text, autoescape=True),
                    account_table.c.login_name.icontains(text, autoescape=True),
                ),
            )
        )
        actions = [key for key, value in AUDIT_NAMES.items() if text in value]
        conditions.append(
            or_(
                table.c.actor_id.in_(actor_ids),
                table.c.action.in_(actions),
                table.c.action.icontains(text, autoescape=True),
            )
        )
    async with service.repository.engine.connect() as connection:
        total = await connection.scalar(
            active_rows(select(func.count()).select_from(table).where(*conditions))
        )
        records = [
            dict(row)
            for row in (
                await connection.execute(
                    active_rows(
                        select(table)
                        .where(*conditions)
                        .order_by(table.c.created_at.desc(), table.c.id.desc())
                        .offset(query.offset)
                        .limit(query.limit)
                    )
                )
            ).mappings()
        ]
        references = [
            (r["target_type"], r["target_id"]) for r in records if r["target_type"] != "account"
        ]
        # 早期数据集事件使用 evaluation 类型，依据事件动作解析其真实对象。
        dataset_events = {
            r["target_id"] for r in records if r["action"] == "evaluation.dataset_version"
        }
        references.extend(("evaluation_dataset", identifier) for identifier in dataset_events)
        references.extend(
            (
                "evaluation_dataset_version"
                if r["action"] == "evaluation.dataset_version"
                else "version",
                r["summary"][field],
            )
            for r in records
            for field in ("version_id", "previous_version_id")
            if isinstance(r["summary"].get(field), str)
        )
        names: dict[tuple[str, str], str] = {}
        from creativity_service.storage import metadata

        display_tables = {
            "custom_role": "custom_roles",
            "menu": "iam_menus",
            "channel": "channels",
            "environment": "channel_environments",
            "client": "service_clients",
            "key": "channel_keys",
            "model": "models",
            "agent": "agents",
            "tool": "tools",
            "prompt": "prompts",
            "skill": "skills",
            "mcp_connection": "mcp_connections",
            "model_route": "model_routes",
            "model_connection": "model_connections",
            "version": "resource_versions",
            "evaluation": "evaluations",
            "evaluation_dataset": "evaluation_datasets",
            "evaluation_dataset_version": "evaluation_dataset_versions",
            "schedule": "automation_schedules",
            "batch": "automation_batches",
            "webhook_endpoint": "webhook_endpoints",
            "alert_rule": "alert_rules",
            "budget_policy": "budget_policies",
            "provider_statement": "provider_statements",
        }
        for kind, name in display_tables.items():
            ids = {identifier for ref_kind, identifier in references if ref_kind == kind}
            if not ids:
                continue
            source = metadata.tables[name]
            display = source.c.name if "name" in source.c else source.c.version_label
            source_channel = "system" if name == "iam_menus" else target
            predicates = [source.c.channel_id == source_channel, source.c.id.in_(ids)]
            if isinstance(context, AuthContext):
                for field in ("environment",):
                    if field in source.c:
                        predicates.append(source.c[field] == getattr(context.scope, field))
            names.update(
                {
                    (kind, row.id): f"{row.display_name}（已删除）"
                    if row.is_deleted
                    else row.display_name
                    for row in await connection.execute(
                        # 审计追溯显式读取删除记录的名称，继续限定已授权渠道及环境。
                        select(
                            source.c.id, display.label("display_name"), source.c.is_deleted
                        ).where(*predicates)
                    )
                }
            )
    accounts = await service.repository.accounts(
        [r["actor_id"] for r in records]
        + [r["target_id"] for r in records if r["target_type"] in {"account", "membership"}]
    )
    items = []
    environments = {
        "control": "平台",
        "dev": "开发",
        "test": "测试",
        "fat": "验收",
        "prod": "生产",
    }
    for row in records:
        actor, subject = accounts.get(row["actor_id"]), accounts.get(row["target_id"])
        summary: dict[str, Any] = row["summary"]
        details = {}
        for field, label in (("version_id", "版本"), ("previous_version_id", "原版本")):
            if summary.get(field):
                version_kind = (
                    "evaluation_dataset_version"
                    if row["action"] == "evaluation.dataset_version"
                    else "version"
                )
                details[label] = names.get((version_kind, summary[field]), "名称不可用")
        if isinstance(summary.get("revision"), int):
            details["修订号"] = str(summary["revision"])
        state_names = {
            "ACTIVE": "启用",
            "DISABLED": "停用",
            "DRAFT": "草稿",
            "PUBLISHED": "已发布",
            "RETIRED": "已退役",
        }
        if summary.get("state") in state_names:
            details["变更后状态"] = state_names[summary["state"]]
        items.append(
            AuditView(
                event_id=row["id"],
                actor_id=row["actor_id"],
                actor_name=actor.display_name if actor else None,
                action=row["action"],
                action_name=AUDIT_NAMES.get(row["action"], "其他操作"),
                target_type=row["target_type"],
                target_id=row["target_id"],
                target_name=summary.get("target_name")
                or (
                    subject.display_name
                    if row["target_type"] in {"account", "membership"} and subject
                    else names.get(("evaluation_dataset", row["target_id"]))
                    if row["action"] == "evaluation.dataset_version"
                    else names.get((row["target_type"], row["target_id"]))
                ),
                outcome=row["outcome"],
                outcome_label="已完成" if row["outcome"] == "SUCCEEDED" else "已拒绝",
                time=row["created_at"],
                request_id=row["request_id"],
                changed_fields=[
                    FIELD_NAMES[k] for k in summary.get("changed_fields", []) if k in FIELD_NAMES
                ],
                environment_name=environments.get(row["environment"]),
                details=details,
            )
        )
    return DirectoryPage(items=items, total=total or 0, offset=query.offset, limit=query.limit)
