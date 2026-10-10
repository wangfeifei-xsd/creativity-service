"""统一 MySQL 逻辑删除字段；只回填删除语义，停用、归档和撤销不视为删除。"""

import sqlalchemy as sa

from alembic import context, op

revision = "0049_soft_delete"
down_revision = "0048_mysql_milvus"
branch_labels = None
depends_on = None

TABLES = (
    "admissions",
    "agent_candidates",
    "agent_environment_states",
    "agent_release_records",
    "agents",
    "alert_rules",
    "artifacts",
    "attempts",
    "audit_events",
    "automation_batches",
    "automation_items",
    "automation_schedules",
    "budget_alerts",
    "budget_policies",
    "budget_reservations",
    "builtin_roles",
    "channel_code_index",
    "channel_environments",
    "channel_keys",
    "channel_lifecycle_events",
    "channel_memberships",
    "channels",
    "checkpoints",
    "context_snapshots",
    "conversation_summaries",
    "conversation_turns",
    "conversations",
    "credentials",
    "custom_roles",
    "delegation_keys",
    "delegation_nonces",
    "deletion_jobs",
    "deletion_markers",
    "deletion_receipts",
    "deletion_work_items",
    "dispatch_outbox",
    "evaluation_cases",
    "evaluation_dataset_versions",
    "evaluation_datasets",
    "evaluation_fixtures",
    "evaluation_reports",
    "evaluation_results",
    "evaluations",
    "evidence_refs",
    "iam_menus",
    "iam_revocations",
    "key_identity_index",
    "key_rotations",
    "mcp_checks",
    "mcp_connections",
    "mcp_discoveries",
    "mcp_imports",
    "mcp_oauth_flows",
    "mcp_oauth_tokens",
    "memories",
    "memory_consolidations",
    "memory_deletion_jobs",
    "memory_embeddings",
    "memory_index_tasks",
    "memory_policies",
    "memory_preferences",
    "memory_retrievals",
    "memory_sources",
    "memory_versions",
    "messages",
    "model_connections",
    "model_routes",
    "model_tests",
    "models",
    "platform_accounts",
    "platform_limits",
    "platform_quota_occupancies",
    "price_versions",
    "prompt_samples",
    "prompt_tests",
    "prompts",
    "provider_catalog",
    "provider_statements",
    "recovery_barriers",
    "release_mappings",
    "release_snapshots",
    "resource_grants",
    "resource_references",
    "resource_uses",
    "resource_versions",
    "run_contents",
    "run_events",
    "run_idempotency",
    "run_leases",
    "run_occupancies",
    "run_recoveries",
    "run_steps",
    "runs",
    "service_clients",
    "skill_files",
    "skill_tests",
    "skills",
    "source_links",
    "subject_review_bindings",
    "tool_calls",
    "tools",
    "transaction_lock_slots",
    "usage_adjustments",
    "usage_aggregates",
    "usage_events",
    "usage_exchange_rates",
    "usage_exports",
    "usage_records",
    "webhook_deliveries",
    "webhook_endpoints",
    "creativity_alembic_version",
)

STATE_COLUMNS = {
    "deletion_work_items": ["state"],
    "agents": ["status"],
    "agent_environment_states": ["status"],
    "evaluations": ["state"],
    "evaluation_results": ["state"],
    "memories": ["status"],
    "memory_sources": ["status"],
    "memory_versions": ["status"],
    "memory_deletion_jobs": ["state"],
    "memory_consolidations": ["state"],
    "memory_index_tasks": ["state"],
    "conversations": ["status"],
    "messages": ["status"],
    "conversation_summaries": ["status"],
    "deletion_jobs": ["state"],
    "delegation_keys": ["status"],
    "automation_schedules": ["state"],
    "automation_batches": ["state"],
    "automation_items": ["state"],
    "webhook_endpoints": ["state"],
    "webhook_deliveries": ["state"],
    "model_connections": ["status"],
    "models": ["status"],
    "model_routes": ["status"],
    "model_tests": ["state"],
    "resource_versions": ["state"],
    "credentials": ["state"],
    "artifacts": ["state"],
    "recovery_barriers": ["state"],
    "runs": ["state"],
    "dispatch_outbox": ["state"],
    "run_steps": ["state"],
    "attempts": ["state"],
    "run_occupancies": ["state"],
    "platform_accounts": ["status"],
    "channel_memberships": ["status"],
    "custom_roles": ["state"],
    "alert_rules": ["state"],
    "channels": ["status"],
    "channel_environments": ["status"],
    "service_clients": ["status"],
    "channel_keys": ["status"],
    "prompts": ["status"],
    "prompt_tests": ["status"],
    "tools": ["status"],
    "tool_calls": ["state"],
    "skills": ["status"],
    "mcp_connections": ["status"],
    "mcp_oauth_flows": ["state"],
    "mcp_oauth_tokens": ["state"],
    "usage_records": ["state"],
    "budget_policies": ["status"],
    "budget_reservations": ["status"],
    "admissions": ["status"],
    "budget_alerts": ["status"],
    "usage_exports": ["state"],
    "platform_limits": ["status"],
    "platform_quota_occupancies": ["status"],
}


def upgrade() -> None:
    connection = op.get_bind()
    inspector = None if context.is_offline_mode() else sa.inspect(connection)
    for name in TABLES:
        # MySQL DDL 会提交；中断后重试只补缺失列，不重置已登记的删除标记。
        missing = (
            name != "creativity_alembic_version"
            if inspector is None
            else "is_deleted" not in {c["name"] for c in inspector.get_columns(name)}
        )
        if missing:
            op.add_column(
                name, sa.Column("is_deleted", sa.Boolean(), nullable=True, comment="是否已逻辑删除")
            )
        table = sa.table(name, sa.column("is_deleted", sa.Boolean()))
        op.execute(table.update().where(table.c.is_deleted.is_(None)).values(is_deleted=False))
    for name, columns in STATE_COLUMNS.items():
        table = sa.table(
            name,
            sa.column("is_deleted", sa.Boolean()),
            *(sa.column(key, sa.String(32)) for key in columns),
        )
        op.execute(
            table.update()
            .where(sa.or_(*(table.c[key].in_(["DELETED", "DELETING"]) for key in columns)))
            .values(is_deleted=True)
        )
    # 记忆撤销包含人工撤销和来源删除，仅有删除标记的记录回填为已删除。
    op.execute(
        sa.text("""
        UPDATE memories AS m JOIN deletion_markers AS d
          ON d.channel_id = m.channel_id AND d.environment = m.environment
         AND d.subject_type <=> m.subject_type AND d.subject_id <=> m.subject_id
         AND d.target_type = 'memory' AND d.target_id = m.id
        SET m.is_deleted = true
    """)
    )
    # 停用与移除共用旧状态，只按最后一次写入后的成功删除审计识别已移除记录。
    op.execute(
        sa.text("""
        UPDATE channel_memberships AS m SET m.is_deleted = true
        WHERE m.status = 'DISABLED' AND EXISTS (
            SELECT 1 FROM audit_events AS a WHERE a.channel_id = m.channel_id
              AND a.target_type = 'account' AND a.target_id = m.user_id
              AND a.action = 'membership:remove' AND a.outcome = 'SUCCEEDED'
              AND a.created_at >= m.updated_at
        )
    """)
    )
    op.execute(
        sa.text("""
        UPDATE resource_grants AS g SET g.is_deleted = true
        WHERE JSON_LENGTH(g.allowed_actions) = 0 AND EXISTS (
            SELECT 1 FROM audit_events AS a WHERE a.channel_id = g.channel_id
              AND a.target_type = 'resource_grant' AND a.target_id = g.id
              AND a.action = 'grant:revoke' AND a.outcome = 'SUCCEEDED'
              AND a.created_at >= g.updated_at
        )
    """)
    )


def downgrade() -> None:
    connection = op.get_bind()
    # 有墓碑时移除字段会使菜单、角色等重新出现；只允许空库或无删除记录的回退。
    for name in TABLES:
        table = sa.table(name, sa.column("is_deleted", sa.Boolean()))
        if connection.scalar(sa.select(sa.literal(1)).where(table.c.is_deleted.is_(True)).limit(1)):
            raise RuntimeError("存在逻辑删除记录，不能回退并重新暴露数据")
    for name in reversed(TABLES):
        if name != "creativity_alembic_version":
            op.drop_column(name, "is_deleted")
