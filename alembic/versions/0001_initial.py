"""未上线阶段的完整建库基线，冻结模型 1.8.0 的最终表、字段、注释与索引。

修订号沿用合并前的最新版本，已有开发库升级时直接识别，不重建表或回填数据。
后续结构变化新增修订，不从运行时模型动态生成本基线。
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0034_admission_indexes"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admissions",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="运行标识"),
        sa.Column("policy_refs", postgresql.JSONB(), nullable=True, comment="命中配额策略"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="占用状态"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, comment="核查时间"),
        sa.Column("snapshot", postgresql.JSONB(), nullable=True, comment="运行来源及候选模型快照"),
        comment="运行准入配额占用",
    )
    op.create_index("ix_admissions_0", "admissions", ["channel_id", "id"], unique=False)
    op.create_index("ix_admissions_1", "admissions", ["channel_id", "run_id"], unique=False)
    op.create_index(
        "ix_admissions_active", "admissions", ["channel_id", "status", "created_at"], unique=False
    )
    op.create_table(
        "agent_candidates",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("agent_id", sa.String(length=64), nullable=True, comment="智能体标识"),
        sa.Column("source_version_id", sa.String(length=64), nullable=True, comment="来源版本标识"),
        sa.Column("source_revision", sa.BigInteger(), nullable=True, comment="来源草稿修订号"),
        sa.Column("purpose", sa.String(length=32), nullable=True, comment="候选用途"),
        sa.Column("content_digest", sa.String(length=64), nullable=True, comment="内容摘要"),
        sa.Column(
            "dependencies_digest", sa.String(length=64), nullable=True, comment="完整依赖摘要"
        ),
        sa.Column("candidate_digest", sa.String(length=64), nullable=True, comment="候选组合摘要"),
        sa.Column("spec", postgresql.JSONB(), nullable=True, comment="不可变执行定义"),
        comment="冻结智能体候选快照",
    )
    op.create_index("ix_agent_candidates_0", "agent_candidates", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_agent_candidates_1",
        "agent_candidates",
        ["channel_id", "agent_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "agent_environment_states",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="目标环境"),
        sa.Column("agent_id", sa.String(length=64), nullable=True, comment="智能体标识"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="当前环境启用状态"),
        sa.Column("reason", sa.String(length=1024), nullable=True, comment="状态变更原因"),
        comment="智能体各环境停用状态",
    )
    op.create_index(
        "ix_agent_environment_states_0",
        "agent_environment_states",
        ["channel_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_environment_states_1",
        "agent_environment_states",
        ["channel_id", "environment", "agent_id"],
        unique=False,
    )
    op.create_table(
        "agent_release_records",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="目标环境"),
        sa.Column("agent_id", sa.String(length=64), nullable=True, comment="智能体标识"),
        sa.Column("version_id", sa.String(length=64), nullable=True, comment="生效版本标识"),
        sa.Column("version_label", sa.String(length=128), nullable=True, comment="版本名称"),
        sa.Column(
            "previous_version_id", sa.String(length=64), nullable=True, comment="原生效版本标识"
        ),
        sa.Column("source_revision", sa.BigInteger(), nullable=True, comment="发布来源修订号"),
        sa.Column("operation", sa.String(length=32), nullable=True, comment="操作类型"),
        sa.Column("note", sa.String(length=1024), nullable=True, comment="操作说明"),
        sa.Column("actor_id", sa.String(length=128), nullable=True, comment="操作人标识"),
        sa.Column("actor_name", sa.String(length=128), nullable=True, comment="操作人名称"),
        sa.Column("content_digest", sa.String(length=64), nullable=True, comment="内容摘要"),
        sa.Column(
            "dependencies_digest", sa.String(length=64), nullable=True, comment="完整依赖摘要"
        ),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=True, comment="评测报告引用"),
        sa.Column("checks", postgresql.JSONB(), nullable=True, comment="发布检查证据"),
        comment="智能体发布检查与操作记录",
    )
    op.create_index(
        "ix_agent_release_records_0", "agent_release_records", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_agent_release_records_1",
        "agent_release_records",
        ["channel_id", "environment", "agent_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("agent_code", sa.String(length=64), nullable=True, comment="调用编码"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="智能体名称"),
        sa.Column("description", sa.Text(), nullable=True, comment="用途说明"),
        sa.Column("owner", sa.String(length=128), nullable=True, comment="负责人标识"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="启用状态"),
        comment="智能体资源",
    )
    op.create_index("ix_agents_0", "agents", ["channel_id", "id"], unique=False)
    op.create_index("ix_agents_1", "agents", ["channel_id", "agent_code"], unique=False)
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="规则名称"),
        sa.Column("kind", sa.String(length=32), nullable=True, comment="监测类型"),
        sa.Column("threshold", sa.BigInteger(), nullable=True, comment="触发次数阈值"),
        sa.Column("window_seconds", sa.BigInteger(), nullable=True, comment="监测窗口秒数"),
        sa.Column("endpoint_id", sa.String(length=64), nullable=True, comment="告警投递端点"),
        sa.Column("owner_key", sa.String(length=64), nullable=True, comment="创建身份摘要"),
        sa.Column("identity", postgresql.JSONB(), nullable=True, comment="原执行身份快照"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="启停状态"),
        sa.Column("active", sa.Boolean(), nullable=True, comment="当前是否告警"),
        sa.Column("generation", sa.BigInteger(), nullable=True, comment="触发周期序号"),
        sa.Column("last_value", sa.BigInteger(), nullable=True, comment="最近观察次数"),
        sa.Column("pending_events", postgresql.JSONB(), nullable=True, comment="待生成投递事件"),
        sa.Column(
            "client_ids",
            postgresql.JSONB(),
            nullable=True,
            comment="订阅的调用服务列表；空列表仅包含配置者运行",
        ),
        comment="外部告警规则",
    )
    op.create_index("ix_alert_rules_0", "alert_rules", ["channel_id", "id"], unique=False)
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("name", sa.String(length=255), nullable=True, comment="文件显示名称"),
        sa.Column("content_type", sa.String(length=128), nullable=True, comment="内容媒体类型"),
        sa.Column("object_key", sa.String(length=1024), nullable=True, comment="私有对象路径"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True, comment="文件字节数"),
        sa.Column("sha256", sa.String(length=64), nullable=True, comment="内容摘要"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="暂存及可用状态"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, comment="保存到期时间"),
        sa.Column(
            "upload_expires_at", sa.DateTime(timezone=True), nullable=True, comment="暂存到期时间"
        ),
        sa.Column(
            "registered_at", sa.DateTime(timezone=True), nullable=True, comment="登记完成时间"
        ),
        comment="受控文件与产物元数据",
    )
    op.create_index("ix_artifacts_0", "artifacts", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_artifacts_1",
        "artifacts",
        ["channel_id", "environment", "state", "upload_expires_at"],
        unique=False,
    )
    op.create_table(
        "attempts",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="运行标识"),
        sa.Column("step_id", sa.String(length=64), nullable=True, comment="步骤标识"),
        sa.Column("kind", sa.String(length=32), nullable=True, comment="模型或工具类别"),
        sa.Column("target_version_id", sa.String(length=64), nullable=True, comment="实际依赖版本"),
        sa.Column(
            "provider_credential_id", sa.String(length=64), nullable=True, comment="实际供应商凭据"
        ),
        sa.Column("source_request_id", sa.String(length=256), nullable=True, comment="源请求标识"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="尝试状态"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True, comment="开始时间"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="结束时间"),
        sa.Column("error", postgresql.JSONB(), nullable=True, comment="脱敏错误"),
        sa.Column("usage_id", sa.String(length=64), nullable=True, comment="用量账本引用"),
        sa.Column("lease_version", sa.BigInteger(), nullable=True, comment="调用所属租约代次"),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), nullable=True, comment="外部发送意图登记时间"
        ),
        sa.Column("retryable", sa.Boolean(), nullable=True, comment="明确失败是否允许有限重试"),
        comment="外部实际尝试",
    )
    op.create_index("ix_attempts_0", "attempts", ["channel_id", "id"], unique=False)
    op.create_index("ix_attempts_1", "attempts", ["channel_id", "run_id"], unique=False)
    op.create_index(
        "ix_attempts_2", "attempts", ["channel_id", "step_id", "created_at"], unique=False
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("actor_id", sa.String(length=128), nullable=True, comment="操作主体标识"),
        sa.Column("action", sa.String(length=128), nullable=True, comment="操作名称"),
        sa.Column("target_type", sa.String(length=64), nullable=True, comment="对象类型"),
        sa.Column("target_id", sa.String(length=128), nullable=True, comment="对象标识"),
        sa.Column("request_id", sa.String(length=64), nullable=True, comment="请求标识"),
        sa.Column("outcome", sa.String(length=32), nullable=True, comment="操作结果"),
        sa.Column("summary", postgresql.JSONB(), nullable=True, comment="脱敏变更摘要"),
        comment="操作审计元数据",
    )
    op.create_index("ix_audit_events_0", "audit_events", ["channel_id", "id"], unique=False)
    op.create_index("ix_audit_events_1", "audit_events", ["channel_id", "created_at"], unique=False)
    op.create_index(
        "ix_audit_events_2",
        "audit_events",
        ["channel_id", "target_type", "target_id"],
        unique=False,
    )
    op.create_table(
        "automation_batches",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="批次名称"),
        sa.Column("request_digest", sa.String(length=64), nullable=True, comment="受理请求摘要"),
        sa.Column("item_ids", postgresql.JSONB(), nullable=True, comment="批次条目关联"),
        sa.Column("owner_key", sa.String(length=64), nullable=True, comment="执行身份摘要"),
        sa.Column("identity", postgresql.JSONB(), nullable=True, comment="原执行身份快照"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="当前处理状态"),
        comment="批量运行受理批次",
    )
    op.create_index(
        "ix_automation_batches_0", "automation_batches", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_automation_batches_1",
        "automation_batches",
        ["channel_id", "environment", "data_scope_id", "subject_type", "subject_id"],
        unique=False,
    )
    op.create_table(
        "automation_items",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("batch_id", sa.String(length=64), nullable=True, comment="来源批次标识"),
        sa.Column("schedule_id", sa.String(length=64), nullable=True, comment="来源计划标识"),
        sa.Column("event_id", sa.String(length=128), nullable=True, comment="外部事件或窗口编号"),
        sa.Column("request_digest", sa.String(length=64), nullable=True, comment="条目语义摘要"),
        sa.Column("request", postgresql.JSONB(), nullable=True, comment="受理运行输入"),
        sa.Column("owner_key", sa.String(length=64), nullable=True, comment="执行身份摘要"),
        sa.Column("identity", postgresql.JSONB(), nullable=True, comment="原执行身份快照"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="当前处理状态"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True, comment="派发租约到期"),
        sa.Column("lease_nonce", sa.String(length=64), nullable=True, comment="派发租约凭据"),
        sa.Column("attempts", sa.BigInteger(), nullable=True, comment="受理尝试次数"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="关联运行标识"),
        sa.Column("error", postgresql.JSONB(), nullable=True, comment="最近受理错误"),
        comment="逐项运行派发记录",
    )
    op.create_index("ix_automation_items_0", "automation_items", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_automation_items_1",
        "automation_items",
        ["channel_id", "environment", "data_scope_id", "subject_type", "subject_id"],
        unique=False,
    )
    op.create_index(
        "ix_automation_items_2",
        "automation_items",
        ["channel_id", "state", "lease_until"],
        unique=False,
    )
    op.create_table(
        "automation_schedules",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="计划名称"),
        sa.Column("spec", postgresql.JSONB(), nullable=True, comment="周期与运行输入"),
        sa.Column("owner_key", sa.String(length=64), nullable=True, comment="执行身份摘要"),
        sa.Column("identity", postgresql.JSONB(), nullable=True, comment="原执行身份快照"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="当前处理状态"),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=True, comment="下次触发时间"),
        sa.Column("last_error", postgresql.JSONB(), nullable=True, comment="最近派发错误"),
        comment="通用运行定时计划",
    )
    op.create_index(
        "ix_automation_schedules_0", "automation_schedules", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_automation_schedules_1",
        "automation_schedules",
        ["channel_id", "environment", "data_scope_id", "subject_type", "subject_id"],
        unique=False,
    )
    op.create_index(
        "ix_automation_schedules_2",
        "automation_schedules",
        ["channel_id", "state", "next_at"],
        unique=False,
    )
    op.create_table(
        "budget_alerts",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("rule_id", sa.String(length=64), nullable=True, comment="规则标识"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True, comment="周期开始"),
        sa.Column(
            "threshold", sa.Numeric(precision=12, scale=6), nullable=True, comment="触发阈值"
        ),
        sa.Column("scope_key", sa.String(length=64), nullable=True, comment="预算范围摘要"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="提醒状态"),
        sa.Column(
            "first_triggered_at", sa.DateTime(timezone=True), nullable=True, comment="首次触发时间"
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True, comment="解除时间"),
        sa.Column("transitions", postgresql.JSONB(), nullable=True, comment="解除及再次触发轨迹"),
        comment="预算阈值提醒",
    )
    op.create_index("ix_budget_alerts_0", "budget_alerts", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_budget_alerts_1",
        "budget_alerts",
        ["channel_id", "rule_id", "period_start", "threshold", "scope_key"],
        unique=False,
    )
    op.create_table(
        "budget_policies",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("scope_type", sa.String(length=64), nullable=True, comment="预算对象类型"),
        sa.Column("scope_id", sa.String(length=128), nullable=True, comment="预算对象标识"),
        sa.Column("period", sa.String(length=32), nullable=True, comment="预算周期"),
        sa.Column("timezone", sa.String(length=64), nullable=True, comment="业务时区"),
        sa.Column("currency", sa.String(length=3), nullable=True, comment="币种"),
        sa.Column("limit_value", sa.Numeric(precision=24, scale=8), nullable=True, comment="限额"),
        sa.Column("unit", sa.String(length=32), nullable=True, comment="金额或数量单位"),
        sa.Column("mode", sa.String(length=32), nullable=True, comment="控制模式"),
        sa.Column("thresholds", postgresql.JSONB(), nullable=True, comment="提醒阈值"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="策略状态"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="预算名称"),
        sa.Column("version_id", sa.String(length=64), nullable=True, comment="当前不可变策略版本"),
        comment="预算策略",
    )
    op.create_index("ix_budget_policies_0", "budget_policies", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_budget_policies_1",
        "budget_policies",
        ["channel_id", "scope_type", "scope_id"],
        unique=False,
    )
    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("policy_id", sa.String(length=64), nullable=True, comment="预算策略标识"),
        sa.Column("policy_revision", sa.BigInteger(), nullable=True, comment="策略修订"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True, comment="周期开始"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="运行标识"),
        sa.Column("attempt_id", sa.String(length=64), nullable=True, comment="实际尝试标识"),
        sa.Column(
            "reserved_amount",
            sa.Numeric(precision=24, scale=8),
            nullable=True,
            comment="预占数量或金额",
        ),
        sa.Column(
            "settled_amount",
            sa.Numeric(precision=24, scale=8),
            nullable=True,
            comment="结算数量或金额",
        ),
        sa.Column("currency", sa.String(length=3), nullable=True, comment="币种"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="预占状态"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, comment="待核查时间"),
        sa.Column(
            "policy_version_id",
            sa.String(length=64),
            nullable=True,
            comment="预占时固定的预算策略版本",
        ),
        sa.Column("unit", sa.String(length=32), nullable=True, comment="占用计量单位"),
        sa.Column(
            "scope_snapshot", postgresql.JSONB(), nullable=True, comment="预算命中对象与周期快照"
        ),
        comment="预算尝试预占",
    )
    op.create_index(
        "ix_budget_reservations_0", "budget_reservations", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_budget_reservations_1",
        "budget_reservations",
        ["channel_id", "policy_id", "period_start"],
        unique=False,
    )
    op.create_index(
        "ix_budget_reservations_2",
        "budget_reservations",
        ["channel_id", "attempt_id"],
        unique=False,
    )
    op.create_table(
        "builtin_roles",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("role_code", sa.String(length=64), nullable=True, comment="角色编码"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="角色名称"),
        sa.Column("allowed_actions", postgresql.JSONB(), nullable=True, comment="允许动作"),
        sa.Column("grant_scope", sa.String(length=32), nullable=True, comment="授权类别"),
        comment="内置角色",
    )
    op.create_index("ix_builtin_roles_0", "builtin_roles", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_builtin_roles_1", "builtin_roles", ["channel_id", "role_code"], unique=False
    )
    op.create_table(
        "channel_code_index",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("channel_code", sa.String(length=64), nullable=True, comment="规范化渠道编码"),
        sa.Column(
            "target_channel_id", sa.String(length=64), nullable=True, comment="实际业务渠道标识"
        ),
        comment="系统渠道的渠道编码定位索引",
    )
    op.create_index(
        "ix_channel_code_index_0", "channel_code_index", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_channel_code_index_1",
        "channel_code_index",
        ["channel_id", "channel_code"],
        unique=False,
    )
    op.create_table(
        "channel_environments",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="环境名称"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="环境状态"),
        sa.Column("release_policy", postgresql.JSONB(), nullable=True, comment="发布策略"),
        sa.Column("retention_policy", postgresql.JSONB(), nullable=True, comment="保存策略"),
        comment="渠道环境",
    )
    op.create_index(
        "ix_channel_environments_0", "channel_environments", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_channel_environments_1",
        "channel_environments",
        ["channel_id", "environment"],
        unique=False,
    )
    op.create_table(
        "channel_keys",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="密钥名称"),
        sa.Column("client_id", sa.String(length=64), nullable=True, comment="接入服务标识"),
        sa.Column("secret_digest", sa.String(length=64), nullable=True, comment="不可逆密钥摘要"),
        sa.Column("prefix", sa.String(length=16), nullable=True, comment="辨认前缀"),
        sa.Column("suffix", sa.String(length=8), nullable=True, comment="辨认末尾"),
        sa.Column("scopes", postgresql.JSONB(), nullable=True, comment="权限上限"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="密钥状态"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, comment="失效时间"),
        sa.Column(
            "last_used_at", sa.DateTime(timezone=True), nullable=True, comment="最近使用时间"
        ),
        comment="渠道接入密钥",
    )
    op.create_index("ix_channel_keys_0", "channel_keys", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_channel_keys_1", "channel_keys", ["channel_id", "client_id", "status"], unique=False
    )
    op.create_table(
        "channel_lifecycle_events",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("event_type", sa.String(length=64), nullable=True, comment="生命周期事件类型"),
        sa.Column("target_type", sa.String(length=64), nullable=True, comment="变更对象类型"),
        sa.Column("target_id", sa.String(length=64), nullable=True, comment="变更对象标识"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="受影响环境"),
        sa.Column("payload", postgresql.JSONB(), nullable=True, comment="变更事实与原始归属"),
        sa.Column(
            "acknowledgements", postgresql.JSONB(), nullable=True, comment="已处理模块及时间"
        ),
        comment="渠道生命周期交接事件",
    )
    op.create_index(
        "ix_channel_lifecycle_events_0",
        "channel_lifecycle_events",
        ["channel_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_channel_lifecycle_events_1",
        "channel_lifecycle_events",
        ["channel_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "channel_memberships",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("user_id", sa.String(length=64), nullable=True, comment="平台账号标识"),
        sa.Column("roles", postgresql.JSONB(), nullable=True, comment="角色清单"),
        sa.Column("environments", postgresql.JSONB(), nullable=True, comment="授权环境"),
        sa.Column("data_scopes", postgresql.JSONB(), nullable=True, comment="授权数据域"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="成员状态"),
        sa.Column("granted_by", sa.String(length=128), nullable=True, comment="授权人标识"),
        comment="渠道成员关系",
    )
    op.create_index(
        "ix_channel_memberships_0", "channel_memberships", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_channel_memberships_1", "channel_memberships", ["channel_id", "user_id"], unique=False
    )
    op.create_table(
        "channels",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("channel_code", sa.String(length=64), nullable=True, comment="渠道稳定编码"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="渠道名称"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="渠道状态"),
        sa.Column("owner", sa.String(length=128), nullable=True, comment="负责人名称"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True, comment="归档时间"),
        sa.Column("retention_policy", postgresql.JSONB(), nullable=True, comment="保存策略"),
        sa.Column("budget_policy_refs", postgresql.JSONB(), nullable=True, comment="预算策略引用"),
        sa.Column(
            "rate_limit_policy_refs", postgresql.JSONB(), nullable=True, comment="限流策略引用"
        ),
        sa.Column(
            "business_type",
            sa.String(length=32),
            nullable=True,
            comment="可选业务分类展示文本，历史分类原值保留",
        ),
        comment="渠道主档",
    )
    op.create_index("ix_channels_0", "channels", ["channel_id", "id"], unique=False)
    op.create_index("ix_channels_1", "channels", ["channel_id", "channel_code"], unique=False)
    op.create_table(
        "checkpoints",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="运行标识"),
        sa.Column("namespace", sa.String(length=128), nullable=True, comment="流程命名空间"),
        sa.Column("checkpoint_key", sa.String(length=128), nullable=True, comment="恢复点逻辑标识"),
        sa.Column("parent_key", sa.String(length=128), nullable=True, comment="父恢复点"),
        sa.Column("lease_version", sa.BigInteger(), nullable=True, comment="提交租约代次"),
        sa.Column(
            "release_snapshot_id", sa.String(length=64), nullable=True, comment="固定依赖快照"
        ),
        sa.Column("state_ref", sa.String(length=64), nullable=True, comment="状态内容引用"),
        sa.Column("metadata", postgresql.JSONB(), nullable=True, comment="无原文恢复元数据"),
        comment="自有流程恢复点",
    )
    op.create_index("ix_checkpoints_0", "checkpoints", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_checkpoints_1",
        "checkpoints",
        ["channel_id", "run_id", "namespace", "checkpoint_key"],
        unique=False,
    )
    op.create_table(
        "context_snapshots",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="运行标识"),
        sa.Column(
            "included_message_ids", postgresql.JSONB(), nullable=True, comment="实际包含消息"
        ),
        sa.Column("summary_version", sa.String(length=64), nullable=True, comment="摘要版本"),
        sa.Column("memory_refs", postgresql.JSONB(), nullable=True, comment="记忆具体版本"),
        sa.Column("truncation", postgresql.JSONB(), nullable=True, comment="删减原因及范围"),
        sa.Column("conversation_id", sa.String(length=64), nullable=True, comment="所属会话"),
        sa.Column("summary_id", sa.String(length=64), nullable=True, comment="引用摘要标识"),
        sa.Column(
            "summary_source_ids", postgresql.JSONB(), nullable=True, comment="摘要来源消息集合"
        ),
        sa.Column(
            "policy_version", sa.String(length=32), nullable=True, comment="上下文选择策略版本"
        ),
        sa.Column(
            "required_characters",
            sa.BigInteger(),
            nullable=True,
            comment="必要指令和当前任务字符数",
        ),
        comment="实际模型上下文快照",
    )
    op.create_index(
        "ix_context_snapshots_0", "context_snapshots", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_context_snapshots_1", "context_snapshots", ["channel_id", "run_id"], unique=False
    )
    op.create_table(
        "conversation_summaries",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("conversation_id", sa.String(length=64), nullable=True, comment="会话标识"),
        sa.Column("source_message_ids", postgresql.JSONB(), nullable=True, comment="来源消息集合"),
        sa.Column("version", sa.BigInteger(), nullable=True, comment="摘要版本"),
        sa.Column("content", sa.Text(), nullable=True, comment="摘要内容"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="摘要有效状态"),
        sa.Column("truncation", postgresql.JSONB(), nullable=True, comment="摘要删减记录"),
        sa.Column(
            "generation_run_id", sa.String(length=64), nullable=True, comment="受控摘要生成运行"
        ),
        comment="可溯源会话摘要",
    )
    op.create_index(
        "ix_conversation_summaries_0", "conversation_summaries", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_conversation_summaries_1",
        "conversation_summaries",
        ["channel_id", "conversation_id", "version"],
        unique=False,
    )
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("conversation_id", sa.String(length=64), nullable=True, comment="会话标识"),
        sa.Column(
            "client_message_id", sa.String(length=128), nullable=True, comment="客户端消息标识"
        ),
        sa.Column("request_digest", sa.String(length=64), nullable=True, comment="语义请求摘要"),
        sa.Column("user_message_id", sa.String(length=64), nullable=True, comment="用户消息标识"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="运行标识"),
        sa.Column("sequence", sa.BigInteger(), nullable=True, comment="会话内顺序"),
        sa.Column(
            "assistant_message_id", sa.String(length=64), nullable=True, comment="助手消息标识"
        ),
        sa.Column(
            "agent_version_id", sa.String(length=64), nullable=True, comment="本轮冻结智能体版本"
        ),
        sa.Column("version_label", sa.String(length=128), nullable=True, comment="本轮版本名称"),
        sa.Column("input", postgresql.JSONB(), nullable=True, comment="不可变业务输入"),
        sa.Column("input_schema", postgresql.JSONB(), nullable=True, comment="本轮输入契约"),
        sa.Column("output_schema", postgresql.JSONB(), nullable=True, comment="本轮输出契约"),
        sa.Column("source_run_id", sa.String(length=64), nullable=True, comment="普通追问来源运行"),
        sa.Column(
            "confirmed_conditions", postgresql.JSONB(), nullable=True, comment="上一轮已确认条件"
        ),
        comment="会话轮次与消息幂等",
    )
    op.create_index(
        "ix_conversation_turns_0", "conversation_turns", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_conversation_turns_1",
        "conversation_turns",
        ["channel_id", "conversation_id", "client_message_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_turns_2",
        "conversation_turns",
        ["channel_id", "conversation_id", "sequence"],
        unique=False,
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("agent_id", sa.String(length=64), nullable=True, comment="智能体标识"),
        sa.Column("title", sa.String(length=255), nullable=True, comment="会话标题"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="会话状态"),
        sa.Column("active_run_id", sa.String(length=64), nullable=True, comment="当前生成运行"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, comment="保留到期时间"),
        sa.Column("agent_code", sa.String(length=128), nullable=True, comment="智能体调用编码"),
        sa.Column("agent_name", sa.String(length=128), nullable=True, comment="智能体名称"),
        sa.Column("subject_name", sa.String(length=128), nullable=True, comment="主体名称"),
        sa.Column("input_schema", postgresql.JSONB(), nullable=True, comment="已接受的输入契约"),
        sa.Column("next_sequence", sa.BigInteger(), nullable=True, comment="下一条消息顺序"),
        sa.Column("next_turn_sequence", sa.BigInteger(), nullable=True, comment="下一轮顺序"),
        comment="业务会话",
    )
    op.create_index("ix_conversations_0", "conversations", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_conversations_1",
        "conversations",
        ["channel_id", "environment", "data_scope_id", "subject_type", "subject_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_conversations_2",
        "conversations",
        ["channel_id", "environment", "data_scope_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "credentials",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("purpose", sa.String(length=32), nullable=True, comment="凭据用途"),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True, comment="认证加密密文"),
        sa.Column("key_version", sa.String(length=64), nullable=True, comment="加密密钥版本"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="凭据状态"),
        comment="加密服务凭据",
    )
    op.create_index("ix_credentials_0", "credentials", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_credentials_1",
        "credentials",
        ["channel_id", "environment", "purpose", "state"],
        unique=False,
    )
    op.create_table(
        "custom_roles",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="角色名称"),
        sa.Column("allowed_actions", postgresql.JSONB(), nullable=True, comment="角色动作上限"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="启停状态"),
        comment="渠道自定义角色",
    )
    op.create_index("ix_custom_roles_0", "custom_roles", ["channel_id", "id"], unique=False)
    op.create_table(
        "data_scopes",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="数据域名称"),
        sa.Column(
            "external_scope_type",
            sa.String(length=64),
            nullable=True,
            comment="显式配置的外部数据域类型",
        ),
        sa.Column(
            "external_scope_id",
            sa.String(length=128),
            nullable=True,
            comment="显式配置的外部数据域编号",
        ),
        sa.Column("status", sa.String(length=32), nullable=True, comment="数据域状态"),
        comment="业务数据域映射",
    )
    op.create_index("ix_data_scopes_0", "data_scopes", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_data_scopes_1",
        "data_scopes",
        ["channel_id", "environment", "external_scope_type", "external_scope_id"],
        unique=False,
    )
    op.create_table(
        "delegation_keys",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("client_id", sa.String(length=64), nullable=True, comment="接入服务标识"),
        sa.Column("key_reference", sa.String(length=64), nullable=True, comment="独立签名密文引用"),
        sa.Column("algorithm", sa.String(length=32), nullable=True, comment="固定签名算法"),
        sa.Column("issuer", sa.String(length=128), nullable=True, comment="可信签发者"),
        sa.Column("audience", sa.String(length=128), nullable=True, comment="委托受众"),
        sa.Column("max_ttl_seconds", sa.Integer(), nullable=True, comment="最长委托有效秒数"),
        sa.Column("clock_skew_seconds", sa.Integer(), nullable=True, comment="允许时钟偏差秒数"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="密钥状态"),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True, comment="密钥生效时间"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, comment="密钥到期时间"),
        sa.Column("rotated_from", sa.String(length=64), nullable=True, comment="轮换前密钥编号"),
        comment="委托签名验证密钥",
    )
    op.create_index("ix_delegation_keys_0", "delegation_keys", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_delegation_keys_1",
        "delegation_keys",
        ["channel_id", "environment", "client_id"],
        unique=False,
    )
    op.create_table(
        "delegation_nonces",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("client_id", sa.String(length=64), nullable=True, comment="稳定接入服务标识"),
        sa.Column("kid", sa.String(length=64), nullable=True, comment="验签密钥编号"),
        sa.Column("nonce_digest", sa.String(length=64), nullable=True, comment="随机数摘要"),
        sa.Column(
            "request_digest", sa.String(length=64), nullable=True, comment="实际请求绑定摘要"
        ),
        sa.Column("claims_digest", sa.String(length=64), nullable=True, comment="完整委托声明摘要"),
        sa.Column("claims", postgresql.JSONB(), nullable=True, comment="验签后权限声明"),
        sa.Column(
            "resolved_scope", postgresql.JSONB(), nullable=True, comment="验签后渠道数据域主体"
        ),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=True, comment="防重放声明有效时间"
        ),
        sa.Column(
            "retain_until",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="防重放记录最早清理时间",
        ),
        comment="已验证业务委托与请求防重放",
    )
    op.create_index(
        "ix_delegation_nonces_0", "delegation_nonces", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_delegation_nonces_1",
        "delegation_nonces",
        ["channel_id", "environment", "client_id", "nonce_digest"],
        unique=False,
    )
    op.create_index(
        "ix_delegation_nonces_2",
        "delegation_nonces",
        ["channel_id", "environment", "retain_until"],
        unique=False,
    )
    op.create_table(
        "deletion_jobs",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("marker_id", sa.String(length=64), nullable=True, comment="删除标记"),
        sa.Column(
            "scope_description", postgresql.JSONB(), nullable=True, comment="待清理范围元数据"
        ),
        sa.Column("affected_resources", postgresql.JSONB(), nullable=True, comment="影响引用清单"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="清理状态"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True, comment="完成时间"),
        sa.Column("conversation_id", sa.String(length=64), nullable=True, comment="删除目标会话"),
        comment="删除传播任务",
    )
    op.create_index("ix_deletion_jobs_0", "deletion_jobs", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_deletion_jobs_1", "deletion_jobs", ["channel_id", "state", "created_at"], unique=False
    )
    op.create_index(
        "ix_deletion_jobs_2", "deletion_jobs", ["channel_id", "conversation_id"], unique=False
    )
    op.create_table(
        "deletion_markers",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("target_type", sa.String(length=64), nullable=True, comment="删除对象类型"),
        sa.Column("target_id", sa.String(length=128), nullable=True, comment="删除对象标识"),
        sa.Column("reason_code", sa.String(length=64), nullable=True, comment="删除原因类别"),
        sa.Column("requested_by", sa.String(length=128), nullable=True, comment="删除申请主体"),
        comment="不可恢复使用的删除标记",
    )
    op.create_index("ix_deletion_markers_0", "deletion_markers", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_deletion_markers_1",
        "deletion_markers",
        ["channel_id", "environment", "target_type", "target_id"],
        unique=False,
    )
    op.create_table(
        "deletion_receipts",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("job_id", sa.String(length=64), nullable=True, comment="删除任务"),
        sa.Column("marker_digest", sa.String(length=64), nullable=True, comment="删除清单摘要"),
        sa.Column("counts", postgresql.JSONB(), nullable=True, comment="各类已完成数量"),
        sa.Column("proof_digest", sa.String(length=64), nullable=True, comment="完成证明摘要"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True, comment="完成时间"),
        comment="删除清理完成证明",
    )
    op.create_index(
        "ix_deletion_receipts_0", "deletion_receipts", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_deletion_receipts_1", "deletion_receipts", ["channel_id", "job_id"], unique=False
    )
    op.create_table(
        "deletion_work_items",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("job_id", sa.String(length=64), nullable=True, comment="删除任务"),
        sa.Column("handler_key", sa.String(length=128), nullable=True, comment="登记处理器"),
        sa.Column("target_type", sa.String(length=64), nullable=True, comment="对象类型"),
        sa.Column("target_id", sa.String(length=128), nullable=True, comment="对象标识"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="步骤状态"),
        sa.Column("attempts", sa.Integer(), nullable=True, comment="尝试次数"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True, comment="重试时间"),
        sa.Column("last_error", sa.String(length=64), nullable=True, comment="脱敏错误类别"),
        sa.Column("lease_token", sa.String(length=64), nullable=True, comment="执行租约令牌"),
        sa.Column(
            "lease_until", sa.DateTime(timezone=True), nullable=True, comment="执行租约到期时间"
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True, comment="完成时间"),
        comment="可重试模块清理步骤",
    )
    op.create_index(
        "ix_deletion_work_items_0", "deletion_work_items", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_deletion_work_items_1",
        "deletion_work_items",
        ["channel_id", "job_id", "handler_key", "target_id"],
        unique=False,
    )
    op.create_index(
        "ix_deletion_work_items_2",
        "deletion_work_items",
        ["channel_id", "state", "next_attempt_at"],
        unique=False,
    )
    op.create_table(
        "dispatch_outbox",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="运行标识"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="投递状态"),
        sa.Column("dispatch_attempts", sa.Integer(), nullable=True, comment="投递次数"),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), nullable=True, comment="下次补偿时间"
        ),
        sa.Column("last_error", sa.String(length=64), nullable=True, comment="脱敏错误类别"),
        sa.Column("delivery_version", sa.BigInteger(), nullable=True, comment="本次投递声明代次"),
        comment="可靠调度投递意图",
    )
    op.create_index("ix_dispatch_outbox_0", "dispatch_outbox", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_dispatch_outbox_1",
        "dispatch_outbox",
        ["channel_id", "state", "next_attempt_at"],
        unique=False,
    )
    op.create_table(
        "evaluation_cases",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("dataset_id", sa.String(length=64), nullable=True, comment="所属样本集"),
        sa.Column("case_key", sa.String(length=128), nullable=True, comment="跨版本样本定位键"),
        sa.Column("title", sa.String(length=255), nullable=True, comment="样本标题"),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=True, comment="输入、断言、标签、人工结论和来源"
        ),
        sa.Column("fixture_id", sa.String(length=64), nullable=True, comment="固定工具数据引用"),
        sa.Column(
            "previous_case_id", sa.String(length=64), nullable=True, comment="修改前样本引用"
        ),
        sa.Column("invalidated", sa.Boolean(), nullable=True, comment="来源已失效"),
        comment="不可变评测样本",
    )
    op.create_index("ix_evaluation_cases_0", "evaluation_cases", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_evaluation_cases_1", "evaluation_cases", ["channel_id", "dataset_id"], unique=False
    )
    op.create_table(
        "evaluation_dataset_versions",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("dataset_id", sa.String(length=64), nullable=True, comment="所属样本集"),
        sa.Column("version_label", sa.String(length=128), nullable=True, comment="版本名称"),
        sa.Column("content_digest", sa.String(length=64), nullable=True, comment="数据和标签摘要"),
        sa.Column("case_ids", postgresql.JSONB(), nullable=True, comment="固定样本清单"),
        sa.Column("reference_versions", postgresql.JSONB(), nullable=True, comment="参考资料版本"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True, comment="固定数据时间"),
        sa.Column(
            "reference_digests", postgresql.JSONB(), nullable=True, comment="参考资料版本内容摘要"
        ),
        comment="不可变评测样本及标签版本",
    )
    op.create_index(
        "ix_evaluation_dataset_versions_0",
        "evaluation_dataset_versions",
        ["channel_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_evaluation_dataset_versions_1",
        "evaluation_dataset_versions",
        ["channel_id", "dataset_id"],
        unique=False,
    )
    op.create_table(
        "evaluation_datasets",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="样本集名称"),
        sa.Column("scenario", sa.String(length=64), nullable=True, comment="适用能力类别"),
        sa.Column("owner", sa.String(length=128), nullable=True, comment="负责人"),
        sa.Column("applicability", sa.Text(), nullable=True, comment="适用范围"),
        sa.Column(
            "current_version_id", sa.String(length=64), nullable=True, comment="当前样本版本"
        ),
        comment="评测样本集",
    )
    op.create_index(
        "ix_evaluation_datasets_0", "evaluation_datasets", ["channel_id", "id"], unique=False
    )
    op.create_table(
        "evaluation_fixtures",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=True, comment="按工具版本及参数匹配的固定结果"
        ),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True, comment="夹具采集时间"),
        sa.Column("invalidated", sa.Boolean(), nullable=True, comment="夹具来源已失效"),
        comment="评测工具夹具",
    )
    op.create_index(
        "ix_evaluation_fixtures_0", "evaluation_fixtures", ["channel_id", "id"], unique=False
    )
    op.create_table(
        "evaluation_reports",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("evaluation_id", sa.String(length=64), nullable=True, comment="所属评测"),
        sa.Column("report_digest", sa.String(length=64), nullable=True, comment="报告证据摘要"),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=True, comment="覆盖、差异、阻断、用量与耗时"
        ),
        sa.Column("reproducible", sa.Boolean(), nullable=True, comment="来源和固定数据可复现"),
        comment="可追溯评测报告",
    )
    op.create_index(
        "ix_evaluation_reports_0", "evaluation_reports", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_evaluation_reports_1",
        "evaluation_reports",
        ["channel_id", "evaluation_id"],
        unique=False,
    )
    op.create_table(
        "evaluation_results",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("evaluation_id", sa.String(length=64), nullable=True, comment="所属评测"),
        sa.Column("case_id", sa.String(length=64), nullable=True, comment="固定样本"),
        sa.Column("candidate_id", sa.String(length=64), nullable=True, comment="冻结候选"),
        sa.Column("attempt_number", sa.Integer(), nullable=True, comment="样本重跑序号"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="统一运行"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="单例状态"),
        sa.Column("judgment", postgresql.JSONB(), nullable=True, comment="确定性与语义判定"),
        sa.Column("human_label", postgresql.JSONB(), nullable=True, comment="独立人工结论"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True, comment="派发占位时间"),
        comment="评测单例及重跑记录",
    )
    op.create_index(
        "ix_evaluation_results_0", "evaluation_results", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_evaluation_results_1",
        "evaluation_results",
        ["channel_id", "evaluation_id"],
        unique=False,
    )
    op.create_index(
        "ix_evaluation_results_2", "evaluation_results", ["channel_id", "run_id"], unique=False
    )
    op.create_table(
        "evaluations",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="任务名称"),
        sa.Column(
            "dataset_version_id", sa.String(length=64), nullable=True, comment="固定样本版本"
        ),
        sa.Column(
            "dataset_digest", sa.String(length=64), nullable=True, comment="固定数据和标签摘要"
        ),
        sa.Column(
            "candidate_snapshots",
            postgresql.JSONB(),
            nullable=True,
            comment="冻结候选及完整依赖清单",
        ),
        sa.Column(
            "baseline_evaluation_id", sa.String(length=64), nullable=True, comment="历史基线评测"
        ),
        sa.Column("baseline_candidate_id", sa.String(length=64), nullable=True, comment="基线候选"),
        sa.Column(
            "execution_mode", sa.String(length=32), nullable=True, comment="工具数据执行模式"
        ),
        sa.Column(
            "config", postgresql.JSONB(), nullable=True, comment="并发、预算、阈值和发布评测配置"
        ),
        sa.Column("identity", postgresql.JSONB(), nullable=True, comment="原始调用身份"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="调度状态"),
        sa.Column("human_review", postgresql.JSONB(), nullable=True, comment="独立报告人工审阅"),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=True, comment="发布证据有效期"
        ),
        comment="批量评测调度任务",
    )
    op.create_index("ix_evaluations_0", "evaluations", ["channel_id", "id"], unique=False)
    op.create_index("ix_evaluations_1", "evaluations", ["channel_id", "state"], unique=False)
    op.create_table(
        "evidence_refs",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("source_type", sa.String(length=64), nullable=True, comment="来源类型"),
        sa.Column("source_id", sa.String(length=128), nullable=True, comment="来源标识"),
        sa.Column("source_version", sa.String(length=128), nullable=True, comment="来源版本"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True, comment="观测时间"),
        sa.Column("location", postgresql.JSONB(), nullable=True, comment="字段路径或文本位置"),
        sa.Column("title", sa.String(length=255), nullable=True, comment="授权范围内的来源名称"),
        sa.Column("artifact_id", sa.String(length=64), nullable=True, comment="内容产物标识"),
        sa.Column("authorization_scope", postgresql.JSONB(), nullable=True, comment="授权范围摘要"),
        comment="证据定位与授权范围",
    )
    op.create_index("ix_evidence_refs_0", "evidence_refs", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_evidence_refs_1",
        "evidence_refs",
        ["channel_id", "source_type", "source_id"],
        unique=False,
    )
    op.create_table(
        "iam_revocations",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("kind", sa.String(length=32), nullable=True, comment="撤销索引类别"),
        sa.Column(
            "target_id", sa.String(length=128), nullable=True, comment="撤销对象标识或令牌摘要"
        ),
        sa.Column(
            "cutoff_at", sa.DateTime(timezone=True), nullable=True, comment="撤销签发时间上界"
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), nullable=True, comment="缓存补偿完成时间"
        ),
        comment="认证撤销补偿记录",
    )
    op.create_index("ix_iam_revocations_0", "iam_revocations", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_iam_revocations_1", "iam_revocations", ["channel_id", "kind", "target_id"], unique=False
    )
    op.create_index("ix_iam_revocations_2", "iam_revocations", ["completed_at"], unique=False)
    op.create_table(
        "integration_tests",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="所属业务数据域"),
        sa.Column("integration_id", sa.String(length=64), nullable=True, comment="连接标识"),
        sa.Column("config_revision", sa.BigInteger(), nullable=True, comment="测试对应配置修订"),
        sa.Column("cases", postgresql.JSONB(), nullable=True, comment="验证能力清单"),
        sa.Column("results", postgresql.JSONB(), nullable=True, comment="脱敏验证结论"),
        sa.Column("capabilities", postgresql.JSONB(), nullable=True, comment="通过验证的能力"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="验证状态"),
        comment="接入契约验证",
    )
    op.create_index(
        "ix_integration_tests_0", "integration_tests", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_integration_tests_1",
        "integration_tests",
        ["channel_id", "environment", "data_scope_id", "integration_id"],
        unique=False,
    )
    op.create_table(
        "integrations",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="所属业务数据域"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="接入名称"),
        sa.Column("adapter_code", sa.String(length=64), nullable=True, comment="适配器编码"),
        sa.Column("adapter_version", sa.String(length=64), nullable=True, comment="适配器版本"),
        sa.Column("business_endpoint", sa.String(length=2048), nullable=True, comment="源服务地址"),
        sa.Column("credential_ref", sa.String(length=64), nullable=True, comment="服务凭据引用"),
        sa.Column("allowed_operations", postgresql.JSONB(), nullable=True, comment="已授权能力"),
        sa.Column("operation_paths", postgresql.JSONB(), nullable=True, comment="固定能力接口路径"),
        sa.Column("field_mapping", postgresql.JSONB(), nullable=True, comment="源字段转换配置"),
        sa.Column(
            "scope_mapping_ref", sa.String(length=64), nullable=True, comment="渠道数据域映射引用"
        ),
        sa.Column("health", sa.String(length=32), nullable=True, comment="连接健康状态"),
        sa.Column("contract_version", sa.String(length=64), nullable=True, comment="标准契约版本"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="启用状态"),
        comment="业务系统适配连接",
    )
    op.create_index("ix_integrations_0", "integrations", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_integrations_1",
        "integrations",
        ["channel_id", "environment", "data_scope_id", "status"],
        unique=False,
    )
    op.create_table(
        "key_identity_index",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column(
            "key_lookup_digest", sa.String(length=64), nullable=True, comment="完整密钥不可逆摘要"
        ),
        sa.Column("key_id", sa.String(length=64), nullable=True, comment="目标密钥标识"),
        sa.Column("target_channel_id", sa.String(length=64), nullable=True, comment="目标渠道标识"),
        comment="系统渠道密钥身份索引",
    )
    op.create_index(
        "ix_key_identity_index_0", "key_identity_index", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_key_identity_index_1",
        "key_identity_index",
        ["channel_id", "key_lookup_digest"],
        unique=False,
    )
    op.create_table(
        "key_rotations",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("old_key_id", sa.String(length=64), nullable=True, comment="原密钥标识"),
        sa.Column("new_key_id", sa.String(length=64), nullable=True, comment="新密钥标识"),
        sa.Column(
            "overlap_until", sa.DateTime(timezone=True), nullable=True, comment="重叠截止时间"
        ),
        sa.Column("operator_id", sa.String(length=128), nullable=True, comment="操作人标识"),
        comment="密钥轮换记录",
    )
    op.create_index("ix_key_rotations_0", "key_rotations", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_key_rotations_1", "key_rotations", ["channel_id", "old_key_id"], unique=False
    )
    op.create_table(
        "mcp_checks",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("connection_id", sa.String(length=64), nullable=True, comment="连接标识"),
        sa.Column(
            "negotiated_version", sa.String(length=64), nullable=True, comment="协商协议版本"
        ),
        sa.Column("server_info", postgresql.JSONB(), nullable=True, comment="远端信息"),
        sa.Column("capabilities", postgresql.JSONB(), nullable=True, comment="协商能力"),
        sa.Column("health_status", sa.String(length=32), nullable=True, comment="健康状态"),
        sa.Column("latency_ms", sa.Integer(), nullable=True, comment="耗时毫秒"),
        sa.Column("error_category", sa.String(length=64), nullable=True, comment="错误类别"),
        sa.Column(
            "connection_revision", sa.BigInteger(), nullable=True, comment="检查时连接配置修订"
        ),
        sa.Column("operation", sa.String(length=32), nullable=True, comment="检查操作类型"),
        comment="握手与健康检查",
    )
    op.create_index("ix_mcp_checks_0", "mcp_checks", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_mcp_checks_1", "mcp_checks", ["channel_id", "connection_id", "created_at"], unique=False
    )
    op.create_table(
        "mcp_connections",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="连接名称"),
        sa.Column("transport", sa.String(length=64), nullable=True, comment="传输类型"),
        sa.Column("endpoint", sa.String(length=2048), nullable=True, comment="服务地址"),
        sa.Column("credential_ref", sa.String(length=64), nullable=True, comment="凭据引用"),
        sa.Column("timeouts", postgresql.JSONB(), nullable=True, comment="超时策略"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="启用状态"),
        sa.Column("health_status", sa.String(length=32), nullable=True, comment="健康状态"),
        sa.Column("configuration_revision", sa.BigInteger(), nullable=True, comment="连接配置修订"),
        sa.Column("credential_revision", sa.BigInteger(), nullable=True, comment="凭据版本"),
        sa.Column("tested_revision", sa.BigInteger(), nullable=True, comment="握手通过的配置修订"),
        sa.Column(
            "discovered_revision", sa.BigInteger(), nullable=True, comment="发现通过的配置修订"
        ),
        sa.Column("failure_count", sa.Integer(), nullable=True, comment="连续失败次数"),
        sa.Column("health_policy", postgresql.JSONB(), nullable=True, comment="检查频率与失败阈值"),
        sa.Column(
            "last_check_at", sa.DateTime(timezone=True), nullable=True, comment="最近检查时间"
        ),
        sa.Column("auth_failed", sa.Boolean(), nullable=True, comment="凭据失效阻断状态"),
        sa.Column(
            "health_actor_id", sa.String(length=64), nullable=True, comment="健康检查授权成员"
        ),
        sa.Column(
            "health_data_scope_id",
            sa.String(length=64),
            nullable=True,
            comment="健康检查授权数据域",
        ),
        sa.Column(
            "next_check_at", sa.DateTime(timezone=True), nullable=True, comment="下次健康检查时间"
        ),
        comment="远程工具连接",
    )
    op.create_index("ix_mcp_connections_0", "mcp_connections", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_mcp_connections_1",
        "mcp_connections",
        ["channel_id", "environment", "status"],
        unique=False,
    )
    op.create_index(
        "ix_mcp_connections_2",
        "mcp_connections",
        ["channel_id", "environment", "next_check_at"],
        unique=False,
    )
    op.create_table(
        "mcp_discoveries",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("connection_id", sa.String(length=64), nullable=True, comment="连接标识"),
        sa.Column("connection_revision", sa.BigInteger(), nullable=True, comment="连接修订"),
        sa.Column("tool_definitions", postgresql.JSONB(), nullable=True, comment="远端工具定义"),
        sa.Column("schema_hashes", postgresql.JSONB(), nullable=True, comment="定义摘要"),
        sa.Column(
            "negotiated_version", sa.String(length=64), nullable=True, comment="发现协商协议版本"
        ),
        sa.Column("credential_revision", sa.BigInteger(), nullable=True, comment="发现时凭据版本"),
        comment="远端工具发现快照",
    )
    op.create_index("ix_mcp_discoveries_0", "mcp_discoveries", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_mcp_discoveries_1",
        "mcp_discoveries",
        ["channel_id", "connection_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "mcp_imports",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("connection_id", sa.String(length=64), nullable=True, comment="连接标识"),
        sa.Column("discovery_id", sa.String(length=64), nullable=True, comment="发现快照"),
        sa.Column("remote_tool_name", sa.String(length=256), nullable=True, comment="远端名称"),
        sa.Column("local_tool_id", sa.String(length=64), nullable=True, comment="本地工具标识"),
        sa.Column("schema_hash", sa.String(length=64), nullable=True, comment="远端结构摘要"),
        sa.Column("imported_version", sa.String(length=64), nullable=True, comment="本地导入版本"),
        sa.Column("effect_type", sa.String(length=32), nullable=True, comment="管理员核定影响类型"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="本地工具显示名称"),
        sa.Column("input_schema", postgresql.JSONB(), nullable=True, comment="固定本地输入契约"),
        sa.Column(
            "contract_status", sa.String(length=32), nullable=True, comment="固定契约可用状态"
        ),
        comment="远端工具导入映射",
    )
    op.create_index("ix_mcp_imports_0", "mcp_imports", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_mcp_imports_1",
        "mcp_imports",
        ["channel_id", "connection_id", "remote_tool_name"],
        unique=False,
    )
    op.create_index(
        "ix_mcp_imports_2",
        "mcp_imports",
        ["channel_id", "environment", "connection_id", "discovery_id", "remote_tool_name"],
        unique=False,
    )
    op.create_table(
        "mcp_oauth_flows",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("connection_id", sa.String(length=64), nullable=True, comment="连接标识"),
        sa.Column("configuration_revision", sa.BigInteger(), nullable=True, comment="连接配置修订"),
        sa.Column("profile_id", sa.String(length=64), nullable=True, comment="身份提供方配置"),
        sa.Column("profile_digest", sa.String(length=64), nullable=True, comment="提供方配置摘要"),
        sa.Column("ownership", sa.String(length=16), nullable=True, comment="凭据归属类型"),
        sa.Column("owner_id", sa.String(length=64), nullable=True, comment="归属身份摘要"),
        sa.Column("state", sa.String(length=16), nullable=True, comment="授权流程状态"),
        sa.Column("verifier_ref", sa.String(length=64), nullable=True, comment="加密验证凭据引用"),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=True, comment="授权流程截止时间"
        ),
        comment="MCP 一次性授权流程",
    )
    op.create_index("ix_mcp_oauth_flows_0", "mcp_oauth_flows", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_mcp_oauth_flows_1",
        "mcp_oauth_flows",
        ["channel_id", "environment", "connection_id"],
        unique=False,
    )
    op.create_table(
        "mcp_oauth_tokens",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("connection_id", sa.String(length=64), nullable=True, comment="连接标识"),
        sa.Column("profile_id", sa.String(length=64), nullable=True, comment="身份提供方配置"),
        sa.Column("profile_digest", sa.String(length=64), nullable=True, comment="提供方配置摘要"),
        sa.Column("ownership", sa.String(length=16), nullable=True, comment="凭据归属类型"),
        sa.Column("owner_id", sa.String(length=64), nullable=True, comment="归属身份摘要"),
        sa.Column(
            "credential_ref", sa.String(length=64), nullable=True, comment="加密委托令牌引用"
        ),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=True, comment="访问令牌截止时间"
        ),
        sa.Column("state", sa.String(length=16), nullable=True, comment="委托状态"),
        sa.Column(
            "refresh_until", sa.DateTime(timezone=True), nullable=True, comment="刷新占用截止时间"
        ),
        sa.Column("refresh_nonce", sa.String(length=64), nullable=True, comment="刷新互斥代次"),
        sa.Column(
            "authorized_at", sa.DateTime(timezone=True), nullable=True, comment="授权发起时间"
        ),
        comment="MCP 分身份委托凭据",
    )
    op.create_index("ix_mcp_oauth_tokens_0", "mcp_oauth_tokens", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_mcp_oauth_tokens_1",
        "mcp_oauth_tokens",
        ["channel_id", "environment", "connection_id"],
        unique=False,
    )
    op.create_table(
        "memories",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("memory_type", sa.String(length=64), nullable=True, comment="记忆类别"),
        sa.Column("key", sa.String(length=128), nullable=True, comment="记忆属性名"),
        sa.Column("display_name", sa.String(length=128), nullable=True, comment="属性中文名"),
        sa.Column("value", postgresql.JSONB(), nullable=True, comment="记忆值"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="确认及有效状态"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, comment="有效截止时间"),
        sa.Column("current_version_id", sa.String(length=64), nullable=True, comment="当前版本"),
        sa.Column("confirmed", sa.Boolean(), nullable=True, comment="是否经过明确确认"),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), nullable=True, comment="最新有效依据时间"
        ),
        sa.Column("trust_level", sa.Integer(), nullable=True, comment="有效来源可信等级"),
        sa.Column("usage_count", sa.BigInteger(), nullable=True, comment="实际使用次数"),
        sa.Column("subject_name", sa.String(length=255), nullable=True, comment="主体可读名称"),
        sa.Column(
            "source_mode",
            sa.String(length=16),
            nullable=True,
            comment="来源有效性模式：独立依据或全部依赖",
        ),
        comment="主体结构化记忆",
    )
    op.create_index("ix_memories_0", "memories", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_memories_1",
        "memories",
        [
            "channel_id",
            "environment",
            "data_scope_id",
            "subject_type",
            "subject_id",
            "key",
            "status",
        ],
        unique=False,
    )
    op.create_table(
        "memory_consolidations",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("conversation_id", sa.String(length=128), nullable=True, comment="来源会话标识"),
        sa.Column(
            "source_run_id",
            sa.String(length=128),
            nullable=True,
            comment="恢复授权与冻结策略的来源运行",
        ),
        sa.Column(
            "source_message_ids", postgresql.JSONB(), nullable=True, comment="本批完整消息引用"
        ),
        sa.Column(
            "memory_ids", postgresql.JSONB(), nullable=True, comment="已生成的归档及画像引用"
        ),
        sa.Column(
            "generation_run_id", sa.String(length=128), nullable=True, comment="后台生成运行标识"
        ),
        sa.Column("state", sa.String(length=32), nullable=True, comment="后台整理状态"),
        sa.Column("attempt", sa.Integer(), nullable=True, comment="生成轮次"),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), nullable=True, comment="下次允许整理时间"
        ),
        sa.Column("error_code", sa.String(length=128), nullable=True, comment="最后失败原因编码"),
        sa.Column(
            "settings", postgresql.JSONB(), nullable=True, comment="冻结策略与属性，不包含会话原文"
        ),
        sa.Column("preference_revision", sa.Integer(), nullable=True, comment="受理时主体偏好修订"),
        comment="会话归档与人物画像后台整理任务",
    )
    op.create_index(
        "ix_memory_consolidations_0", "memory_consolidations", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_memory_consolidations_1",
        "memory_consolidations",
        ["channel_id", "state", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_memory_consolidations_2",
        "memory_consolidations",
        ["channel_id", "conversation_id"],
        unique=False,
    )
    op.create_table(
        "memory_deletion_jobs",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("memory_ids", postgresql.JSONB(), nullable=True, comment="待清理记忆标识集合"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="清理进度"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True, comment="完成时间"),
        sa.Column("kind", sa.String(length=16), nullable=True, comment="单项遗忘或主体清空"),
        comment="主体记忆删除清理意图",
    )
    op.create_index(
        "ix_memory_deletion_jobs_0", "memory_deletion_jobs", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_memory_deletion_jobs_1",
        "memory_deletion_jobs",
        ["channel_id", "environment", "data_scope_id", "subject_type", "subject_id", "state"],
        unique=False,
    )
    op.create_table(
        "memory_embeddings",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("memory_id", sa.String(length=64), nullable=True, comment="来源记忆标识"),
        sa.Column("memory_version_id", sa.String(length=64), nullable=True, comment="来源记忆版本"),
        sa.Column("model_version_id", sa.String(length=64), nullable=True, comment="向量模型版本"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="生成向量的运行"),
        sa.Column("dimensions", sa.Integer(), nullable=True, comment="向量维度"),
        sa.Column("embedding", postgresql.JSONB(), nullable=True, comment="记忆向量"),
        comment="按主体和模型版本隔离的记忆向量",
    )
    op.create_index(
        "ix_memory_embeddings_0", "memory_embeddings", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_memory_embeddings_1",
        "memory_embeddings",
        [
            "channel_id",
            "environment",
            "data_scope_id",
            "subject_type",
            "subject_id",
            "model_version_id",
        ],
        unique=False,
    )
    op.create_index(
        "ix_memory_embeddings_2", "memory_embeddings", ["channel_id", "memory_id"], unique=False
    )
    op.create_table(
        "memory_policies",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("agent_id", sa.String(length=64), nullable=True, comment="智能体标识"),
        sa.Column("allowed_types", postgresql.JSONB(), nullable=True, comment="允许类别"),
        sa.Column("write_mode", sa.String(length=32), nullable=True, comment="写入方式"),
        sa.Column("ttl_seconds", sa.Integer(), nullable=True, comment="保留秒数"),
        sa.Column("max_items", sa.Integer(), nullable=True, comment="存储条数上限"),
        sa.Column("retrieval_limit", sa.Integer(), nullable=True, comment="召回条数上限"),
        sa.Column("read_enabled", sa.Boolean(), nullable=True, comment="是否允许读取"),
        sa.Column("suggest_enabled", sa.Boolean(), nullable=True, comment="是否允许建议写入"),
        sa.Column("failure_mode", sa.String(length=16), nullable=True, comment="读取故障处理方式"),
        sa.Column(
            "attributes", postgresql.JSONB(), nullable=True, comment="渠道可配置的画像属性定义"
        ),
        sa.Column(
            "consolidation", postgresql.JSONB(), nullable=True, comment="后台归档与画像整理策略"
        ),
        comment="渠道与智能体记忆策略",
    )
    op.create_index("ix_memory_policies_0", "memory_policies", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_memory_policies_1", "memory_policies", ["channel_id", "agent_id"], unique=False
    )
    op.create_table(
        "memory_preferences",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("enabled", sa.Boolean(), nullable=True, comment="是否启用"),
        sa.Column("changed_by", sa.String(length=128), nullable=True, comment="变更主体"),
        comment="主体长期记忆开关",
    )
    op.create_index(
        "ix_memory_preferences_0", "memory_preferences", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_memory_preferences_1",
        "memory_preferences",
        ["channel_id", "environment", "data_scope_id", "subject_type", "subject_id"],
        unique=False,
    )
    op.create_table(
        "memory_retrievals",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="运行标识"),
        sa.Column("memory_refs", postgresql.JSONB(), nullable=True, comment="使用的记忆版本"),
        sa.Column("selection_reason", postgresql.JSONB(), nullable=True, comment="选择依据"),
        sa.Column("warnings", postgresql.JSONB(), nullable=True, comment="脱敏降级提示"),
        sa.Column("agent_id", sa.String(length=64), nullable=True, comment="使用记忆的智能体"),
        comment="运行记忆召回记录",
    )
    op.create_index(
        "ix_memory_retrievals_0", "memory_retrievals", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_memory_retrievals_1", "memory_retrievals", ["channel_id", "run_id"], unique=False
    )
    op.create_table(
        "memory_sources",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("memory_id", sa.String(length=64), nullable=True, comment="记忆标识"),
        sa.Column("source_type", sa.String(length=64), nullable=True, comment="来源类型"),
        sa.Column("source_id", sa.String(length=128), nullable=True, comment="来源标识"),
        sa.Column("source_version", sa.String(length=128), nullable=True, comment="来源版本"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True, comment="观测时间"),
        sa.Column("evidence_id", sa.String(length=64), nullable=True, comment="证据标识"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="来源状态"),
        sa.Column("authority", sa.String(length=32), nullable=True, comment="来源权限类型"),
        sa.Column("trust_level", sa.Integer(), nullable=True, comment="来源可信等级"),
        comment="记忆有效来源",
    )
    op.create_index("ix_memory_sources_0", "memory_sources", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_memory_sources_1", "memory_sources", ["channel_id", "memory_id"], unique=False
    )
    op.create_index(
        "ix_memory_sources_2",
        "memory_sources",
        [
            "channel_id",
            "environment",
            "data_scope_id",
            "subject_type",
            "subject_id",
            "source_type",
            "source_id",
        ],
        unique=False,
    )
    op.create_table(
        "memory_versions",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("memory_id", sa.String(length=64), nullable=True, comment="记忆标识"),
        sa.Column("previous_version_id", sa.String(length=64), nullable=True, comment="前版本"),
        sa.Column("value", postgresql.JSONB(), nullable=True, comment="历史内容空槽位，不保存原文"),
        sa.Column("changed_by", sa.String(length=128), nullable=True, comment="变更主体"),
        sa.Column("reason", sa.String(length=512), nullable=True, comment="变更原因"),
        sa.Column("version_number", sa.Integer(), nullable=True, comment="版本序号"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="变更后状态"),
        sa.Column("source_ids", postgresql.JSONB(), nullable=True, comment="有效来源记录集合"),
        comment="记忆变更版本",
    )
    op.create_index("ix_memory_versions_0", "memory_versions", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_memory_versions_1",
        "memory_versions",
        ["channel_id", "memory_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("conversation_id", sa.String(length=64), nullable=True, comment="会话标识"),
        sa.Column("role", sa.String(length=32), nullable=True, comment="消息角色"),
        sa.Column("content_parts", postgresql.JSONB(), nullable=True, comment="文本及附件引用"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="关联运行"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="消息完成状态"),
        sa.Column("sequence", sa.BigInteger(), nullable=True, comment="会话消息顺序"),
        sa.Column("turn_id", sa.String(length=64), nullable=True, comment="关联轮次"),
        sa.Column("event_sequence", sa.BigInteger(), nullable=True, comment="已投影运行事件顺序"),
        comment="会话消息",
    )
    op.create_index("ix_messages_0", "messages", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_messages_1",
        "messages",
        ["channel_id", "conversation_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_messages_2", "messages", ["channel_id", "conversation_id", "sequence"], unique=False
    )
    op.create_table(
        "model_connections",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="连接名称"),
        sa.Column("protocol", sa.String(length=64), nullable=True, comment="协议类型"),
        sa.Column("endpoint", sa.String(length=2048), nullable=True, comment="服务地址"),
        sa.Column("credential_ref", sa.String(length=64), nullable=True, comment="凭据标识"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True, comment="调用超时秒数"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="连接启用状态"),
        sa.Column("health_status", sa.String(length=32), nullable=True, comment="最近健康状态"),
        sa.Column("provider_id", sa.String(length=64), nullable=True, comment="供应商字典引用"),
        sa.Column(
            "current_version_id", sa.String(length=64), nullable=True, comment="当前连接版本标识"
        ),
        sa.Column(
            "health_reason", sa.String(length=512), nullable=True, comment="最近健康异常原因"
        ),
        sa.Column(
            "health_checked_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="最近健康检查时间",
        ),
        sa.Column(
            "validation_revision", sa.BigInteger(), nullable=True, comment="能力验证语义修订号"
        ),
        comment="模型供应商连接",
    )
    op.create_index(
        "ix_model_connections_0", "model_connections", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_model_connections_1",
        "model_connections",
        ["channel_id", "environment", "status"],
        unique=False,
    )
    op.create_table(
        "model_routes",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("code", sa.String(length=64), nullable=True, comment="路由编码"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="路由名称"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="启用状态"),
        comment="模型路由资源",
    )
    op.create_index("ix_model_routes_0", "model_routes", ["channel_id", "id"], unique=False)
    op.create_index("ix_model_routes_1", "model_routes", ["channel_id", "code"], unique=False)
    op.create_table(
        "model_tests",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("model_id", sa.String(length=64), nullable=True, comment="模型标识"),
        sa.Column("config_revision", sa.BigInteger(), nullable=True, comment="配置修订号"),
        sa.Column("cases", postgresql.JSONB(), nullable=True, comment="验证用例"),
        sa.Column("results", postgresql.JSONB(), nullable=True, comment="验证结果"),
        sa.Column("latency_ms", sa.Integer(), nullable=True, comment="耗时毫秒"),
        sa.Column("attempt_ids", postgresql.JSONB(), nullable=True, comment="实际尝试引用"),
        sa.Column("config_digest", sa.String(length=64), nullable=True, comment="冻结配置摘要"),
        sa.Column("execution", postgresql.JSONB(), nullable=True, comment="冻结调试执行描述"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="验证执行状态"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="统一调试运行标识"),
        sa.Column("error_code", sa.String(length=64), nullable=True, comment="验证失败类别"),
        sa.Column("reason", sa.String(length=512), nullable=True, comment="验证失败原因"),
        comment="模型验证记录",
    )
    op.create_index("ix_model_tests_0", "model_tests", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_model_tests_1", "model_tests", ["channel_id", "model_id", "created_at"], unique=False
    )
    op.create_table(
        "models",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("model_code", sa.String(length=64), nullable=True, comment="稳定模型编码"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="模型名称"),
        sa.Column("connection_id", sa.String(length=64), nullable=True, comment="连接标识"),
        sa.Column(
            "provider_model_name", sa.String(length=256), nullable=True, comment="供应商模型名"
        ),
        sa.Column("context_limit", sa.Integer(), nullable=True, comment="上下文上限"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="启用状态"),
        sa.Column(
            "capabilities", postgresql.JSONB(), nullable=True, comment="按能力记录支持及验证状态"
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True, comment="验证时间"),
        sa.Column(
            "current_version_id",
            sa.String(length=64),
            nullable=True,
            comment="当前模型映射版本标识",
        ),
        sa.Column("parameters", postgresql.JSONB(), nullable=True, comment="模型默认参数"),
        sa.Column(
            "parameter_allowlist", postgresql.JSONB(), nullable=True, comment="模型允许的参数清单"
        ),
        sa.Column(
            "validation_revision", sa.BigInteger(), nullable=True, comment="能力验证语义修订号"
        ),
        comment="模型映射",
    )
    op.create_index("ix_models_0", "models", ["channel_id", "id"], unique=False)
    op.create_index("ix_models_1", "models", ["channel_id", "model_code"], unique=False)
    op.create_table(
        "platform_accounts",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("login_name", sa.String(length=128), nullable=True, comment="规范化登录名"),
        sa.Column("display_name", sa.String(length=128), nullable=True, comment="显示名称"),
        sa.Column("password_hash", sa.String(length=512), nullable=True, comment="密码安全摘要"),
        sa.Column("platform_roles", postgresql.JSONB(), nullable=True, comment="平台角色清单"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="账号状态"),
        sa.Column("must_change_password", sa.Boolean(), nullable=True, comment="首次修改密码标记"),
        sa.Column(
            "credential_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="凭据更新时间",
        ),
        sa.Column("credential_version", sa.BigInteger(), nullable=True, comment="凭据撤销代次"),
        comment="平台账号",
    )
    op.create_index(
        "ix_platform_accounts_0", "platform_accounts", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_platform_accounts_1", "platform_accounts", ["channel_id", "login_name"], unique=False
    )
    op.create_table(
        "platform_limits",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("limit_code", sa.String(length=64), nullable=True, comment="平台限额编码"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="限额名称"),
        sa.Column("kind", sa.String(length=32), nullable=True, comment="并发或请求数量类别"),
        sa.Column("period", sa.String(length=32), nullable=True, comment="限额周期"),
        sa.Column("timezone", sa.String(length=64), nullable=True, comment="周期时区"),
        sa.Column(
            "limit_value", sa.Numeric(precision=24, scale=8), nullable=True, comment="限额数量"
        ),
        sa.Column("unit", sa.String(length=32), nullable=True, comment="计量单位"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="限额启用状态"),
        sa.Column("replaces_id", sa.String(length=64), nullable=True, comment="前一个限额版本标识"),
        sa.Column(
            "effective_at", sa.DateTime(timezone=True), nullable=True, comment="该限额版本生效时间"
        ),
        comment="系统渠道平台总准入限额",
    )
    op.create_index("ix_platform_limits_0", "platform_limits", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_platform_limits_1", "platform_limits", ["channel_id", "limit_code"], unique=False
    )
    op.create_index(
        "ix_platform_limits_current",
        "platform_limits",
        ["channel_id", "limit_code", "created_at"],
        unique=False,
    )
    op.create_table(
        "platform_quota_occupancies",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column(
            "target_channel_id", sa.String(length=64), nullable=True, comment="实际业务渠道标识"
        ),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="实际运行标识"),
        sa.Column("limit_id", sa.String(length=64), nullable=True, comment="平台限额版本"),
        sa.Column("limit_code", sa.String(length=64), nullable=True, comment="稳定平台限额编码"),
        sa.Column(
            "period_start", sa.DateTime(timezone=True), nullable=True, comment="计数周期起点"
        ),
        sa.Column("unit", sa.String(length=32), nullable=True, comment="请求量或并发单位"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="占用状态"),
        comment="平台配额运行占用",
    )
    op.create_index(
        "ix_platform_quota_held",
        "platform_quota_occupancies",
        ["channel_id", "limit_code", "status"],
        unique=False,
    )
    op.create_index(
        "ix_platform_quota_occupancies_0",
        "platform_quota_occupancies",
        ["channel_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_platform_quota_occupancies_1",
        "platform_quota_occupancies",
        ["channel_id", "limit_code", "period_start"],
        unique=False,
    )
    op.create_index(
        "ix_platform_quota_occupancies_2",
        "platform_quota_occupancies",
        ["channel_id", "target_channel_id", "run_id"],
        unique=False,
    )
    op.create_index(
        "ix_platform_quota_period",
        "platform_quota_occupancies",
        ["channel_id", "limit_code", "created_at"],
        unique=False,
    )
    op.create_table(
        "price_versions",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("model_id", sa.String(length=64), nullable=True, comment="模型标识"),
        sa.Column("currency", sa.String(length=3), nullable=True, comment="币种"),
        sa.Column("price_items", postgresql.JSONB(), nullable=True, comment="各计价维度和子集关系"),
        sa.Column("unit", sa.String(length=64), nullable=True, comment="计价单位"),
        sa.Column(
            "effective_at", sa.DateTime(timezone=True), nullable=True, comment="价格生效时间"
        ),
        sa.Column("source", sa.String(length=1024), nullable=True, comment="价格来源"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="价格版本名称"),
        sa.Column(
            "subset_relations", postgresql.JSONB(), nullable=True, comment="适配器计量子集关系"
        ),
        comment="模型价格版本",
    )
    op.create_index("ix_price_versions_0", "price_versions", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_price_versions_1",
        "price_versions",
        ["channel_id", "model_id", "effective_at"],
        unique=False,
    )
    op.create_table(
        "prompt_samples",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("prompt_id", sa.String(length=64), nullable=True, comment="提示词资源标识"),
        sa.Column("title", sa.String(length=128), nullable=True, comment="样例名称"),
        sa.Column("input", postgresql.JSONB(), nullable=True, comment="样例输入"),
        sa.Column("expected_constraints", postgresql.JSONB(), nullable=True, comment="预期断言"),
        comment="提示词调试样例",
    )
    op.create_index("ix_prompt_samples_0", "prompt_samples", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_prompt_samples_1", "prompt_samples", ["channel_id", "prompt_id"], unique=False
    )
    op.create_table(
        "prompt_tests",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("version_id", sa.String(length=64), nullable=True, comment="版本标识"),
        sa.Column("draft_revision", sa.BigInteger(), nullable=True, comment="草稿修订"),
        sa.Column(
            "release_snapshot_id", sa.String(length=64), nullable=True, comment="冻结快照标识"
        ),
        sa.Column(
            "model_route_version", sa.String(length=64), nullable=True, comment="模型路由版本"
        ),
        sa.Column(
            "rendered_input_ref", sa.String(length=64), nullable=True, comment="渲染内容引用"
        ),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="调试运行标识"),
        sa.Column(
            "frozen_version", postgresql.JSONB(), nullable=True, comment="调试时固定的提示词版本"
        ),
        sa.Column(
            "sample_snapshot",
            postgresql.JSONB(),
            nullable=True,
            comment="调试时固定的样例与预期断言",
        ),
        sa.Column(
            "rendered_input",
            postgresql.JSONB(),
            nullable=True,
            comment="保持来源分区的完整渲染快照",
        ),
        sa.Column(
            "descriptor_digest", sa.String(length=64), nullable=True, comment="调试执行描述摘要"
        ),
        sa.Column("sample_id", sa.String(length=64), nullable=True, comment="调试样例标识"),
        sa.Column(
            "model_route_name", sa.String(length=128), nullable=True, comment="调试时的模型路由名称"
        ),
        sa.Column("status", sa.String(length=32), nullable=True, comment="调试受理状态"),
        comment="提示词调试记录",
    )
    op.create_index("ix_prompt_tests_0", "prompt_tests", ["channel_id", "id"], unique=False)
    op.create_index("ix_prompt_tests_1", "prompt_tests", ["channel_id", "version_id"], unique=False)
    op.create_index(
        "ix_prompt_tests_2",
        "prompt_tests",
        ["channel_id", "environment", "version_id", "descriptor_digest"],
        unique=False,
    )
    op.create_table(
        "prompts",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("prompt_code", sa.String(length=64), nullable=True, comment="提示词编码"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="提示词名称"),
        sa.Column("purpose", sa.String(length=512), nullable=True, comment="使用用途"),
        sa.Column("owner", sa.String(length=128), nullable=True, comment="负责人标识"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="资源状态"),
        comment="提示词资源",
    )
    op.create_index("ix_prompts_0", "prompts", ["channel_id", "id"], unique=False)
    op.create_index("ix_prompts_1", "prompts", ["channel_id", "prompt_code"], unique=False)
    op.create_table(
        "provider_catalog",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("code", sa.String(length=64), nullable=True, comment="供应商编码"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="供应商名称"),
        sa.Column("protocols", postgresql.JSONB(), nullable=True, comment="支持协议族"),
        sa.Column("template_content", postgresql.JSONB(), nullable=True, comment="无凭据连接模板"),
        comment="供应商字典",
    )
    op.create_index("ix_provider_catalog_0", "provider_catalog", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_provider_catalog_1", "provider_catalog", ["channel_id", "code"], unique=False
    )
    op.create_table(
        "provider_statements",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="账单来源名称"),
        sa.Column("source_digest", sa.String(length=64), nullable=True, comment="导入来源摘要"),
        sa.Column("version", sa.String(length=64), nullable=True, comment="来源账单版本"),
        sa.Column("connection_id", sa.String(length=64), nullable=True, comment="模型连接标识"),
        sa.Column("currency", sa.String(length=3), nullable=True, comment="账单币种"),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True, comment="核查开始时间"),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True, comment="核查结束时间"),
        sa.Column("lines", postgresql.JSONB(), nullable=True, comment="规范化供应商记录"),
        sa.Column("owner_key", sa.String(length=64), nullable=True, comment="创建身份摘要"),
        comment="供应商账单核查批次",
    )
    op.create_index(
        "ix_provider_statements_0", "provider_statements", ["channel_id", "id"], unique=False
    )
    op.create_table(
        "recovery_barriers",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="恢复屏障状态"),
        sa.Column("recovery_id", sa.String(length=64), nullable=True, comment="本次恢复标识"),
        sa.Column(
            "marker_digest", sa.String(length=64), nullable=True, comment="已校验删除账本摘要"
        ),
        sa.Column(
            "verified_at", sa.DateTime(timezone=True), nullable=True, comment="删除账本核对时间"
        ),
        comment="内容恢复屏障",
    )
    op.create_index(
        "ix_recovery_barriers_0", "recovery_barriers", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_recovery_barriers_1",
        "recovery_barriers",
        ["channel_id", "environment", "data_scope_id", "subject_type", "subject_id"],
        unique=False,
    )
    op.create_table(
        "release_mappings",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("resource_type", sa.String(length=64), nullable=True, comment="资源类型"),
        sa.Column("resource_id", sa.String(length=64), nullable=True, comment="稳定资源标识"),
        sa.Column("version_id", sa.String(length=64), nullable=True, comment="生效版本标识"),
        sa.Column("published_by", sa.String(length=128), nullable=True, comment="发布主体标识"),
        sa.Column("release_note", sa.String(length=1024), nullable=True, comment="发布说明"),
        comment="环境生效版本映射",
    )
    op.create_index("ix_release_mappings_0", "release_mappings", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_release_mappings_1",
        "release_mappings",
        ["channel_id", "environment", "resource_type", "resource_id"],
        unique=False,
    )
    op.create_table(
        "release_snapshots",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="所属运行标识"),
        sa.Column("purpose", sa.String(length=32), nullable=True, comment="使用用途"),
        sa.Column("versions", postgresql.JSONB(), nullable=True, comment="具体版本及草稿内容快照"),
        sa.Column(
            "dependencies_digest", sa.String(length=64), nullable=True, comment="全量依赖摘要"
        ),
        sa.Column("output_schema", postgresql.JSONB(), nullable=True, comment="固定输出结构"),
        comment="执行依赖冻结快照",
    )
    op.create_index(
        "ix_release_snapshots_0", "release_snapshots", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_release_snapshots_1",
        "release_snapshots",
        ["channel_id", "environment", "run_id"],
        unique=False,
    )
    op.create_table(
        "resource_grants",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("grantee_type", sa.String(length=64), nullable=True, comment="受权主体类型"),
        sa.Column("grantee_id", sa.String(length=128), nullable=True, comment="受权主体标识"),
        sa.Column("resource_type", sa.String(length=64), nullable=True, comment="资源类型"),
        sa.Column("resource_id", sa.String(length=64), nullable=True, comment="资源标识"),
        sa.Column("allowed_actions", postgresql.JSONB(), nullable=True, comment="允许动作"),
        sa.Column("environments", postgresql.JSONB(), nullable=True, comment="授权环境"),
        sa.Column("data_scopes", postgresql.JSONB(), nullable=True, comment="授权数据域"),
        comment="资源授权",
    )
    op.create_index("ix_resource_grants_0", "resource_grants", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_resource_grants_1",
        "resource_grants",
        ["channel_id", "grantee_type", "grantee_id"],
        unique=False,
    )
    op.create_index(
        "ix_resource_grants_2",
        "resource_grants",
        ["channel_id", "resource_type", "resource_id"],
        unique=False,
    )
    op.create_table(
        "resource_references",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column(
            "source_version_id", sa.String(length=64), nullable=True, comment="引用方版本标识"
        ),
        sa.Column(
            "target_version_id", sa.String(length=64), nullable=True, comment="被引用版本标识"
        ),
        sa.Column(
            "target_resource_type", sa.String(length=64), nullable=True, comment="被引用资源类型"
        ),
        comment="版本引用关系",
    )
    op.create_index(
        "ix_resource_references_0", "resource_references", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_resource_references_1",
        "resource_references",
        ["channel_id", "target_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_resource_references_2",
        "resource_references",
        ["channel_id", "source_version_id"],
        unique=False,
    )
    op.create_table(
        "resource_versions",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("resource_type", sa.String(length=64), nullable=True, comment="资源类型"),
        sa.Column("resource_id", sa.String(length=64), nullable=True, comment="稳定资源标识"),
        sa.Column("version_label", sa.String(length=128), nullable=True, comment="可读版本名称"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="版本状态"),
        sa.Column("content", postgresql.JSONB(), nullable=True, comment="版本内容"),
        sa.Column("content_digest", sa.String(length=64), nullable=True, comment="规范化内容摘要"),
        sa.Column("dependencies", postgresql.JSONB(), nullable=True, comment="固定依赖清单"),
        sa.Column("dependencies_digest", sa.String(length=64), nullable=True, comment="依赖摘要"),
        sa.Column("output_schema", postgresql.JSONB(), nullable=True, comment="输出结构定义"),
        sa.Column("created_by", sa.String(length=128), nullable=True, comment="创建主体标识"),
        comment="资源版本与草稿",
    )
    op.create_index(
        "ix_resource_versions_0", "resource_versions", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_resource_versions_1",
        "resource_versions",
        ["channel_id", "resource_type", "resource_id", "state"],
        unique=False,
    )
    op.create_table(
        "run_contents",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="所属运行"),
        sa.Column("kind", sa.String(length=32), nullable=True, comment="内容用途"),
        sa.Column("payload", postgresql.JSONB(), nullable=True, comment="受控内容正文"),
        comment="运行敏感内容引用",
    )
    op.create_index("ix_run_contents_0", "run_contents", ["channel_id", "id"], unique=False)
    op.create_index("ix_run_contents_1", "run_contents", ["channel_id", "run_id"], unique=False)
    op.create_table(
        "run_events",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="运行标识"),
        sa.Column("sequence", sa.BigInteger(), nullable=True, comment="运行内单调序号"),
        sa.Column("event_type", sa.String(length=64), nullable=True, comment="事件类别"),
        sa.Column("payload_ref", sa.String(length=64), nullable=True, comment="事件内容引用"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, comment="事件失效时间"),
        comment="可补发运行事件",
    )
    op.create_index("ix_run_events_0", "run_events", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_run_events_1", "run_events", ["channel_id", "run_id", "sequence"], unique=False
    )
    op.create_table(
        "run_idempotency",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("client_id", sa.String(length=64), nullable=True, comment="接入服务稳定标识"),
        sa.Column("agent_id", sa.String(length=64), nullable=True, comment="智能体标识"),
        sa.Column("key", sa.String(length=128), nullable=True, comment="调用方幂等键"),
        sa.Column("request_digest", sa.String(length=64), nullable=True, comment="语义请求摘要"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="首次受理运行"),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=True, comment="最早可清理时间"
        ),
        sa.Column("identity_type", sa.String(length=32), nullable=True, comment="稳定身份来源类型"),
        sa.Column(
            "identity_id", sa.String(length=128), nullable=True, comment="稳定调用服务或管理操作者"
        ),
        sa.Column("scope_digest", sa.String(length=64), nullable=True, comment="幂等范围摘要"),
        comment="接入请求幂等",
    )
    op.create_index("ix_run_idempotency_0", "run_idempotency", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_run_idempotency_1",
        "run_idempotency",
        [
            "channel_id",
            "environment",
            "data_scope_id",
            "subject_type",
            "subject_id",
            "client_id",
            "agent_id",
            "key",
        ],
        unique=False,
    )
    op.create_index(
        "ix_run_idempotency_2",
        "run_idempotency",
        ["channel_id", "scope_digest", "key"],
        unique=False,
    )
    op.create_table(
        "run_leases",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="运行标识"),
        sa.Column("worker_id", sa.String(length=128), nullable=True, comment="进程标识"),
        sa.Column("lease_version", sa.BigInteger(), nullable=True, comment="租约代次"),
        sa.Column(
            "heartbeat_at", sa.DateTime(timezone=True), nullable=True, comment="最近续租时间"
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, comment="租约到期时间"),
        comment="工作进程执行租约",
    )
    op.create_index("ix_run_leases_0", "run_leases", ["channel_id", "id"], unique=False)
    op.create_index("ix_run_leases_1", "run_leases", ["channel_id", "run_id"], unique=False)
    op.create_table(
        "run_occupancies",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("conversation_id", sa.String(length=64), nullable=True, comment="占用会话"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="占用运行"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="占用状态"),
        comment="运行会话执行占用",
    )
    op.create_index("ix_run_occupancies_0", "run_occupancies", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_run_occupancies_1",
        "run_occupancies",
        ["channel_id", "environment", "conversation_id"],
        unique=False,
    )
    op.create_table(
        "run_recoveries",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="所属运行"),
        sa.Column("lease_version", sa.BigInteger(), nullable=True, comment="失效租约代次"),
        sa.Column("decision", sa.String(length=32), nullable=True, comment="恢复判断结果"),
        sa.Column("reason", sa.String(length=64), nullable=True, comment="脱敏原因类别"),
        comment="执行租约恢复判断记录",
    )
    op.create_index("ix_run_recoveries_0", "run_recoveries", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_run_recoveries_1",
        "run_recoveries",
        ["channel_id", "run_id", "lease_version"],
        unique=False,
    )
    op.create_table(
        "run_steps",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="运行标识"),
        sa.Column("node_key", sa.String(length=128), nullable=True, comment="流程节点"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="步骤状态"),
        sa.Column("input_ref", sa.String(length=64), nullable=True, comment="输入引用"),
        sa.Column("output_ref", sa.String(length=64), nullable=True, comment="输出引用"),
        sa.Column("checkpoint_ref", sa.String(length=64), nullable=True, comment="恢复点"),
        sa.Column("sequence", sa.BigInteger(), nullable=True, comment="步骤顺序"),
        sa.Column("attempt_count", sa.Integer(), nullable=True, comment="实际尝试累计次数"),
        sa.Column("lease_version", sa.BigInteger(), nullable=True, comment="最近有效提交租约代次"),
        comment="执行步骤",
    )
    op.create_index("ix_run_steps_0", "run_steps", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_run_steps_1", "run_steps", ["channel_id", "run_id", "sequence"], unique=False
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("source_type", sa.String(length=32), nullable=True, comment="发起来源"),
        sa.Column("client_id", sa.String(length=64), nullable=True, comment="接入服务"),
        sa.Column("key_id", sa.String(length=64), nullable=True, comment="渠道密钥"),
        sa.Column("actor_id", sa.String(length=128), nullable=True, comment="管理发起人"),
        sa.Column("agent_id", sa.String(length=64), nullable=True, comment="智能体"),
        sa.Column("agent_version_id", sa.String(length=64), nullable=True, comment="智能体版本"),
        sa.Column(
            "release_snapshot_id", sa.String(length=64), nullable=True, comment="冻结依赖快照"
        ),
        sa.Column("purpose", sa.String(length=32), nullable=True, comment="调用用途"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="技术状态"),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True, comment="全程截止时间"),
        sa.Column("conversation_id", sa.String(length=64), nullable=True, comment="会话标识"),
        sa.Column("parent_run_id", sa.String(length=64), nullable=True, comment="来源运行"),
        sa.Column("input_ref", sa.String(length=64), nullable=True, comment="输入内容引用"),
        sa.Column("result_ref", sa.String(length=64), nullable=True, comment="正式结果引用"),
        sa.Column(
            "partial_output_ref", sa.String(length=64), nullable=True, comment="部分输出引用"
        ),
        sa.Column("error", postgresql.JSONB(), nullable=True, comment="脱敏错误"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True, comment="终结时间"),
        sa.Column("agent_code", sa.String(length=128), nullable=True, comment="智能体调用编码"),
        sa.Column("agent_name", sa.String(length=128), nullable=True, comment="受理时智能体名称"),
        sa.Column("identity", postgresql.JSONB(), nullable=True, comment="不含访问令牌的原始身份"),
        sa.Column(
            "execution_policy", postgresql.JSONB(), nullable=True, comment="冻结执行限额与步骤策略"
        ),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True, comment="全程时限秒数"),
        sa.Column("timeout_source", sa.String(length=128), nullable=True, comment="时限配置来源"),
        sa.Column("event_sequence", sa.BigInteger(), nullable=True, comment="最后事件序号"),
        sa.Column("resources_released", sa.Boolean(), nullable=True, comment="终态占用已释放"),
        sa.Column("recovery_count", sa.Integer(), nullable=True, comment="租约失效恢复次数"),
        comment="逻辑执行任务",
    )
    op.create_index("ix_runs_0", "runs", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_runs_1", "runs", ["channel_id", "environment", "state", "created_at"], unique=False
    )
    op.create_table(
        "service_clients",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="服务名称"),
        sa.Column("scopes", postgresql.JSONB(), nullable=True, comment="权限上限"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="服务状态"),
        sa.Column("data_scopes", postgresql.JSONB(), nullable=True, comment="授权业务数据域清单"),
        comment="业务接入服务",
    )
    op.create_index("ix_service_clients_0", "service_clients", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_service_clients_1",
        "service_clients",
        ["channel_id", "environment", "status"],
        unique=False,
    )
    op.create_table(
        "skill_files",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("version_id", sa.String(length=64), nullable=True, comment="技能版本"),
        sa.Column("relative_path", sa.String(length=1024), nullable=True, comment="包内路径"),
        sa.Column("content_type", sa.String(length=128), nullable=True, comment="媒体类型"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True, comment="文件字节数"),
        sa.Column("sha256", sa.String(length=64), nullable=True, comment="文件摘要"),
        sa.Column("artifact_id", sa.String(length=64), nullable=True, comment="受控内容引用"),
        sa.Column("loadable", sa.Boolean(), nullable=True, comment="当前是否可加载"),
        sa.Column("unavailable_reason", sa.Text(), nullable=True, comment="不可加载原因"),
        comment="技能版本文件清单",
    )
    op.create_index("ix_skill_files_0", "skill_files", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_skill_files_1",
        "skill_files",
        ["channel_id", "version_id", "relative_path"],
        unique=False,
    )
    op.create_table(
        "skill_tests",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("version_id", sa.String(length=64), nullable=True, comment="技能版本"),
        sa.Column(
            "release_snapshot_id", sa.String(length=64), nullable=True, comment="上下文冻结快照"
        ),
        sa.Column("selected_files", postgresql.JSONB(), nullable=True, comment="实际加载文件"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="运行标识"),
        sa.Column("result", postgresql.JSONB(), nullable=True, comment="测试结果"),
        sa.Column(
            "context_snapshot", postgresql.JSONB(), nullable=True, comment="加载输入与版本冻结快照"
        ),
        comment="技能加载测试",
    )
    op.create_index("ix_skill_tests_0", "skill_tests", ["channel_id", "id"], unique=False)
    op.create_index("ix_skill_tests_1", "skill_tests", ["channel_id", "version_id"], unique=False)
    op.create_table(
        "skills",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("skill_code", sa.String(length=64), nullable=True, comment="技能编码"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="技能名称"),
        sa.Column("description", sa.Text(), nullable=True, comment="用途说明"),
        sa.Column("owner", sa.String(length=128), nullable=True, comment="负责人"),
        sa.Column("tags", postgresql.JSONB(), nullable=True, comment="发现标签"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="启用状态"),
        comment="技能资源",
    )
    op.create_index("ix_skills_0", "skills", ["channel_id", "id"], unique=False)
    op.create_index("ix_skills_1", "skills", ["channel_id", "skill_code"], unique=False)
    op.create_table(
        "source_links",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("source_type", sa.String(length=64), nullable=True, comment="来源类型"),
        sa.Column("source_id", sa.String(length=128), nullable=True, comment="来源标识"),
        sa.Column("derived_type", sa.String(length=64), nullable=True, comment="派生类型"),
        sa.Column("derived_id", sa.String(length=128), nullable=True, comment="派生标识"),
        sa.Column("source_version", sa.String(length=128), nullable=True, comment="来源版本"),
        comment="内容来源与派生关系",
    )
    op.create_index("ix_source_links_0", "source_links", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_source_links_1",
        "source_links",
        ["channel_id", "environment", "source_type", "source_id"],
        unique=False,
    )
    op.create_index(
        "ix_source_links_2",
        "source_links",
        ["channel_id", "environment", "derived_type", "derived_id"],
        unique=False,
    )
    op.create_table(
        "subject_review_bindings",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="所属业务数据域"),
        sa.Column("client_id", sa.String(length=64), nullable=True, comment="受限接入服务标识"),
        sa.Column("connection_id", sa.String(length=64), nullable=True, comment="固定身份复核连接"),
        sa.Column("discovery_id", sa.String(length=64), nullable=True, comment="授权时发现快照"),
        sa.Column(
            "remote_tool_name", sa.String(length=256), nullable=True, comment="专用身份复核工具名"
        ),
        sa.Column("tool_name", sa.String(length=128), nullable=True, comment="身份工具显示名称"),
        sa.Column(
            "connection_revision", sa.BigInteger(), nullable=True, comment="授权时连接配置修订"
        ),
        sa.Column(
            "schema_hash", sa.String(length=128), nullable=True, comment="授权时身份工具契约摘要"
        ),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True, comment="身份复核超时秒数"),
        sa.Column("enabled", sa.Boolean(), nullable=True, comment="是否允许身份复核"),
        comment="当前主体复核的固定 MCP 绑定",
    )
    op.create_index(
        "ix_subject_review_bindings_0",
        "subject_review_bindings",
        ["channel_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_subject_review_bindings_1",
        "subject_review_bindings",
        ["channel_id", "environment", "data_scope_id", "client_id"],
        unique=False,
    )
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="运行标识"),
        sa.Column("step_id", sa.String(length=64), nullable=True, comment="步骤标识"),
        sa.Column("attempt_id", sa.String(length=64), nullable=True, comment="尝试标识"),
        sa.Column("tool_version_id", sa.String(length=64), nullable=True, comment="工具版本"),
        sa.Column("args_digest", sa.String(length=64), nullable=True, comment="参数摘要"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="调用状态"),
        sa.Column("source_request_id", sa.String(length=256), nullable=True, comment="源请求标识"),
        sa.Column("result_ref", sa.String(length=64), nullable=True, comment="结果内容引用"),
        sa.Column("latency_ms", sa.Integer(), nullable=True, comment="耗时毫秒"),
        sa.Column("error", postgresql.JSONB(), nullable=True, comment="脱敏错误"),
        sa.Column("tool_id", sa.String(length=64), nullable=True, comment="工具资源标识"),
        sa.Column("redacted_arguments", postgresql.JSONB(), nullable=True, comment="脱敏输入参数"),
        sa.Column("result_summary", postgresql.JSONB(), nullable=True, comment="结果结构摘要"),
        sa.Column("evidence_ids", postgresql.JSONB(), nullable=True, comment="有效证据标识集合"),
        sa.Column("attempt", postgresql.JSONB(), nullable=True, comment="独立尝试状态"),
        comment="工具实际调用",
    )
    op.create_index("ix_tool_calls_0", "tool_calls", ["channel_id", "id"], unique=False)
    op.create_index("ix_tool_calls_1", "tool_calls", ["channel_id", "run_id"], unique=False)
    op.create_index("ix_tool_calls_2", "tool_calls", ["channel_id", "attempt_id"], unique=False)
    op.create_table(
        "tools",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("tool_code", sa.String(length=64), nullable=True, comment="调用编码"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="工具名称"),
        sa.Column("description", sa.Text(), nullable=True, comment="用途说明"),
        sa.Column("source_type", sa.String(length=32), nullable=True, comment="来源类型"),
        sa.Column("owner", sa.String(length=128), nullable=True, comment="负责人"),
        sa.Column("status", sa.String(length=32), nullable=True, comment="启用状态"),
        comment="工具资源",
    )
    op.create_index("ix_tools_0", "tools", ["channel_id", "id"], unique=False)
    op.create_index("ix_tools_1", "tools", ["channel_id", "tool_code"], unique=False)
    op.create_table(
        "usage_adjustments",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("usage_id", sa.String(length=64), nullable=True, comment="账本标识"),
        sa.Column("event_id", sa.String(length=64), nullable=True, comment="来源事件标识"),
        sa.Column("previous_revision", sa.BigInteger(), nullable=True, comment="前次修订"),
        sa.Column(
            "amount_delta", sa.Numeric(precision=24, scale=8), nullable=True, comment="金额变动"
        ),
        sa.Column("currency", sa.String(length=3), nullable=True, comment="币种"),
        sa.Column("reason", sa.String(length=512), nullable=True, comment="修正原因"),
        sa.Column("calculation", postgresql.JSONB(), nullable=True, comment="核算依据"),
        comment="用量计价修正轨迹",
    )
    op.create_index(
        "ix_usage_adjustments_0", "usage_adjustments", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_usage_adjustments_1",
        "usage_adjustments",
        ["channel_id", "usage_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "usage_aggregates",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("dimensions", postgresql.JSONB(), nullable=True, comment="统计维度"),
        sa.Column("dimensions_digest", sa.String(length=64), nullable=True, comment="统计范围摘要"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True, comment="周期开始"),
        sa.Column("currency", sa.String(length=3), nullable=True, comment="币种"),
        sa.Column("totals", postgresql.JSONB(), nullable=True, comment="数量及完整性分组"),
        sa.Column(
            "ledger_watermark", sa.DateTime(timezone=True), nullable=True, comment="账本处理水位"
        ),
        comment="可重算用量聚合",
    )
    op.create_index("ix_usage_aggregates_0", "usage_aggregates", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_usage_aggregates_1",
        "usage_aggregates",
        ["channel_id", "dimensions_digest", "period_start", "currency"],
        unique=False,
    )
    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("attempt_id", sa.String(length=64), nullable=True, comment="尝试标识"),
        sa.Column("connection_id", sa.String(length=64), nullable=True, comment="供应商连接标识"),
        sa.Column(
            "source_request_id", sa.String(length=256), nullable=True, comment="供应商请求标识"
        ),
        sa.Column("event_version", sa.BigInteger(), nullable=True, comment="事件版本"),
        sa.Column("raw_usage", postgresql.JSONB(), nullable=True, comment="原始计量值与子集口径"),
        sa.Column("usage_status", sa.String(length=32), nullable=True, comment="事件完整性"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True, comment="观测时间"),
        sa.Column(
            "event_payload", postgresql.JSONB(), nullable=True, comment="经契约验证的完整计量事件"
        ),
        sa.Column("payload_digest", sa.String(length=64), nullable=True, comment="事件内容摘要"),
        sa.Column("applied", sa.Boolean(), nullable=True, comment="是否成为当前有效计量"),
        comment="供应商用量来源事件",
    )
    op.create_index("ix_usage_events_0", "usage_events", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_usage_events_1",
        "usage_events",
        ["channel_id", "connection_id", "source_request_id", "event_version"],
        unique=False,
    )
    op.create_table(
        "usage_exchange_rates",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("base_currency", sa.String(length=3), nullable=True, comment="原始币种"),
        sa.Column("quote_currency", sa.String(length=3), nullable=True, comment="折算币种"),
        sa.Column("rate", sa.Numeric(precision=24, scale=8), nullable=True, comment="折算汇率"),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True, comment="汇率日期"),
        sa.Column("source", sa.String(length=1024), nullable=True, comment="汇率来源"),
        comment="核算展示汇率版本",
    )
    op.create_index(
        "ix_usage_exchange_rates_0", "usage_exchange_rates", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_usage_exchange_rates_1",
        "usage_exchange_rates",
        ["channel_id", "base_currency", "quote_currency", "effective_at"],
        unique=False,
    )
    op.create_table(
        "usage_exports",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("requested_by", sa.String(length=128), nullable=True, comment="导出人"),
        sa.Column("filters", postgresql.JSONB(), nullable=True, comment="授权筛选条件"),
        sa.Column("timezone", sa.String(length=64), nullable=True, comment="业务时区"),
        sa.Column("channel_range", postgresql.JSONB(), nullable=True, comment="明确授权的渠道集合"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="导出状态"),
        sa.Column("artifact_id", sa.String(length=64), nullable=True, comment="产物标识"),
        sa.Column(
            "scope_snapshot", postgresql.JSONB(), nullable=True, comment="创建时受信查询范围"
        ),
        sa.Column(
            "object_key", sa.String(length=1024), nullable=True, comment="渠道隔离的私有产物路径"
        ),
        sa.Column("metadata", postgresql.JSONB(), nullable=True, comment="计量口径及价格完整性"),
        sa.Column("error_message", sa.String(length=512), nullable=True, comment="导出失败原因"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, comment="导出失效时间"),
        comment="用量导出请求",
    )
    op.create_index("ix_usage_exports_0", "usage_exports", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_usage_exports_1", "usage_exports", ["channel_id", "created_at"], unique=False
    )
    op.create_table(
        "usage_records",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="运行标识"),
        sa.Column("attempt_id", sa.String(length=64), nullable=True, comment="实际尝试标识"),
        sa.Column("source_type", sa.String(length=32), nullable=True, comment="来源类型"),
        sa.Column("client_id", sa.String(length=64), nullable=True, comment="接入服务标识"),
        sa.Column("key_id", sa.String(length=64), nullable=True, comment="渠道密钥标识"),
        sa.Column("actor_id", sa.String(length=128), nullable=True, comment="管理主体标识"),
        sa.Column("agent_id", sa.String(length=64), nullable=True, comment="智能体标识"),
        sa.Column("model_id", sa.String(length=64), nullable=True, comment="模型标识"),
        sa.Column("connection_id", sa.String(length=64), nullable=True, comment="供应商连接标识"),
        sa.Column("purpose", sa.String(length=32), nullable=True, comment="调用用途"),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True, comment="输入数量"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True, comment="输出数量"),
        sa.Column("cached_tokens", sa.BigInteger(), nullable=True, comment="缓存子集数量"),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=True, comment="推理子集数量"),
        sa.Column("raw_usage_ref", sa.String(length=64), nullable=True, comment="原始用量引用"),
        sa.Column("usage_status", sa.String(length=32), nullable=True, comment="用量完整性"),
        sa.Column("pricing_status", sa.String(length=32), nullable=True, comment="计价完整性"),
        sa.Column("price_version_id", sa.String(length=64), nullable=True, comment="价格版本标识"),
        sa.Column("amount", sa.Numeric(precision=24, scale=8), nullable=True, comment="核算金额"),
        sa.Column("currency", sa.String(length=3), nullable=True, comment="币种"),
        sa.Column(
            "snapshot", postgresql.JSONB(), nullable=True, comment="受信运行来源及可读名称快照"
        ),
        sa.Column(
            "normalized_tokens", postgresql.JSONB(), nullable=True, comment="按维度归一化用量"
        ),
        sa.Column("subset_relations", postgresql.JSONB(), nullable=True, comment="计量子集关系"),
        sa.Column("calculation", postgresql.JSONB(), nullable=True, comment="当前核算公式及依据"),
        sa.Column(
            "upper_tokens", postgresql.JSONB(), nullable=True, comment="调用前核准的计量上限"
        ),
        sa.Column(
            "upper_amount",
            sa.Numeric(precision=24, scale=8),
            nullable=True,
            comment="调用前预占金额",
        ),
        sa.Column("state", sa.String(length=32), nullable=True, comment="实际调用结算状态"),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), nullable=True, comment="供应商调用发送前登记时间"
        ),
        sa.Column("outcome", sa.String(length=32), nullable=True, comment="实际尝试结果"),
        sa.Column(
            "latest_event_version", sa.BigInteger(), nullable=True, comment="当前有效来源事件版本"
        ),
        sa.Column(
            "latest_event_id", sa.String(length=64), nullable=True, comment="当前有效来源事件"
        ),
        sa.Column("final_reported", sa.Boolean(), nullable=True, comment="是否收到供应商最终用量"),
        sa.Column(
            "source_request_id", sa.String(length=256), nullable=True, comment="固定供应商请求标识"
        ),
        comment="实际尝试用量账本",
    )
    op.create_index("ix_usage_records_0", "usage_records", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_usage_records_1", "usage_records", ["channel_id", "attempt_id"], unique=False
    )
    op.create_index(
        "ix_usage_records_2", "usage_records", ["channel_id", "created_at"], unique=False
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("endpoint_id", sa.String(length=64), nullable=True, comment="投递端点标识"),
        sa.Column("event_id", sa.String(length=64), nullable=True, comment="稳定事件编号"),
        sa.Column("kind", sa.String(length=64), nullable=True, comment="事件类型"),
        sa.Column("payload", postgresql.JSONB(), nullable=True, comment="最小事件正文"),
        sa.Column("owner_key", sa.String(length=64), nullable=True, comment="执行身份摘要"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="当前处理状态"),
        sa.Column("attempts", sa.BigInteger(), nullable=True, comment="累计投递次数"),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=True, comment="下次尝试时间"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True, comment="投递租约到期"),
        sa.Column("lease_nonce", sa.String(length=64), nullable=True, comment="投递租约凭据"),
        sa.Column("error", postgresql.JSONB(), nullable=True, comment="最近投递错误"),
        sa.Column("http_status", sa.BigInteger(), nullable=True, comment="最近响应状态"),
        sa.Column(
            "cycle_attempts",
            sa.BigInteger(),
            nullable=True,
            comment="本轮自动投递次数，人工重投重新计数",
        ),
        comment="持久化事件投递",
    )
    op.create_index(
        "ix_webhook_deliveries_0", "webhook_deliveries", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_webhook_deliveries_1",
        "webhook_deliveries",
        ["channel_id", "environment", "data_scope_id", "subject_type", "subject_id"],
        unique=False,
    )
    op.create_index(
        "ix_webhook_deliveries_2",
        "webhook_deliveries",
        ["channel_id", "state", "next_at"],
        unique=False,
    )
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.String(length=64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(length=64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("environment", sa.String(length=16), nullable=True, comment="所属环境"),
        sa.Column("data_scope_id", sa.String(length=64), nullable=True, comment="业务数据域标识"),
        sa.Column("subject_type", sa.String(length=64), nullable=True, comment="业务主体类型"),
        sa.Column("subject_id", sa.String(length=128), nullable=True, comment="业务主体编号"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="端点名称"),
        sa.Column("url", sa.Text(), nullable=True, comment="固定接收地址"),
        sa.Column("secret_ref", sa.String(length=64), nullable=True, comment="签名密钥密文引用"),
        sa.Column("events", postgresql.JSONB(), nullable=True, comment="订阅事件类型"),
        sa.Column("owner_key", sa.String(length=64), nullable=True, comment="执行身份摘要"),
        sa.Column("identity", postgresql.JSONB(), nullable=True, comment="原执行身份快照"),
        sa.Column("state", sa.String(length=32), nullable=True, comment="当前处理状态"),
        sa.Column(
            "client_ids",
            postgresql.JSONB(),
            nullable=True,
            comment="订阅的调用服务列表；空列表仅包含配置者运行",
        ),
        comment="事件投递端点",
    )
    op.create_index(
        "ix_webhook_endpoints_0", "webhook_endpoints", ["channel_id", "id"], unique=False
    )
    op.create_index(
        "ix_webhook_endpoints_1",
        "webhook_endpoints",
        ["channel_id", "environment", "data_scope_id", "subject_type", "subject_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("webhook_endpoints")
    op.drop_table("webhook_deliveries")
    op.drop_table("usage_records")
    op.drop_table("usage_exports")
    op.drop_table("usage_exchange_rates")
    op.drop_table("usage_events")
    op.drop_table("usage_aggregates")
    op.drop_table("usage_adjustments")
    op.drop_table("tools")
    op.drop_table("tool_calls")
    op.drop_table("subject_review_bindings")
    op.drop_table("source_links")
    op.drop_table("skills")
    op.drop_table("skill_tests")
    op.drop_table("skill_files")
    op.drop_table("service_clients")
    op.drop_table("runs")
    op.drop_table("run_steps")
    op.drop_table("run_recoveries")
    op.drop_table("run_occupancies")
    op.drop_table("run_leases")
    op.drop_table("run_idempotency")
    op.drop_table("run_events")
    op.drop_table("run_contents")
    op.drop_table("resource_versions")
    op.drop_table("resource_references")
    op.drop_table("resource_grants")
    op.drop_table("release_snapshots")
    op.drop_table("release_mappings")
    op.drop_table("recovery_barriers")
    op.drop_table("provider_statements")
    op.drop_table("provider_catalog")
    op.drop_table("prompts")
    op.drop_table("prompt_tests")
    op.drop_table("prompt_samples")
    op.drop_table("price_versions")
    op.drop_table("platform_quota_occupancies")
    op.drop_table("platform_limits")
    op.drop_table("platform_accounts")
    op.drop_table("models")
    op.drop_table("model_tests")
    op.drop_table("model_routes")
    op.drop_table("model_connections")
    op.drop_table("messages")
    op.drop_table("memory_versions")
    op.drop_table("memory_sources")
    op.drop_table("memory_retrievals")
    op.drop_table("memory_preferences")
    op.drop_table("memory_policies")
    op.drop_table("memory_embeddings")
    op.drop_table("memory_deletion_jobs")
    op.drop_table("memory_consolidations")
    op.drop_table("memories")
    op.drop_table("mcp_oauth_tokens")
    op.drop_table("mcp_oauth_flows")
    op.drop_table("mcp_imports")
    op.drop_table("mcp_discoveries")
    op.drop_table("mcp_connections")
    op.drop_table("mcp_checks")
    op.drop_table("key_rotations")
    op.drop_table("key_identity_index")
    op.drop_table("integrations")
    op.drop_table("integration_tests")
    op.drop_table("iam_revocations")
    op.drop_table("evidence_refs")
    op.drop_table("evaluations")
    op.drop_table("evaluation_results")
    op.drop_table("evaluation_reports")
    op.drop_table("evaluation_fixtures")
    op.drop_table("evaluation_datasets")
    op.drop_table("evaluation_dataset_versions")
    op.drop_table("evaluation_cases")
    op.drop_table("dispatch_outbox")
    op.drop_table("deletion_work_items")
    op.drop_table("deletion_receipts")
    op.drop_table("deletion_markers")
    op.drop_table("deletion_jobs")
    op.drop_table("delegation_nonces")
    op.drop_table("delegation_keys")
    op.drop_table("data_scopes")
    op.drop_table("custom_roles")
    op.drop_table("credentials")
    op.drop_table("conversations")
    op.drop_table("conversation_turns")
    op.drop_table("conversation_summaries")
    op.drop_table("context_snapshots")
    op.drop_table("checkpoints")
    op.drop_table("channels")
    op.drop_table("channel_memberships")
    op.drop_table("channel_lifecycle_events")
    op.drop_table("channel_keys")
    op.drop_table("channel_environments")
    op.drop_table("channel_code_index")
    op.drop_table("builtin_roles")
    op.drop_table("budget_reservations")
    op.drop_table("budget_policies")
    op.drop_table("budget_alerts")
    op.drop_table("automation_schedules")
    op.drop_table("automation_items")
    op.drop_table("automation_batches")
    op.drop_table("audit_events")
    op.drop_table("attempts")
    op.drop_table("artifacts")
    op.drop_table("alert_rules")
    op.drop_table("agents")
    op.drop_table("agent_release_records")
    op.drop_table("agent_environment_states")
    op.drop_table("agent_candidates")
    op.drop_table("admissions")
