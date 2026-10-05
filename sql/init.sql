-- Creativity 全量初始化归档，适用于 PostgreSQL 17 空库或空 schema。
-- 模型版本：1.8.0；迁移基线：0034_admission_indexes。
-- 初始建库基线：alembic/versions/0001_initial.py；后续修订在其上追加。
-- 包含 110 张表、1665 个字段、244 个普通索引及全部中文注释。
-- 生成命令：make sql；一致性检查：make sql-check。请勿手工修改生成内容。
-- 执行方式与管理员初始化见 sql/README.md；表创建在连接的当前 schema。
-- 已有同名表时整个事务失败回滚；系统渠道和迁移基线与建表一起提交。

BEGIN;
SET LOCAL standard_conforming_strings = on;
SELECT pg_advisory_xact_lock(71977002001);

-- creativity_alembic_version：平台数据库迁移版本记录。
CREATE TABLE creativity_alembic_version (
	version_num VARCHAR(64),
	channel_id VARCHAR(64)
);

COMMENT ON TABLE creativity_alembic_version IS '平台数据库迁移版本记录';

COMMENT ON COLUMN creativity_alembic_version.version_num IS '当前数据库迁移修订编号';

COMMENT ON COLUMN creativity_alembic_version.channel_id IS '迁移记录所属系统渠道';

-- admissions：运行准入配额占用。
CREATE TABLE admissions (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	run_id VARCHAR(64),
	policy_refs JSONB,
	status VARCHAR(32),
	expires_at TIMESTAMP WITH TIME ZONE,
	snapshot JSONB
);

COMMENT ON TABLE admissions IS '运行准入配额占用';

COMMENT ON COLUMN admissions.id IS '记录标识';

COMMENT ON COLUMN admissions.channel_id IS '所属渠道标识';

COMMENT ON COLUMN admissions.created_at IS '创建时间';

COMMENT ON COLUMN admissions.updated_at IS '更新时间';

COMMENT ON COLUMN admissions.revision IS '并发修订号';

COMMENT ON COLUMN admissions.environment IS '所属环境';

COMMENT ON COLUMN admissions.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN admissions.subject_type IS '业务主体类型';

COMMENT ON COLUMN admissions.subject_id IS '业务主体编号';

COMMENT ON COLUMN admissions.run_id IS '运行标识';

COMMENT ON COLUMN admissions.policy_refs IS '命中配额策略';

COMMENT ON COLUMN admissions.status IS '占用状态';

COMMENT ON COLUMN admissions.expires_at IS '核查时间';

COMMENT ON COLUMN admissions.snapshot IS '运行来源及候选模型快照';

CREATE INDEX ix_admissions_0 ON admissions (channel_id, id);

CREATE INDEX ix_admissions_1 ON admissions (channel_id, run_id);

CREATE INDEX ix_admissions_active ON admissions (channel_id, status, created_at);

-- agent_candidates：冻结智能体候选快照。
CREATE TABLE agent_candidates (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	agent_id VARCHAR(64),
	source_version_id VARCHAR(64),
	source_revision BIGINT,
	purpose VARCHAR(32),
	content_digest VARCHAR(64),
	dependencies_digest VARCHAR(64),
	candidate_digest VARCHAR(64),
	spec JSONB
);

COMMENT ON TABLE agent_candidates IS '冻结智能体候选快照';

COMMENT ON COLUMN agent_candidates.id IS '记录标识';

COMMENT ON COLUMN agent_candidates.channel_id IS '所属渠道标识';

COMMENT ON COLUMN agent_candidates.created_at IS '创建时间';

COMMENT ON COLUMN agent_candidates.updated_at IS '更新时间';

COMMENT ON COLUMN agent_candidates.revision IS '并发修订号';

COMMENT ON COLUMN agent_candidates.environment IS '所属环境';

COMMENT ON COLUMN agent_candidates.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN agent_candidates.subject_type IS '业务主体类型';

COMMENT ON COLUMN agent_candidates.subject_id IS '业务主体编号';

COMMENT ON COLUMN agent_candidates.agent_id IS '智能体标识';

COMMENT ON COLUMN agent_candidates.source_version_id IS '来源版本标识';

COMMENT ON COLUMN agent_candidates.source_revision IS '来源草稿修订号';

COMMENT ON COLUMN agent_candidates.purpose IS '候选用途';

COMMENT ON COLUMN agent_candidates.content_digest IS '内容摘要';

COMMENT ON COLUMN agent_candidates.dependencies_digest IS '完整依赖摘要';

COMMENT ON COLUMN agent_candidates.candidate_digest IS '候选组合摘要';

COMMENT ON COLUMN agent_candidates.spec IS '不可变执行定义';

CREATE INDEX ix_agent_candidates_0 ON agent_candidates (channel_id, id);

CREATE INDEX ix_agent_candidates_1 ON agent_candidates (channel_id, agent_id, created_at);

-- agent_environment_states：智能体各环境停用状态。
CREATE TABLE agent_environment_states (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	agent_id VARCHAR(64),
	status VARCHAR(32),
	reason VARCHAR(1024)
);

COMMENT ON TABLE agent_environment_states IS '智能体各环境停用状态';

COMMENT ON COLUMN agent_environment_states.id IS '记录标识';

COMMENT ON COLUMN agent_environment_states.channel_id IS '所属渠道标识';

COMMENT ON COLUMN agent_environment_states.created_at IS '创建时间';

COMMENT ON COLUMN agent_environment_states.updated_at IS '更新时间';

COMMENT ON COLUMN agent_environment_states.revision IS '并发修订号';

COMMENT ON COLUMN agent_environment_states.environment IS '目标环境';

COMMENT ON COLUMN agent_environment_states.agent_id IS '智能体标识';

COMMENT ON COLUMN agent_environment_states.status IS '当前环境启用状态';

COMMENT ON COLUMN agent_environment_states.reason IS '状态变更原因';

CREATE INDEX ix_agent_environment_states_0 ON agent_environment_states (channel_id, id);

CREATE INDEX ix_agent_environment_states_1 ON agent_environment_states (channel_id, environment, agent_id);

-- agent_release_records：智能体发布检查与操作记录。
CREATE TABLE agent_release_records (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	agent_id VARCHAR(64),
	version_id VARCHAR(64),
	version_label VARCHAR(128),
	previous_version_id VARCHAR(64),
	source_revision BIGINT,
	operation VARCHAR(32),
	note VARCHAR(1024),
	actor_id VARCHAR(128),
	actor_name VARCHAR(128),
	content_digest VARCHAR(64),
	dependencies_digest VARCHAR(64),
	evidence_refs JSONB,
	checks JSONB
);

COMMENT ON TABLE agent_release_records IS '智能体发布检查与操作记录';

COMMENT ON COLUMN agent_release_records.id IS '记录标识';

COMMENT ON COLUMN agent_release_records.channel_id IS '所属渠道标识';

COMMENT ON COLUMN agent_release_records.created_at IS '创建时间';

COMMENT ON COLUMN agent_release_records.updated_at IS '更新时间';

COMMENT ON COLUMN agent_release_records.revision IS '并发修订号';

COMMENT ON COLUMN agent_release_records.environment IS '目标环境';

COMMENT ON COLUMN agent_release_records.agent_id IS '智能体标识';

COMMENT ON COLUMN agent_release_records.version_id IS '生效版本标识';

COMMENT ON COLUMN agent_release_records.version_label IS '版本名称';

COMMENT ON COLUMN agent_release_records.previous_version_id IS '原生效版本标识';

COMMENT ON COLUMN agent_release_records.source_revision IS '发布来源修订号';

COMMENT ON COLUMN agent_release_records.operation IS '操作类型';

COMMENT ON COLUMN agent_release_records.note IS '操作说明';

COMMENT ON COLUMN agent_release_records.actor_id IS '操作人标识';

COMMENT ON COLUMN agent_release_records.actor_name IS '操作人名称';

COMMENT ON COLUMN agent_release_records.content_digest IS '内容摘要';

COMMENT ON COLUMN agent_release_records.dependencies_digest IS '完整依赖摘要';

COMMENT ON COLUMN agent_release_records.evidence_refs IS '评测报告引用';

COMMENT ON COLUMN agent_release_records.checks IS '发布检查证据';

CREATE INDEX ix_agent_release_records_0 ON agent_release_records (channel_id, id);

CREATE INDEX ix_agent_release_records_1 ON agent_release_records (channel_id, environment, agent_id, created_at);

-- agents：智能体资源。
CREATE TABLE agents (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	agent_code VARCHAR(64),
	name VARCHAR(128),
	description TEXT,
	owner VARCHAR(128),
	status VARCHAR(32)
);

COMMENT ON TABLE agents IS '智能体资源';

COMMENT ON COLUMN agents.id IS '记录标识';

COMMENT ON COLUMN agents.channel_id IS '所属渠道标识';

COMMENT ON COLUMN agents.created_at IS '创建时间';

COMMENT ON COLUMN agents.updated_at IS '更新时间';

COMMENT ON COLUMN agents.revision IS '并发修订号';

COMMENT ON COLUMN agents.agent_code IS '调用编码';

COMMENT ON COLUMN agents.name IS '智能体名称';

COMMENT ON COLUMN agents.description IS '用途说明';

COMMENT ON COLUMN agents.owner IS '负责人标识';

COMMENT ON COLUMN agents.status IS '启用状态';

CREATE INDEX ix_agents_0 ON agents (channel_id, id);

CREATE INDEX ix_agents_1 ON agents (channel_id, agent_code);

-- alert_rules：外部告警规则。
CREATE TABLE alert_rules (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	name VARCHAR(128),
	kind VARCHAR(32),
	threshold BIGINT,
	window_seconds BIGINT,
	endpoint_id VARCHAR(64),
	owner_key VARCHAR(64),
	identity JSONB,
	state VARCHAR(32),
	active BOOLEAN,
	generation BIGINT,
	last_value BIGINT,
	pending_events JSONB,
	client_ids JSONB
);

COMMENT ON TABLE alert_rules IS '外部告警规则';

COMMENT ON COLUMN alert_rules.id IS '记录标识';

COMMENT ON COLUMN alert_rules.channel_id IS '所属渠道标识';

COMMENT ON COLUMN alert_rules.created_at IS '创建时间';

COMMENT ON COLUMN alert_rules.updated_at IS '更新时间';

COMMENT ON COLUMN alert_rules.revision IS '并发修订号';

COMMENT ON COLUMN alert_rules.environment IS '所属环境';

COMMENT ON COLUMN alert_rules.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN alert_rules.subject_type IS '业务主体类型';

COMMENT ON COLUMN alert_rules.subject_id IS '业务主体编号';

COMMENT ON COLUMN alert_rules.name IS '规则名称';

COMMENT ON COLUMN alert_rules.kind IS '监测类型';

COMMENT ON COLUMN alert_rules.threshold IS '触发次数阈值';

COMMENT ON COLUMN alert_rules.window_seconds IS '监测窗口秒数';

COMMENT ON COLUMN alert_rules.endpoint_id IS '告警投递端点';

COMMENT ON COLUMN alert_rules.owner_key IS '创建身份摘要';

COMMENT ON COLUMN alert_rules.identity IS '原执行身份快照';

COMMENT ON COLUMN alert_rules.state IS '启停状态';

COMMENT ON COLUMN alert_rules.active IS '当前是否告警';

COMMENT ON COLUMN alert_rules.generation IS '触发周期序号';

COMMENT ON COLUMN alert_rules.last_value IS '最近观察次数';

COMMENT ON COLUMN alert_rules.pending_events IS '待生成投递事件';

COMMENT ON COLUMN alert_rules.client_ids IS '订阅的调用服务列表；空列表仅包含配置者运行';

CREATE INDEX ix_alert_rules_0 ON alert_rules (channel_id, id);

-- artifacts：受控文件与产物元数据。
CREATE TABLE artifacts (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	name VARCHAR(255),
	content_type VARCHAR(128),
	object_key VARCHAR(1024),
	size_bytes BIGINT,
	sha256 VARCHAR(64),
	state VARCHAR(32),
	expires_at TIMESTAMP WITH TIME ZONE,
	upload_expires_at TIMESTAMP WITH TIME ZONE,
	registered_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE artifacts IS '受控文件与产物元数据';

COMMENT ON COLUMN artifacts.id IS '记录标识';

COMMENT ON COLUMN artifacts.channel_id IS '所属渠道标识';

COMMENT ON COLUMN artifacts.created_at IS '创建时间';

COMMENT ON COLUMN artifacts.updated_at IS '更新时间';

COMMENT ON COLUMN artifacts.revision IS '并发修订号';

COMMENT ON COLUMN artifacts.environment IS '所属环境';

COMMENT ON COLUMN artifacts.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN artifacts.subject_type IS '业务主体类型';

COMMENT ON COLUMN artifacts.subject_id IS '业务主体编号';

COMMENT ON COLUMN artifacts.name IS '文件显示名称';

COMMENT ON COLUMN artifacts.content_type IS '内容媒体类型';

COMMENT ON COLUMN artifacts.object_key IS '私有对象路径';

COMMENT ON COLUMN artifacts.size_bytes IS '文件字节数';

COMMENT ON COLUMN artifacts.sha256 IS '内容摘要';

COMMENT ON COLUMN artifacts.state IS '暂存及可用状态';

COMMENT ON COLUMN artifacts.expires_at IS '保存到期时间';

COMMENT ON COLUMN artifacts.upload_expires_at IS '暂存到期时间';

COMMENT ON COLUMN artifacts.registered_at IS '登记完成时间';

CREATE INDEX ix_artifacts_0 ON artifacts (channel_id, id);

CREATE INDEX ix_artifacts_1 ON artifacts (channel_id, environment, state, upload_expires_at);

-- attempts：外部实际尝试。
CREATE TABLE attempts (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	run_id VARCHAR(64),
	step_id VARCHAR(64),
	kind VARCHAR(32),
	target_version_id VARCHAR(64),
	provider_credential_id VARCHAR(64),
	source_request_id VARCHAR(256),
	state VARCHAR(32),
	started_at TIMESTAMP WITH TIME ZONE,
	finished_at TIMESTAMP WITH TIME ZONE,
	error JSONB,
	usage_id VARCHAR(64),
	lease_version BIGINT,
	sent_at TIMESTAMP WITH TIME ZONE,
	retryable BOOLEAN
);

COMMENT ON TABLE attempts IS '外部实际尝试';

COMMENT ON COLUMN attempts.id IS '记录标识';

COMMENT ON COLUMN attempts.channel_id IS '所属渠道标识';

COMMENT ON COLUMN attempts.created_at IS '创建时间';

COMMENT ON COLUMN attempts.updated_at IS '更新时间';

COMMENT ON COLUMN attempts.revision IS '并发修订号';

COMMENT ON COLUMN attempts.environment IS '所属环境';

COMMENT ON COLUMN attempts.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN attempts.subject_type IS '业务主体类型';

COMMENT ON COLUMN attempts.subject_id IS '业务主体编号';

COMMENT ON COLUMN attempts.run_id IS '运行标识';

COMMENT ON COLUMN attempts.step_id IS '步骤标识';

COMMENT ON COLUMN attempts.kind IS '模型或工具类别';

COMMENT ON COLUMN attempts.target_version_id IS '实际依赖版本';

COMMENT ON COLUMN attempts.provider_credential_id IS '实际供应商凭据';

COMMENT ON COLUMN attempts.source_request_id IS '源请求标识';

COMMENT ON COLUMN attempts.state IS '尝试状态';

COMMENT ON COLUMN attempts.started_at IS '开始时间';

COMMENT ON COLUMN attempts.finished_at IS '结束时间';

COMMENT ON COLUMN attempts.error IS '脱敏错误';

COMMENT ON COLUMN attempts.usage_id IS '用量账本引用';

COMMENT ON COLUMN attempts.lease_version IS '调用所属租约代次';

COMMENT ON COLUMN attempts.sent_at IS '外部发送意图登记时间';

COMMENT ON COLUMN attempts.retryable IS '明确失败是否允许有限重试';

CREATE INDEX ix_attempts_0 ON attempts (channel_id, id);

CREATE INDEX ix_attempts_1 ON attempts (channel_id, run_id);

CREATE INDEX ix_attempts_2 ON attempts (channel_id, step_id, created_at);

-- audit_events：操作审计元数据。
CREATE TABLE audit_events (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	actor_id VARCHAR(128),
	action VARCHAR(128),
	target_type VARCHAR(64),
	target_id VARCHAR(128),
	request_id VARCHAR(64),
	outcome VARCHAR(32),
	summary JSONB
);

COMMENT ON TABLE audit_events IS '操作审计元数据';

COMMENT ON COLUMN audit_events.id IS '记录标识';

COMMENT ON COLUMN audit_events.channel_id IS '所属渠道标识';

COMMENT ON COLUMN audit_events.created_at IS '创建时间';

COMMENT ON COLUMN audit_events.updated_at IS '更新时间';

COMMENT ON COLUMN audit_events.revision IS '并发修订号';

COMMENT ON COLUMN audit_events.environment IS '所属环境';

COMMENT ON COLUMN audit_events.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN audit_events.subject_type IS '业务主体类型';

COMMENT ON COLUMN audit_events.subject_id IS '业务主体编号';

COMMENT ON COLUMN audit_events.actor_id IS '操作主体标识';

COMMENT ON COLUMN audit_events.action IS '操作名称';

COMMENT ON COLUMN audit_events.target_type IS '对象类型';

COMMENT ON COLUMN audit_events.target_id IS '对象标识';

COMMENT ON COLUMN audit_events.request_id IS '请求标识';

COMMENT ON COLUMN audit_events.outcome IS '操作结果';

COMMENT ON COLUMN audit_events.summary IS '脱敏变更摘要';

CREATE INDEX ix_audit_events_0 ON audit_events (channel_id, id);

CREATE INDEX ix_audit_events_1 ON audit_events (channel_id, created_at);

CREATE INDEX ix_audit_events_2 ON audit_events (channel_id, target_type, target_id);

-- automation_batches：批量运行受理批次。
CREATE TABLE automation_batches (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	name VARCHAR(128),
	request_digest VARCHAR(64),
	item_ids JSONB,
	owner_key VARCHAR(64),
	identity JSONB,
	state VARCHAR(32)
);

COMMENT ON TABLE automation_batches IS '批量运行受理批次';

COMMENT ON COLUMN automation_batches.id IS '记录标识';

COMMENT ON COLUMN automation_batches.channel_id IS '所属渠道标识';

COMMENT ON COLUMN automation_batches.created_at IS '创建时间';

COMMENT ON COLUMN automation_batches.updated_at IS '更新时间';

COMMENT ON COLUMN automation_batches.revision IS '并发修订号';

COMMENT ON COLUMN automation_batches.environment IS '所属环境';

COMMENT ON COLUMN automation_batches.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN automation_batches.subject_type IS '业务主体类型';

COMMENT ON COLUMN automation_batches.subject_id IS '业务主体编号';

COMMENT ON COLUMN automation_batches.name IS '批次名称';

COMMENT ON COLUMN automation_batches.request_digest IS '受理请求摘要';

COMMENT ON COLUMN automation_batches.item_ids IS '批次条目关联';

COMMENT ON COLUMN automation_batches.owner_key IS '执行身份摘要';

COMMENT ON COLUMN automation_batches.identity IS '原执行身份快照';

COMMENT ON COLUMN automation_batches.state IS '当前处理状态';

CREATE INDEX ix_automation_batches_0 ON automation_batches (channel_id, id);

CREATE INDEX ix_automation_batches_1 ON automation_batches (channel_id, environment, data_scope_id, subject_type, subject_id);

-- automation_items：逐项运行派发记录。
CREATE TABLE automation_items (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	batch_id VARCHAR(64),
	schedule_id VARCHAR(64),
	event_id VARCHAR(128),
	request_digest VARCHAR(64),
	request JSONB,
	owner_key VARCHAR(64),
	identity JSONB,
	state VARCHAR(32),
	lease_until TIMESTAMP WITH TIME ZONE,
	lease_nonce VARCHAR(64),
	attempts BIGINT,
	run_id VARCHAR(64),
	error JSONB
);

COMMENT ON TABLE automation_items IS '逐项运行派发记录';

COMMENT ON COLUMN automation_items.id IS '记录标识';

COMMENT ON COLUMN automation_items.channel_id IS '所属渠道标识';

COMMENT ON COLUMN automation_items.created_at IS '创建时间';

COMMENT ON COLUMN automation_items.updated_at IS '更新时间';

COMMENT ON COLUMN automation_items.revision IS '并发修订号';

COMMENT ON COLUMN automation_items.environment IS '所属环境';

COMMENT ON COLUMN automation_items.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN automation_items.subject_type IS '业务主体类型';

COMMENT ON COLUMN automation_items.subject_id IS '业务主体编号';

COMMENT ON COLUMN automation_items.batch_id IS '来源批次标识';

COMMENT ON COLUMN automation_items.schedule_id IS '来源计划标识';

COMMENT ON COLUMN automation_items.event_id IS '外部事件或窗口编号';

COMMENT ON COLUMN automation_items.request_digest IS '条目语义摘要';

COMMENT ON COLUMN automation_items.request IS '受理运行输入';

COMMENT ON COLUMN automation_items.owner_key IS '执行身份摘要';

COMMENT ON COLUMN automation_items.identity IS '原执行身份快照';

COMMENT ON COLUMN automation_items.state IS '当前处理状态';

COMMENT ON COLUMN automation_items.lease_until IS '派发租约到期';

COMMENT ON COLUMN automation_items.lease_nonce IS '派发租约凭据';

COMMENT ON COLUMN automation_items.attempts IS '受理尝试次数';

COMMENT ON COLUMN automation_items.run_id IS '关联运行标识';

COMMENT ON COLUMN automation_items.error IS '最近受理错误';

CREATE INDEX ix_automation_items_0 ON automation_items (channel_id, id);

CREATE INDEX ix_automation_items_1 ON automation_items (channel_id, environment, data_scope_id, subject_type, subject_id);

CREATE INDEX ix_automation_items_2 ON automation_items (channel_id, state, lease_until);

-- automation_schedules：通用运行定时计划。
CREATE TABLE automation_schedules (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	name VARCHAR(128),
	spec JSONB,
	owner_key VARCHAR(64),
	identity JSONB,
	state VARCHAR(32),
	next_at TIMESTAMP WITH TIME ZONE,
	last_error JSONB
);

COMMENT ON TABLE automation_schedules IS '通用运行定时计划';

COMMENT ON COLUMN automation_schedules.id IS '记录标识';

COMMENT ON COLUMN automation_schedules.channel_id IS '所属渠道标识';

COMMENT ON COLUMN automation_schedules.created_at IS '创建时间';

COMMENT ON COLUMN automation_schedules.updated_at IS '更新时间';

COMMENT ON COLUMN automation_schedules.revision IS '并发修订号';

COMMENT ON COLUMN automation_schedules.environment IS '所属环境';

COMMENT ON COLUMN automation_schedules.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN automation_schedules.subject_type IS '业务主体类型';

COMMENT ON COLUMN automation_schedules.subject_id IS '业务主体编号';

COMMENT ON COLUMN automation_schedules.name IS '计划名称';

COMMENT ON COLUMN automation_schedules.spec IS '周期与运行输入';

COMMENT ON COLUMN automation_schedules.owner_key IS '执行身份摘要';

COMMENT ON COLUMN automation_schedules.identity IS '原执行身份快照';

COMMENT ON COLUMN automation_schedules.state IS '当前处理状态';

COMMENT ON COLUMN automation_schedules.next_at IS '下次触发时间';

COMMENT ON COLUMN automation_schedules.last_error IS '最近派发错误';

CREATE INDEX ix_automation_schedules_0 ON automation_schedules (channel_id, id);

CREATE INDEX ix_automation_schedules_1 ON automation_schedules (channel_id, environment, data_scope_id, subject_type, subject_id);

CREATE INDEX ix_automation_schedules_2 ON automation_schedules (channel_id, state, next_at);

-- budget_alerts：预算阈值提醒。
CREATE TABLE budget_alerts (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	rule_id VARCHAR(64),
	period_start TIMESTAMP WITH TIME ZONE,
	threshold NUMERIC(12, 6),
	scope_key VARCHAR(64),
	status VARCHAR(32),
	first_triggered_at TIMESTAMP WITH TIME ZONE,
	resolved_at TIMESTAMP WITH TIME ZONE,
	transitions JSONB
);

COMMENT ON TABLE budget_alerts IS '预算阈值提醒';

COMMENT ON COLUMN budget_alerts.id IS '记录标识';

COMMENT ON COLUMN budget_alerts.channel_id IS '所属渠道标识';

COMMENT ON COLUMN budget_alerts.created_at IS '创建时间';

COMMENT ON COLUMN budget_alerts.updated_at IS '更新时间';

COMMENT ON COLUMN budget_alerts.revision IS '并发修订号';

COMMENT ON COLUMN budget_alerts.rule_id IS '规则标识';

COMMENT ON COLUMN budget_alerts.period_start IS '周期开始';

COMMENT ON COLUMN budget_alerts.threshold IS '触发阈值';

COMMENT ON COLUMN budget_alerts.scope_key IS '预算范围摘要';

COMMENT ON COLUMN budget_alerts.status IS '提醒状态';

COMMENT ON COLUMN budget_alerts.first_triggered_at IS '首次触发时间';

COMMENT ON COLUMN budget_alerts.resolved_at IS '解除时间';

COMMENT ON COLUMN budget_alerts.transitions IS '解除及再次触发轨迹';

CREATE INDEX ix_budget_alerts_0 ON budget_alerts (channel_id, id);

CREATE INDEX ix_budget_alerts_1 ON budget_alerts (channel_id, rule_id, period_start, threshold, scope_key);

-- budget_policies：预算策略。
CREATE TABLE budget_policies (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	scope_type VARCHAR(64),
	scope_id VARCHAR(128),
	period VARCHAR(32),
	timezone VARCHAR(64),
	currency VARCHAR(3),
	limit_value NUMERIC(24, 8),
	unit VARCHAR(32),
	mode VARCHAR(32),
	thresholds JSONB,
	status VARCHAR(32),
	name VARCHAR(128),
	version_id VARCHAR(64)
);

COMMENT ON TABLE budget_policies IS '预算策略';

COMMENT ON COLUMN budget_policies.id IS '记录标识';

COMMENT ON COLUMN budget_policies.channel_id IS '所属渠道标识';

COMMENT ON COLUMN budget_policies.created_at IS '创建时间';

COMMENT ON COLUMN budget_policies.updated_at IS '更新时间';

COMMENT ON COLUMN budget_policies.revision IS '并发修订号';

COMMENT ON COLUMN budget_policies.scope_type IS '预算对象类型';

COMMENT ON COLUMN budget_policies.scope_id IS '预算对象标识';

COMMENT ON COLUMN budget_policies.period IS '预算周期';

COMMENT ON COLUMN budget_policies.timezone IS '业务时区';

COMMENT ON COLUMN budget_policies.currency IS '币种';

COMMENT ON COLUMN budget_policies.limit_value IS '限额';

COMMENT ON COLUMN budget_policies.unit IS '金额或数量单位';

COMMENT ON COLUMN budget_policies.mode IS '控制模式';

COMMENT ON COLUMN budget_policies.thresholds IS '提醒阈值';

COMMENT ON COLUMN budget_policies.status IS '策略状态';

COMMENT ON COLUMN budget_policies.name IS '预算名称';

COMMENT ON COLUMN budget_policies.version_id IS '当前不可变策略版本';

CREATE INDEX ix_budget_policies_0 ON budget_policies (channel_id, id);

CREATE INDEX ix_budget_policies_1 ON budget_policies (channel_id, scope_type, scope_id);

-- budget_reservations：预算尝试预占。
CREATE TABLE budget_reservations (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	policy_id VARCHAR(64),
	policy_revision BIGINT,
	period_start TIMESTAMP WITH TIME ZONE,
	run_id VARCHAR(64),
	attempt_id VARCHAR(64),
	reserved_amount NUMERIC(24, 8),
	settled_amount NUMERIC(24, 8),
	currency VARCHAR(3),
	status VARCHAR(32),
	expires_at TIMESTAMP WITH TIME ZONE,
	policy_version_id VARCHAR(64),
	unit VARCHAR(32),
	scope_snapshot JSONB
);

COMMENT ON TABLE budget_reservations IS '预算尝试预占';

COMMENT ON COLUMN budget_reservations.id IS '记录标识';

COMMENT ON COLUMN budget_reservations.channel_id IS '所属渠道标识';

COMMENT ON COLUMN budget_reservations.created_at IS '创建时间';

COMMENT ON COLUMN budget_reservations.updated_at IS '更新时间';

COMMENT ON COLUMN budget_reservations.revision IS '并发修订号';

COMMENT ON COLUMN budget_reservations.policy_id IS '预算策略标识';

COMMENT ON COLUMN budget_reservations.policy_revision IS '策略修订';

COMMENT ON COLUMN budget_reservations.period_start IS '周期开始';

COMMENT ON COLUMN budget_reservations.run_id IS '运行标识';

COMMENT ON COLUMN budget_reservations.attempt_id IS '实际尝试标识';

COMMENT ON COLUMN budget_reservations.reserved_amount IS '预占数量或金额';

COMMENT ON COLUMN budget_reservations.settled_amount IS '结算数量或金额';

COMMENT ON COLUMN budget_reservations.currency IS '币种';

COMMENT ON COLUMN budget_reservations.status IS '预占状态';

COMMENT ON COLUMN budget_reservations.expires_at IS '待核查时间';

COMMENT ON COLUMN budget_reservations.policy_version_id IS '预占时固定的预算策略版本';

COMMENT ON COLUMN budget_reservations.unit IS '占用计量单位';

COMMENT ON COLUMN budget_reservations.scope_snapshot IS '预算命中对象与周期快照';

CREATE INDEX ix_budget_reservations_0 ON budget_reservations (channel_id, id);

CREATE INDEX ix_budget_reservations_1 ON budget_reservations (channel_id, policy_id, period_start);

CREATE INDEX ix_budget_reservations_2 ON budget_reservations (channel_id, attempt_id);

-- builtin_roles：内置角色。
CREATE TABLE builtin_roles (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	role_code VARCHAR(64),
	name VARCHAR(128),
	allowed_actions JSONB,
	grant_scope VARCHAR(32)
);

COMMENT ON TABLE builtin_roles IS '内置角色';

COMMENT ON COLUMN builtin_roles.id IS '记录标识';

COMMENT ON COLUMN builtin_roles.channel_id IS '所属渠道标识';

COMMENT ON COLUMN builtin_roles.created_at IS '创建时间';

COMMENT ON COLUMN builtin_roles.updated_at IS '更新时间';

COMMENT ON COLUMN builtin_roles.revision IS '并发修订号';

COMMENT ON COLUMN builtin_roles.role_code IS '角色编码';

COMMENT ON COLUMN builtin_roles.name IS '角色名称';

COMMENT ON COLUMN builtin_roles.allowed_actions IS '允许动作';

COMMENT ON COLUMN builtin_roles.grant_scope IS '授权类别';

CREATE INDEX ix_builtin_roles_0 ON builtin_roles (channel_id, id);

CREATE INDEX ix_builtin_roles_1 ON builtin_roles (channel_id, role_code);

-- channel_code_index：系统渠道的渠道编码定位索引。
CREATE TABLE channel_code_index (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	channel_code VARCHAR(64),
	target_channel_id VARCHAR(64)
);

COMMENT ON TABLE channel_code_index IS '系统渠道的渠道编码定位索引';

COMMENT ON COLUMN channel_code_index.id IS '记录标识';

COMMENT ON COLUMN channel_code_index.channel_id IS '所属渠道标识';

COMMENT ON COLUMN channel_code_index.created_at IS '创建时间';

COMMENT ON COLUMN channel_code_index.updated_at IS '更新时间';

COMMENT ON COLUMN channel_code_index.revision IS '并发修订号';

COMMENT ON COLUMN channel_code_index.channel_code IS '规范化渠道编码';

COMMENT ON COLUMN channel_code_index.target_channel_id IS '实际业务渠道标识';

CREATE INDEX ix_channel_code_index_0 ON channel_code_index (channel_id, id);

CREATE INDEX ix_channel_code_index_1 ON channel_code_index (channel_id, channel_code);

-- channel_environments：渠道环境。
CREATE TABLE channel_environments (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	name VARCHAR(128),
	status VARCHAR(32),
	release_policy JSONB,
	retention_policy JSONB
);

COMMENT ON TABLE channel_environments IS '渠道环境';

COMMENT ON COLUMN channel_environments.id IS '记录标识';

COMMENT ON COLUMN channel_environments.channel_id IS '所属渠道标识';

COMMENT ON COLUMN channel_environments.created_at IS '创建时间';

COMMENT ON COLUMN channel_environments.updated_at IS '更新时间';

COMMENT ON COLUMN channel_environments.revision IS '并发修订号';

COMMENT ON COLUMN channel_environments.environment IS '所属环境';

COMMENT ON COLUMN channel_environments.name IS '环境名称';

COMMENT ON COLUMN channel_environments.status IS '环境状态';

COMMENT ON COLUMN channel_environments.release_policy IS '发布策略';

COMMENT ON COLUMN channel_environments.retention_policy IS '保存策略';

CREATE INDEX ix_channel_environments_0 ON channel_environments (channel_id, id);

CREATE INDEX ix_channel_environments_1 ON channel_environments (channel_id, environment);

-- channel_keys：渠道接入密钥。
CREATE TABLE channel_keys (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	name VARCHAR(128),
	client_id VARCHAR(64),
	secret_digest VARCHAR(64),
	prefix VARCHAR(16),
	suffix VARCHAR(8),
	scopes JSONB,
	status VARCHAR(32),
	expires_at TIMESTAMP WITH TIME ZONE,
	last_used_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE channel_keys IS '渠道接入密钥';

COMMENT ON COLUMN channel_keys.id IS '记录标识';

COMMENT ON COLUMN channel_keys.channel_id IS '所属渠道标识';

COMMENT ON COLUMN channel_keys.created_at IS '创建时间';

COMMENT ON COLUMN channel_keys.updated_at IS '更新时间';

COMMENT ON COLUMN channel_keys.revision IS '并发修订号';

COMMENT ON COLUMN channel_keys.environment IS '所属环境';

COMMENT ON COLUMN channel_keys.name IS '密钥名称';

COMMENT ON COLUMN channel_keys.client_id IS '接入服务标识';

COMMENT ON COLUMN channel_keys.secret_digest IS '不可逆密钥摘要';

COMMENT ON COLUMN channel_keys.prefix IS '辨认前缀';

COMMENT ON COLUMN channel_keys.suffix IS '辨认末尾';

COMMENT ON COLUMN channel_keys.scopes IS '权限上限';

COMMENT ON COLUMN channel_keys.status IS '密钥状态';

COMMENT ON COLUMN channel_keys.expires_at IS '失效时间';

COMMENT ON COLUMN channel_keys.last_used_at IS '最近使用时间';

CREATE INDEX ix_channel_keys_0 ON channel_keys (channel_id, id);

CREATE INDEX ix_channel_keys_1 ON channel_keys (channel_id, client_id, status);

-- channel_lifecycle_events：渠道生命周期交接事件。
CREATE TABLE channel_lifecycle_events (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	event_type VARCHAR(64),
	target_type VARCHAR(64),
	target_id VARCHAR(64),
	environment VARCHAR(16),
	payload JSONB,
	acknowledgements JSONB
);

COMMENT ON TABLE channel_lifecycle_events IS '渠道生命周期交接事件';

COMMENT ON COLUMN channel_lifecycle_events.id IS '记录标识';

COMMENT ON COLUMN channel_lifecycle_events.channel_id IS '所属渠道标识';

COMMENT ON COLUMN channel_lifecycle_events.created_at IS '创建时间';

COMMENT ON COLUMN channel_lifecycle_events.updated_at IS '更新时间';

COMMENT ON COLUMN channel_lifecycle_events.revision IS '并发修订号';

COMMENT ON COLUMN channel_lifecycle_events.event_type IS '生命周期事件类型';

COMMENT ON COLUMN channel_lifecycle_events.target_type IS '变更对象类型';

COMMENT ON COLUMN channel_lifecycle_events.target_id IS '变更对象标识';

COMMENT ON COLUMN channel_lifecycle_events.environment IS '受影响环境';

COMMENT ON COLUMN channel_lifecycle_events.payload IS '变更事实与原始归属';

COMMENT ON COLUMN channel_lifecycle_events.acknowledgements IS '已处理模块及时间';

CREATE INDEX ix_channel_lifecycle_events_0 ON channel_lifecycle_events (channel_id, id);

CREATE INDEX ix_channel_lifecycle_events_1 ON channel_lifecycle_events (channel_id, created_at);

-- channel_memberships：渠道成员关系。
CREATE TABLE channel_memberships (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	user_id VARCHAR(64),
	roles JSONB,
	environments JSONB,
	data_scopes JSONB,
	status VARCHAR(32),
	granted_by VARCHAR(128)
);

COMMENT ON TABLE channel_memberships IS '渠道成员关系';

COMMENT ON COLUMN channel_memberships.id IS '记录标识';

COMMENT ON COLUMN channel_memberships.channel_id IS '所属渠道标识';

COMMENT ON COLUMN channel_memberships.created_at IS '创建时间';

COMMENT ON COLUMN channel_memberships.updated_at IS '更新时间';

COMMENT ON COLUMN channel_memberships.revision IS '并发修订号';

COMMENT ON COLUMN channel_memberships.user_id IS '平台账号标识';

COMMENT ON COLUMN channel_memberships.roles IS '角色清单';

COMMENT ON COLUMN channel_memberships.environments IS '授权环境';

COMMENT ON COLUMN channel_memberships.data_scopes IS '授权数据域';

COMMENT ON COLUMN channel_memberships.status IS '成员状态';

COMMENT ON COLUMN channel_memberships.granted_by IS '授权人标识';

CREATE INDEX ix_channel_memberships_0 ON channel_memberships (channel_id, id);

CREATE INDEX ix_channel_memberships_1 ON channel_memberships (channel_id, user_id);

-- channels：渠道主档。
CREATE TABLE channels (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	channel_code VARCHAR(64),
	name VARCHAR(128),
	status VARCHAR(32),
	owner VARCHAR(128),
	archived_at TIMESTAMP WITH TIME ZONE,
	retention_policy JSONB,
	budget_policy_refs JSONB,
	rate_limit_policy_refs JSONB,
	business_type VARCHAR(32)
);

COMMENT ON TABLE channels IS '渠道主档';

COMMENT ON COLUMN channels.id IS '记录标识';

COMMENT ON COLUMN channels.channel_id IS '所属渠道标识';

COMMENT ON COLUMN channels.created_at IS '创建时间';

COMMENT ON COLUMN channels.updated_at IS '更新时间';

COMMENT ON COLUMN channels.revision IS '并发修订号';

COMMENT ON COLUMN channels.channel_code IS '渠道稳定编码';

COMMENT ON COLUMN channels.name IS '渠道名称';

COMMENT ON COLUMN channels.status IS '渠道状态';

COMMENT ON COLUMN channels.owner IS '负责人名称';

COMMENT ON COLUMN channels.archived_at IS '归档时间';

COMMENT ON COLUMN channels.retention_policy IS '保存策略';

COMMENT ON COLUMN channels.budget_policy_refs IS '预算策略引用';

COMMENT ON COLUMN channels.rate_limit_policy_refs IS '限流策略引用';

COMMENT ON COLUMN channels.business_type IS '可选业务分类展示文本，历史分类原值保留';

CREATE INDEX ix_channels_0 ON channels (channel_id, id);

CREATE INDEX ix_channels_1 ON channels (channel_id, channel_code);

-- checkpoints：自有流程恢复点。
CREATE TABLE checkpoints (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	run_id VARCHAR(64),
	namespace VARCHAR(128),
	checkpoint_key VARCHAR(128),
	parent_key VARCHAR(128),
	lease_version BIGINT,
	release_snapshot_id VARCHAR(64),
	state_ref VARCHAR(64),
	metadata JSONB
);

COMMENT ON TABLE checkpoints IS '自有流程恢复点';

COMMENT ON COLUMN checkpoints.id IS '记录标识';

COMMENT ON COLUMN checkpoints.channel_id IS '所属渠道标识';

COMMENT ON COLUMN checkpoints.created_at IS '创建时间';

COMMENT ON COLUMN checkpoints.updated_at IS '更新时间';

COMMENT ON COLUMN checkpoints.revision IS '并发修订号';

COMMENT ON COLUMN checkpoints.environment IS '所属环境';

COMMENT ON COLUMN checkpoints.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN checkpoints.subject_type IS '业务主体类型';

COMMENT ON COLUMN checkpoints.subject_id IS '业务主体编号';

COMMENT ON COLUMN checkpoints.run_id IS '运行标识';

COMMENT ON COLUMN checkpoints.namespace IS '流程命名空间';

COMMENT ON COLUMN checkpoints.checkpoint_key IS '恢复点逻辑标识';

COMMENT ON COLUMN checkpoints.parent_key IS '父恢复点';

COMMENT ON COLUMN checkpoints.lease_version IS '提交租约代次';

COMMENT ON COLUMN checkpoints.release_snapshot_id IS '固定依赖快照';

COMMENT ON COLUMN checkpoints.state_ref IS '状态内容引用';

COMMENT ON COLUMN checkpoints.metadata IS '无原文恢复元数据';

CREATE INDEX ix_checkpoints_0 ON checkpoints (channel_id, id);

CREATE INDEX ix_checkpoints_1 ON checkpoints (channel_id, run_id, namespace, checkpoint_key);

-- context_snapshots：实际模型上下文快照。
CREATE TABLE context_snapshots (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	run_id VARCHAR(64),
	included_message_ids JSONB,
	summary_version VARCHAR(64),
	memory_refs JSONB,
	truncation JSONB,
	conversation_id VARCHAR(64),
	summary_id VARCHAR(64),
	summary_source_ids JSONB,
	policy_version VARCHAR(32),
	required_characters BIGINT
);

COMMENT ON TABLE context_snapshots IS '实际模型上下文快照';

COMMENT ON COLUMN context_snapshots.id IS '记录标识';

COMMENT ON COLUMN context_snapshots.channel_id IS '所属渠道标识';

COMMENT ON COLUMN context_snapshots.created_at IS '创建时间';

COMMENT ON COLUMN context_snapshots.updated_at IS '更新时间';

COMMENT ON COLUMN context_snapshots.revision IS '并发修订号';

COMMENT ON COLUMN context_snapshots.environment IS '所属环境';

COMMENT ON COLUMN context_snapshots.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN context_snapshots.subject_type IS '业务主体类型';

COMMENT ON COLUMN context_snapshots.subject_id IS '业务主体编号';

COMMENT ON COLUMN context_snapshots.run_id IS '运行标识';

COMMENT ON COLUMN context_snapshots.included_message_ids IS '实际包含消息';

COMMENT ON COLUMN context_snapshots.summary_version IS '摘要版本';

COMMENT ON COLUMN context_snapshots.memory_refs IS '记忆具体版本';

COMMENT ON COLUMN context_snapshots.truncation IS '删减原因及范围';

COMMENT ON COLUMN context_snapshots.conversation_id IS '所属会话';

COMMENT ON COLUMN context_snapshots.summary_id IS '引用摘要标识';

COMMENT ON COLUMN context_snapshots.summary_source_ids IS '摘要来源消息集合';

COMMENT ON COLUMN context_snapshots.policy_version IS '上下文选择策略版本';

COMMENT ON COLUMN context_snapshots.required_characters IS '必要指令和当前任务字符数';

CREATE INDEX ix_context_snapshots_0 ON context_snapshots (channel_id, id);

CREATE INDEX ix_context_snapshots_1 ON context_snapshots (channel_id, run_id);

-- conversation_summaries：可溯源会话摘要。
CREATE TABLE conversation_summaries (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	conversation_id VARCHAR(64),
	source_message_ids JSONB,
	version BIGINT,
	content TEXT,
	status VARCHAR(32),
	truncation JSONB,
	generation_run_id VARCHAR(64)
);

COMMENT ON TABLE conversation_summaries IS '可溯源会话摘要';

COMMENT ON COLUMN conversation_summaries.id IS '记录标识';

COMMENT ON COLUMN conversation_summaries.channel_id IS '所属渠道标识';

COMMENT ON COLUMN conversation_summaries.created_at IS '创建时间';

COMMENT ON COLUMN conversation_summaries.updated_at IS '更新时间';

COMMENT ON COLUMN conversation_summaries.revision IS '并发修订号';

COMMENT ON COLUMN conversation_summaries.environment IS '所属环境';

COMMENT ON COLUMN conversation_summaries.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN conversation_summaries.subject_type IS '业务主体类型';

COMMENT ON COLUMN conversation_summaries.subject_id IS '业务主体编号';

COMMENT ON COLUMN conversation_summaries.conversation_id IS '会话标识';

COMMENT ON COLUMN conversation_summaries.source_message_ids IS '来源消息集合';

COMMENT ON COLUMN conversation_summaries.version IS '摘要版本';

COMMENT ON COLUMN conversation_summaries.content IS '摘要内容';

COMMENT ON COLUMN conversation_summaries.status IS '摘要有效状态';

COMMENT ON COLUMN conversation_summaries.truncation IS '摘要删减记录';

COMMENT ON COLUMN conversation_summaries.generation_run_id IS '受控摘要生成运行';

CREATE INDEX ix_conversation_summaries_0 ON conversation_summaries (channel_id, id);

CREATE INDEX ix_conversation_summaries_1 ON conversation_summaries (channel_id, conversation_id, version);

-- conversation_turns：会话轮次与消息幂等。
CREATE TABLE conversation_turns (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	conversation_id VARCHAR(64),
	client_message_id VARCHAR(128),
	request_digest VARCHAR(64),
	user_message_id VARCHAR(64),
	run_id VARCHAR(64),
	sequence BIGINT,
	assistant_message_id VARCHAR(64),
	agent_version_id VARCHAR(64),
	version_label VARCHAR(128),
	input JSONB,
	input_schema JSONB,
	output_schema JSONB,
	source_run_id VARCHAR(64),
	confirmed_conditions JSONB
);

COMMENT ON TABLE conversation_turns IS '会话轮次与消息幂等';

COMMENT ON COLUMN conversation_turns.id IS '记录标识';

COMMENT ON COLUMN conversation_turns.channel_id IS '所属渠道标识';

COMMENT ON COLUMN conversation_turns.created_at IS '创建时间';

COMMENT ON COLUMN conversation_turns.updated_at IS '更新时间';

COMMENT ON COLUMN conversation_turns.revision IS '并发修订号';

COMMENT ON COLUMN conversation_turns.environment IS '所属环境';

COMMENT ON COLUMN conversation_turns.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN conversation_turns.subject_type IS '业务主体类型';

COMMENT ON COLUMN conversation_turns.subject_id IS '业务主体编号';

COMMENT ON COLUMN conversation_turns.conversation_id IS '会话标识';

COMMENT ON COLUMN conversation_turns.client_message_id IS '客户端消息标识';

COMMENT ON COLUMN conversation_turns.request_digest IS '语义请求摘要';

COMMENT ON COLUMN conversation_turns.user_message_id IS '用户消息标识';

COMMENT ON COLUMN conversation_turns.run_id IS '运行标识';

COMMENT ON COLUMN conversation_turns.sequence IS '会话内顺序';

COMMENT ON COLUMN conversation_turns.assistant_message_id IS '助手消息标识';

COMMENT ON COLUMN conversation_turns.agent_version_id IS '本轮冻结智能体版本';

COMMENT ON COLUMN conversation_turns.version_label IS '本轮版本名称';

COMMENT ON COLUMN conversation_turns.input IS '不可变业务输入';

COMMENT ON COLUMN conversation_turns.input_schema IS '本轮输入契约';

COMMENT ON COLUMN conversation_turns.output_schema IS '本轮输出契约';

COMMENT ON COLUMN conversation_turns.source_run_id IS '普通追问来源运行';

COMMENT ON COLUMN conversation_turns.confirmed_conditions IS '上一轮已确认条件';

CREATE INDEX ix_conversation_turns_0 ON conversation_turns (channel_id, id);

CREATE INDEX ix_conversation_turns_1 ON conversation_turns (channel_id, conversation_id, client_message_id);

CREATE INDEX ix_conversation_turns_2 ON conversation_turns (channel_id, conversation_id, sequence);

-- conversations：业务会话。
CREATE TABLE conversations (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	agent_id VARCHAR(64),
	title VARCHAR(255),
	status VARCHAR(32),
	active_run_id VARCHAR(64),
	expires_at TIMESTAMP WITH TIME ZONE,
	agent_code VARCHAR(128),
	agent_name VARCHAR(128),
	subject_name VARCHAR(128),
	input_schema JSONB,
	next_sequence BIGINT,
	next_turn_sequence BIGINT
);

COMMENT ON TABLE conversations IS '业务会话';

COMMENT ON COLUMN conversations.id IS '记录标识';

COMMENT ON COLUMN conversations.channel_id IS '所属渠道标识';

COMMENT ON COLUMN conversations.created_at IS '创建时间';

COMMENT ON COLUMN conversations.updated_at IS '更新时间';

COMMENT ON COLUMN conversations.revision IS '并发修订号';

COMMENT ON COLUMN conversations.environment IS '所属环境';

COMMENT ON COLUMN conversations.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN conversations.subject_type IS '业务主体类型';

COMMENT ON COLUMN conversations.subject_id IS '业务主体编号';

COMMENT ON COLUMN conversations.agent_id IS '智能体标识';

COMMENT ON COLUMN conversations.title IS '会话标题';

COMMENT ON COLUMN conversations.status IS '会话状态';

COMMENT ON COLUMN conversations.active_run_id IS '当前生成运行';

COMMENT ON COLUMN conversations.expires_at IS '保留到期时间';

COMMENT ON COLUMN conversations.agent_code IS '智能体调用编码';

COMMENT ON COLUMN conversations.agent_name IS '智能体名称';

COMMENT ON COLUMN conversations.subject_name IS '主体名称';

COMMENT ON COLUMN conversations.input_schema IS '已接受的输入契约';

COMMENT ON COLUMN conversations.next_sequence IS '下一条消息顺序';

COMMENT ON COLUMN conversations.next_turn_sequence IS '下一轮顺序';

CREATE INDEX ix_conversations_0 ON conversations (channel_id, id);

CREATE INDEX ix_conversations_1 ON conversations (channel_id, environment, data_scope_id, subject_type, subject_id, updated_at);

CREATE INDEX ix_conversations_2 ON conversations (channel_id, environment, data_scope_id, created_at, id);

-- credentials：加密服务凭据。
CREATE TABLE credentials (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	purpose VARCHAR(32),
	ciphertext BYTEA,
	key_version VARCHAR(64),
	state VARCHAR(32)
);

COMMENT ON TABLE credentials IS '加密服务凭据';

COMMENT ON COLUMN credentials.id IS '记录标识';

COMMENT ON COLUMN credentials.channel_id IS '所属渠道标识';

COMMENT ON COLUMN credentials.created_at IS '创建时间';

COMMENT ON COLUMN credentials.updated_at IS '更新时间';

COMMENT ON COLUMN credentials.revision IS '并发修订号';

COMMENT ON COLUMN credentials.environment IS '所属环境';

COMMENT ON COLUMN credentials.purpose IS '凭据用途';

COMMENT ON COLUMN credentials.ciphertext IS '认证加密密文';

COMMENT ON COLUMN credentials.key_version IS '加密密钥版本';

COMMENT ON COLUMN credentials.state IS '凭据状态';

CREATE INDEX ix_credentials_0 ON credentials (channel_id, id);

CREATE INDEX ix_credentials_1 ON credentials (channel_id, environment, purpose, state);

-- custom_roles：渠道自定义角色。
CREATE TABLE custom_roles (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	name VARCHAR(128),
	allowed_actions JSONB,
	state VARCHAR(32)
);

COMMENT ON TABLE custom_roles IS '渠道自定义角色';

COMMENT ON COLUMN custom_roles.id IS '记录标识';

COMMENT ON COLUMN custom_roles.channel_id IS '所属渠道标识';

COMMENT ON COLUMN custom_roles.created_at IS '创建时间';

COMMENT ON COLUMN custom_roles.updated_at IS '更新时间';

COMMENT ON COLUMN custom_roles.revision IS '并发修订号';

COMMENT ON COLUMN custom_roles.name IS '角色名称';

COMMENT ON COLUMN custom_roles.allowed_actions IS '角色动作上限';

COMMENT ON COLUMN custom_roles.state IS '启停状态';

CREATE INDEX ix_custom_roles_0 ON custom_roles (channel_id, id);

-- data_scopes：业务数据域映射。
CREATE TABLE data_scopes (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	name VARCHAR(128),
	external_scope_type VARCHAR(64),
	external_scope_id VARCHAR(128),
	status VARCHAR(32)
);

COMMENT ON TABLE data_scopes IS '业务数据域映射';

COMMENT ON COLUMN data_scopes.id IS '记录标识';

COMMENT ON COLUMN data_scopes.channel_id IS '所属渠道标识';

COMMENT ON COLUMN data_scopes.created_at IS '创建时间';

COMMENT ON COLUMN data_scopes.updated_at IS '更新时间';

COMMENT ON COLUMN data_scopes.revision IS '并发修订号';

COMMENT ON COLUMN data_scopes.environment IS '所属环境';

COMMENT ON COLUMN data_scopes.name IS '数据域名称';

COMMENT ON COLUMN data_scopes.external_scope_type IS '显式配置的外部数据域类型';

COMMENT ON COLUMN data_scopes.external_scope_id IS '显式配置的外部数据域编号';

COMMENT ON COLUMN data_scopes.status IS '数据域状态';

CREATE INDEX ix_data_scopes_0 ON data_scopes (channel_id, id);

CREATE INDEX ix_data_scopes_1 ON data_scopes (channel_id, environment, external_scope_type, external_scope_id);

-- delegation_keys：委托签名验证密钥。
CREATE TABLE delegation_keys (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	client_id VARCHAR(64),
	key_reference VARCHAR(64),
	algorithm VARCHAR(32),
	issuer VARCHAR(128),
	audience VARCHAR(128),
	max_ttl_seconds INTEGER,
	clock_skew_seconds INTEGER,
	status VARCHAR(32),
	not_before TIMESTAMP WITH TIME ZONE,
	expires_at TIMESTAMP WITH TIME ZONE,
	rotated_from VARCHAR(64)
);

COMMENT ON TABLE delegation_keys IS '委托签名验证密钥';

COMMENT ON COLUMN delegation_keys.id IS '记录标识';

COMMENT ON COLUMN delegation_keys.channel_id IS '所属渠道标识';

COMMENT ON COLUMN delegation_keys.created_at IS '创建时间';

COMMENT ON COLUMN delegation_keys.updated_at IS '更新时间';

COMMENT ON COLUMN delegation_keys.revision IS '并发修订号';

COMMENT ON COLUMN delegation_keys.environment IS '所属环境';

COMMENT ON COLUMN delegation_keys.client_id IS '接入服务标识';

COMMENT ON COLUMN delegation_keys.key_reference IS '独立签名密文引用';

COMMENT ON COLUMN delegation_keys.algorithm IS '固定签名算法';

COMMENT ON COLUMN delegation_keys.issuer IS '可信签发者';

COMMENT ON COLUMN delegation_keys.audience IS '委托受众';

COMMENT ON COLUMN delegation_keys.max_ttl_seconds IS '最长委托有效秒数';

COMMENT ON COLUMN delegation_keys.clock_skew_seconds IS '允许时钟偏差秒数';

COMMENT ON COLUMN delegation_keys.status IS '密钥状态';

COMMENT ON COLUMN delegation_keys.not_before IS '密钥生效时间';

COMMENT ON COLUMN delegation_keys.expires_at IS '密钥到期时间';

COMMENT ON COLUMN delegation_keys.rotated_from IS '轮换前密钥编号';

CREATE INDEX ix_delegation_keys_0 ON delegation_keys (channel_id, id);

CREATE INDEX ix_delegation_keys_1 ON delegation_keys (channel_id, environment, client_id);

-- delegation_nonces：已验证业务委托与请求防重放。
CREATE TABLE delegation_nonces (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	client_id VARCHAR(64),
	kid VARCHAR(64),
	nonce_digest VARCHAR(64),
	request_digest VARCHAR(64),
	claims_digest VARCHAR(64),
	claims JSONB,
	resolved_scope JSONB,
	expires_at TIMESTAMP WITH TIME ZONE,
	retain_until TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE delegation_nonces IS '已验证业务委托与请求防重放';

COMMENT ON COLUMN delegation_nonces.id IS '记录标识';

COMMENT ON COLUMN delegation_nonces.channel_id IS '所属渠道标识';

COMMENT ON COLUMN delegation_nonces.created_at IS '创建时间';

COMMENT ON COLUMN delegation_nonces.updated_at IS '更新时间';

COMMENT ON COLUMN delegation_nonces.revision IS '并发修订号';

COMMENT ON COLUMN delegation_nonces.environment IS '所属环境';

COMMENT ON COLUMN delegation_nonces.client_id IS '稳定接入服务标识';

COMMENT ON COLUMN delegation_nonces.kid IS '验签密钥编号';

COMMENT ON COLUMN delegation_nonces.nonce_digest IS '随机数摘要';

COMMENT ON COLUMN delegation_nonces.request_digest IS '实际请求绑定摘要';

COMMENT ON COLUMN delegation_nonces.claims_digest IS '完整委托声明摘要';

COMMENT ON COLUMN delegation_nonces.claims IS '验签后权限声明';

COMMENT ON COLUMN delegation_nonces.resolved_scope IS '验签后渠道数据域主体';

COMMENT ON COLUMN delegation_nonces.expires_at IS '防重放声明有效时间';

COMMENT ON COLUMN delegation_nonces.retain_until IS '防重放记录最早清理时间';

CREATE INDEX ix_delegation_nonces_0 ON delegation_nonces (channel_id, id);

CREATE INDEX ix_delegation_nonces_1 ON delegation_nonces (channel_id, environment, client_id, nonce_digest);

CREATE INDEX ix_delegation_nonces_2 ON delegation_nonces (channel_id, environment, retain_until);

-- deletion_jobs：删除传播任务。
CREATE TABLE deletion_jobs (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	marker_id VARCHAR(64),
	scope_description JSONB,
	affected_resources JSONB,
	state VARCHAR(32),
	completed_at TIMESTAMP WITH TIME ZONE,
	conversation_id VARCHAR(64)
);

COMMENT ON TABLE deletion_jobs IS '删除传播任务';

COMMENT ON COLUMN deletion_jobs.id IS '记录标识';

COMMENT ON COLUMN deletion_jobs.channel_id IS '所属渠道标识';

COMMENT ON COLUMN deletion_jobs.created_at IS '创建时间';

COMMENT ON COLUMN deletion_jobs.updated_at IS '更新时间';

COMMENT ON COLUMN deletion_jobs.revision IS '并发修订号';

COMMENT ON COLUMN deletion_jobs.environment IS '所属环境';

COMMENT ON COLUMN deletion_jobs.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN deletion_jobs.subject_type IS '业务主体类型';

COMMENT ON COLUMN deletion_jobs.subject_id IS '业务主体编号';

COMMENT ON COLUMN deletion_jobs.marker_id IS '删除标记';

COMMENT ON COLUMN deletion_jobs.scope_description IS '待清理范围元数据';

COMMENT ON COLUMN deletion_jobs.affected_resources IS '影响引用清单';

COMMENT ON COLUMN deletion_jobs.state IS '清理状态';

COMMENT ON COLUMN deletion_jobs.completed_at IS '完成时间';

COMMENT ON COLUMN deletion_jobs.conversation_id IS '删除目标会话';

CREATE INDEX ix_deletion_jobs_0 ON deletion_jobs (channel_id, id);

CREATE INDEX ix_deletion_jobs_1 ON deletion_jobs (channel_id, state, created_at);

CREATE INDEX ix_deletion_jobs_2 ON deletion_jobs (channel_id, conversation_id);

-- deletion_markers：不可恢复使用的删除标记。
CREATE TABLE deletion_markers (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	target_type VARCHAR(64),
	target_id VARCHAR(128),
	reason_code VARCHAR(64),
	requested_by VARCHAR(128)
);

COMMENT ON TABLE deletion_markers IS '不可恢复使用的删除标记';

COMMENT ON COLUMN deletion_markers.id IS '记录标识';

COMMENT ON COLUMN deletion_markers.channel_id IS '所属渠道标识';

COMMENT ON COLUMN deletion_markers.created_at IS '创建时间';

COMMENT ON COLUMN deletion_markers.updated_at IS '更新时间';

COMMENT ON COLUMN deletion_markers.revision IS '并发修订号';

COMMENT ON COLUMN deletion_markers.environment IS '所属环境';

COMMENT ON COLUMN deletion_markers.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN deletion_markers.subject_type IS '业务主体类型';

COMMENT ON COLUMN deletion_markers.subject_id IS '业务主体编号';

COMMENT ON COLUMN deletion_markers.target_type IS '删除对象类型';

COMMENT ON COLUMN deletion_markers.target_id IS '删除对象标识';

COMMENT ON COLUMN deletion_markers.reason_code IS '删除原因类别';

COMMENT ON COLUMN deletion_markers.requested_by IS '删除申请主体';

CREATE INDEX ix_deletion_markers_0 ON deletion_markers (channel_id, id);

CREATE INDEX ix_deletion_markers_1 ON deletion_markers (channel_id, environment, target_type, target_id);

-- deletion_receipts：删除清理完成证明。
CREATE TABLE deletion_receipts (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	job_id VARCHAR(64),
	marker_digest VARCHAR(64),
	counts JSONB,
	proof_digest VARCHAR(64),
	completed_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE deletion_receipts IS '删除清理完成证明';

COMMENT ON COLUMN deletion_receipts.id IS '记录标识';

COMMENT ON COLUMN deletion_receipts.channel_id IS '所属渠道标识';

COMMENT ON COLUMN deletion_receipts.created_at IS '创建时间';

COMMENT ON COLUMN deletion_receipts.updated_at IS '更新时间';

COMMENT ON COLUMN deletion_receipts.revision IS '并发修订号';

COMMENT ON COLUMN deletion_receipts.environment IS '所属环境';

COMMENT ON COLUMN deletion_receipts.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN deletion_receipts.subject_type IS '业务主体类型';

COMMENT ON COLUMN deletion_receipts.subject_id IS '业务主体编号';

COMMENT ON COLUMN deletion_receipts.job_id IS '删除任务';

COMMENT ON COLUMN deletion_receipts.marker_digest IS '删除清单摘要';

COMMENT ON COLUMN deletion_receipts.counts IS '各类已完成数量';

COMMENT ON COLUMN deletion_receipts.proof_digest IS '完成证明摘要';

COMMENT ON COLUMN deletion_receipts.completed_at IS '完成时间';

CREATE INDEX ix_deletion_receipts_0 ON deletion_receipts (channel_id, id);

CREATE INDEX ix_deletion_receipts_1 ON deletion_receipts (channel_id, job_id);

-- deletion_work_items：可重试模块清理步骤。
CREATE TABLE deletion_work_items (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	job_id VARCHAR(64),
	handler_key VARCHAR(128),
	target_type VARCHAR(64),
	target_id VARCHAR(128),
	state VARCHAR(32),
	attempts INTEGER,
	next_attempt_at TIMESTAMP WITH TIME ZONE,
	last_error VARCHAR(64),
	lease_token VARCHAR(64),
	lease_until TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE deletion_work_items IS '可重试模块清理步骤';

COMMENT ON COLUMN deletion_work_items.id IS '记录标识';

COMMENT ON COLUMN deletion_work_items.channel_id IS '所属渠道标识';

COMMENT ON COLUMN deletion_work_items.created_at IS '创建时间';

COMMENT ON COLUMN deletion_work_items.updated_at IS '更新时间';

COMMENT ON COLUMN deletion_work_items.revision IS '并发修订号';

COMMENT ON COLUMN deletion_work_items.environment IS '所属环境';

COMMENT ON COLUMN deletion_work_items.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN deletion_work_items.subject_type IS '业务主体类型';

COMMENT ON COLUMN deletion_work_items.subject_id IS '业务主体编号';

COMMENT ON COLUMN deletion_work_items.job_id IS '删除任务';

COMMENT ON COLUMN deletion_work_items.handler_key IS '登记处理器';

COMMENT ON COLUMN deletion_work_items.target_type IS '对象类型';

COMMENT ON COLUMN deletion_work_items.target_id IS '对象标识';

COMMENT ON COLUMN deletion_work_items.state IS '步骤状态';

COMMENT ON COLUMN deletion_work_items.attempts IS '尝试次数';

COMMENT ON COLUMN deletion_work_items.next_attempt_at IS '重试时间';

COMMENT ON COLUMN deletion_work_items.last_error IS '脱敏错误类别';

COMMENT ON COLUMN deletion_work_items.lease_token IS '执行租约令牌';

COMMENT ON COLUMN deletion_work_items.lease_until IS '执行租约到期时间';

COMMENT ON COLUMN deletion_work_items.completed_at IS '完成时间';

CREATE INDEX ix_deletion_work_items_0 ON deletion_work_items (channel_id, id);

CREATE INDEX ix_deletion_work_items_1 ON deletion_work_items (channel_id, job_id, handler_key, target_id);

CREATE INDEX ix_deletion_work_items_2 ON deletion_work_items (channel_id, state, next_attempt_at);

-- dispatch_outbox：可靠调度投递意图。
CREATE TABLE dispatch_outbox (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	run_id VARCHAR(64),
	state VARCHAR(32),
	dispatch_attempts INTEGER,
	next_attempt_at TIMESTAMP WITH TIME ZONE,
	last_error VARCHAR(64),
	delivery_version BIGINT
);

COMMENT ON TABLE dispatch_outbox IS '可靠调度投递意图';

COMMENT ON COLUMN dispatch_outbox.id IS '记录标识';

COMMENT ON COLUMN dispatch_outbox.channel_id IS '所属渠道标识';

COMMENT ON COLUMN dispatch_outbox.created_at IS '创建时间';

COMMENT ON COLUMN dispatch_outbox.updated_at IS '更新时间';

COMMENT ON COLUMN dispatch_outbox.revision IS '并发修订号';

COMMENT ON COLUMN dispatch_outbox.environment IS '所属环境';

COMMENT ON COLUMN dispatch_outbox.run_id IS '运行标识';

COMMENT ON COLUMN dispatch_outbox.state IS '投递状态';

COMMENT ON COLUMN dispatch_outbox.dispatch_attempts IS '投递次数';

COMMENT ON COLUMN dispatch_outbox.next_attempt_at IS '下次补偿时间';

COMMENT ON COLUMN dispatch_outbox.last_error IS '脱敏错误类别';

COMMENT ON COLUMN dispatch_outbox.delivery_version IS '本次投递声明代次';

CREATE INDEX ix_dispatch_outbox_0 ON dispatch_outbox (channel_id, id);

CREATE INDEX ix_dispatch_outbox_1 ON dispatch_outbox (channel_id, state, next_attempt_at);

-- evaluation_cases：不可变评测样本。
CREATE TABLE evaluation_cases (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	dataset_id VARCHAR(64),
	case_key VARCHAR(128),
	title VARCHAR(255),
	payload JSONB,
	fixture_id VARCHAR(64),
	previous_case_id VARCHAR(64),
	invalidated BOOLEAN
);

COMMENT ON TABLE evaluation_cases IS '不可变评测样本';

COMMENT ON COLUMN evaluation_cases.id IS '记录标识';

COMMENT ON COLUMN evaluation_cases.channel_id IS '所属渠道标识';

COMMENT ON COLUMN evaluation_cases.created_at IS '创建时间';

COMMENT ON COLUMN evaluation_cases.updated_at IS '更新时间';

COMMENT ON COLUMN evaluation_cases.revision IS '并发修订号';

COMMENT ON COLUMN evaluation_cases.environment IS '所属环境';

COMMENT ON COLUMN evaluation_cases.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN evaluation_cases.subject_type IS '业务主体类型';

COMMENT ON COLUMN evaluation_cases.subject_id IS '业务主体编号';

COMMENT ON COLUMN evaluation_cases.dataset_id IS '所属样本集';

COMMENT ON COLUMN evaluation_cases.case_key IS '跨版本样本定位键';

COMMENT ON COLUMN evaluation_cases.title IS '样本标题';

COMMENT ON COLUMN evaluation_cases.payload IS '输入、断言、标签、人工结论和来源';

COMMENT ON COLUMN evaluation_cases.fixture_id IS '固定工具数据引用';

COMMENT ON COLUMN evaluation_cases.previous_case_id IS '修改前样本引用';

COMMENT ON COLUMN evaluation_cases.invalidated IS '来源已失效';

CREATE INDEX ix_evaluation_cases_0 ON evaluation_cases (channel_id, id);

CREATE INDEX ix_evaluation_cases_1 ON evaluation_cases (channel_id, dataset_id);

-- evaluation_dataset_versions：不可变评测样本及标签版本。
CREATE TABLE evaluation_dataset_versions (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	dataset_id VARCHAR(64),
	version_label VARCHAR(128),
	content_digest VARCHAR(64),
	case_ids JSONB,
	reference_versions JSONB,
	captured_at TIMESTAMP WITH TIME ZONE,
	reference_digests JSONB
);

COMMENT ON TABLE evaluation_dataset_versions IS '不可变评测样本及标签版本';

COMMENT ON COLUMN evaluation_dataset_versions.id IS '记录标识';

COMMENT ON COLUMN evaluation_dataset_versions.channel_id IS '所属渠道标识';

COMMENT ON COLUMN evaluation_dataset_versions.created_at IS '创建时间';

COMMENT ON COLUMN evaluation_dataset_versions.updated_at IS '更新时间';

COMMENT ON COLUMN evaluation_dataset_versions.revision IS '并发修订号';

COMMENT ON COLUMN evaluation_dataset_versions.environment IS '所属环境';

COMMENT ON COLUMN evaluation_dataset_versions.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN evaluation_dataset_versions.subject_type IS '业务主体类型';

COMMENT ON COLUMN evaluation_dataset_versions.subject_id IS '业务主体编号';

COMMENT ON COLUMN evaluation_dataset_versions.dataset_id IS '所属样本集';

COMMENT ON COLUMN evaluation_dataset_versions.version_label IS '版本名称';

COMMENT ON COLUMN evaluation_dataset_versions.content_digest IS '数据和标签摘要';

COMMENT ON COLUMN evaluation_dataset_versions.case_ids IS '固定样本清单';

COMMENT ON COLUMN evaluation_dataset_versions.reference_versions IS '参考资料版本';

COMMENT ON COLUMN evaluation_dataset_versions.captured_at IS '固定数据时间';

COMMENT ON COLUMN evaluation_dataset_versions.reference_digests IS '参考资料版本内容摘要';

CREATE INDEX ix_evaluation_dataset_versions_0 ON evaluation_dataset_versions (channel_id, id);

CREATE INDEX ix_evaluation_dataset_versions_1 ON evaluation_dataset_versions (channel_id, dataset_id);

-- evaluation_datasets：评测样本集。
CREATE TABLE evaluation_datasets (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	name VARCHAR(128),
	scenario VARCHAR(64),
	owner VARCHAR(128),
	applicability TEXT,
	current_version_id VARCHAR(64)
);

COMMENT ON TABLE evaluation_datasets IS '评测样本集';

COMMENT ON COLUMN evaluation_datasets.id IS '记录标识';

COMMENT ON COLUMN evaluation_datasets.channel_id IS '所属渠道标识';

COMMENT ON COLUMN evaluation_datasets.created_at IS '创建时间';

COMMENT ON COLUMN evaluation_datasets.updated_at IS '更新时间';

COMMENT ON COLUMN evaluation_datasets.revision IS '并发修订号';

COMMENT ON COLUMN evaluation_datasets.environment IS '所属环境';

COMMENT ON COLUMN evaluation_datasets.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN evaluation_datasets.subject_type IS '业务主体类型';

COMMENT ON COLUMN evaluation_datasets.subject_id IS '业务主体编号';

COMMENT ON COLUMN evaluation_datasets.name IS '样本集名称';

COMMENT ON COLUMN evaluation_datasets.scenario IS '适用能力类别';

COMMENT ON COLUMN evaluation_datasets.owner IS '负责人';

COMMENT ON COLUMN evaluation_datasets.applicability IS '适用范围';

COMMENT ON COLUMN evaluation_datasets.current_version_id IS '当前样本版本';

CREATE INDEX ix_evaluation_datasets_0 ON evaluation_datasets (channel_id, id);

-- evaluation_fixtures：评测工具夹具。
CREATE TABLE evaluation_fixtures (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	payload JSONB,
	captured_at TIMESTAMP WITH TIME ZONE,
	invalidated BOOLEAN
);

COMMENT ON TABLE evaluation_fixtures IS '评测工具夹具';

COMMENT ON COLUMN evaluation_fixtures.id IS '记录标识';

COMMENT ON COLUMN evaluation_fixtures.channel_id IS '所属渠道标识';

COMMENT ON COLUMN evaluation_fixtures.created_at IS '创建时间';

COMMENT ON COLUMN evaluation_fixtures.updated_at IS '更新时间';

COMMENT ON COLUMN evaluation_fixtures.revision IS '并发修订号';

COMMENT ON COLUMN evaluation_fixtures.environment IS '所属环境';

COMMENT ON COLUMN evaluation_fixtures.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN evaluation_fixtures.subject_type IS '业务主体类型';

COMMENT ON COLUMN evaluation_fixtures.subject_id IS '业务主体编号';

COMMENT ON COLUMN evaluation_fixtures.payload IS '按工具版本及参数匹配的固定结果';

COMMENT ON COLUMN evaluation_fixtures.captured_at IS '夹具采集时间';

COMMENT ON COLUMN evaluation_fixtures.invalidated IS '夹具来源已失效';

CREATE INDEX ix_evaluation_fixtures_0 ON evaluation_fixtures (channel_id, id);

-- evaluation_reports：可追溯评测报告。
CREATE TABLE evaluation_reports (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	evaluation_id VARCHAR(64),
	report_digest VARCHAR(64),
	payload JSONB,
	reproducible BOOLEAN
);

COMMENT ON TABLE evaluation_reports IS '可追溯评测报告';

COMMENT ON COLUMN evaluation_reports.id IS '记录标识';

COMMENT ON COLUMN evaluation_reports.channel_id IS '所属渠道标识';

COMMENT ON COLUMN evaluation_reports.created_at IS '创建时间';

COMMENT ON COLUMN evaluation_reports.updated_at IS '更新时间';

COMMENT ON COLUMN evaluation_reports.revision IS '并发修订号';

COMMENT ON COLUMN evaluation_reports.environment IS '所属环境';

COMMENT ON COLUMN evaluation_reports.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN evaluation_reports.subject_type IS '业务主体类型';

COMMENT ON COLUMN evaluation_reports.subject_id IS '业务主体编号';

COMMENT ON COLUMN evaluation_reports.evaluation_id IS '所属评测';

COMMENT ON COLUMN evaluation_reports.report_digest IS '报告证据摘要';

COMMENT ON COLUMN evaluation_reports.payload IS '覆盖、差异、阻断、用量与耗时';

COMMENT ON COLUMN evaluation_reports.reproducible IS '来源和固定数据可复现';

CREATE INDEX ix_evaluation_reports_0 ON evaluation_reports (channel_id, id);

CREATE INDEX ix_evaluation_reports_1 ON evaluation_reports (channel_id, evaluation_id);

-- evaluation_results：评测单例及重跑记录。
CREATE TABLE evaluation_results (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	evaluation_id VARCHAR(64),
	case_id VARCHAR(64),
	candidate_id VARCHAR(64),
	attempt_number INTEGER,
	run_id VARCHAR(64),
	state VARCHAR(32),
	judgment JSONB,
	human_label JSONB,
	claimed_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE evaluation_results IS '评测单例及重跑记录';

COMMENT ON COLUMN evaluation_results.id IS '记录标识';

COMMENT ON COLUMN evaluation_results.channel_id IS '所属渠道标识';

COMMENT ON COLUMN evaluation_results.created_at IS '创建时间';

COMMENT ON COLUMN evaluation_results.updated_at IS '更新时间';

COMMENT ON COLUMN evaluation_results.revision IS '并发修订号';

COMMENT ON COLUMN evaluation_results.environment IS '所属环境';

COMMENT ON COLUMN evaluation_results.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN evaluation_results.subject_type IS '业务主体类型';

COMMENT ON COLUMN evaluation_results.subject_id IS '业务主体编号';

COMMENT ON COLUMN evaluation_results.evaluation_id IS '所属评测';

COMMENT ON COLUMN evaluation_results.case_id IS '固定样本';

COMMENT ON COLUMN evaluation_results.candidate_id IS '冻结候选';

COMMENT ON COLUMN evaluation_results.attempt_number IS '样本重跑序号';

COMMENT ON COLUMN evaluation_results.run_id IS '统一运行';

COMMENT ON COLUMN evaluation_results.state IS '单例状态';

COMMENT ON COLUMN evaluation_results.judgment IS '确定性与语义判定';

COMMENT ON COLUMN evaluation_results.human_label IS '独立人工结论';

COMMENT ON COLUMN evaluation_results.claimed_at IS '派发占位时间';

CREATE INDEX ix_evaluation_results_0 ON evaluation_results (channel_id, id);

CREATE INDEX ix_evaluation_results_1 ON evaluation_results (channel_id, evaluation_id);

CREATE INDEX ix_evaluation_results_2 ON evaluation_results (channel_id, run_id);

-- evaluations：批量评测调度任务。
CREATE TABLE evaluations (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	name VARCHAR(128),
	dataset_version_id VARCHAR(64),
	dataset_digest VARCHAR(64),
	candidate_snapshots JSONB,
	baseline_evaluation_id VARCHAR(64),
	baseline_candidate_id VARCHAR(64),
	execution_mode VARCHAR(32),
	config JSONB,
	identity JSONB,
	state VARCHAR(32),
	human_review JSONB,
	expires_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE evaluations IS '批量评测调度任务';

COMMENT ON COLUMN evaluations.id IS '记录标识';

COMMENT ON COLUMN evaluations.channel_id IS '所属渠道标识';

COMMENT ON COLUMN evaluations.created_at IS '创建时间';

COMMENT ON COLUMN evaluations.updated_at IS '更新时间';

COMMENT ON COLUMN evaluations.revision IS '并发修订号';

COMMENT ON COLUMN evaluations.environment IS '所属环境';

COMMENT ON COLUMN evaluations.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN evaluations.subject_type IS '业务主体类型';

COMMENT ON COLUMN evaluations.subject_id IS '业务主体编号';

COMMENT ON COLUMN evaluations.name IS '任务名称';

COMMENT ON COLUMN evaluations.dataset_version_id IS '固定样本版本';

COMMENT ON COLUMN evaluations.dataset_digest IS '固定数据和标签摘要';

COMMENT ON COLUMN evaluations.candidate_snapshots IS '冻结候选及完整依赖清单';

COMMENT ON COLUMN evaluations.baseline_evaluation_id IS '历史基线评测';

COMMENT ON COLUMN evaluations.baseline_candidate_id IS '基线候选';

COMMENT ON COLUMN evaluations.execution_mode IS '工具数据执行模式';

COMMENT ON COLUMN evaluations.config IS '并发、预算、阈值和发布评测配置';

COMMENT ON COLUMN evaluations.identity IS '原始调用身份';

COMMENT ON COLUMN evaluations.state IS '调度状态';

COMMENT ON COLUMN evaluations.human_review IS '独立报告人工审阅';

COMMENT ON COLUMN evaluations.expires_at IS '发布证据有效期';

CREATE INDEX ix_evaluations_0 ON evaluations (channel_id, id);

CREATE INDEX ix_evaluations_1 ON evaluations (channel_id, state);

-- evidence_refs：证据定位与授权范围。
CREATE TABLE evidence_refs (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	source_type VARCHAR(64),
	source_id VARCHAR(128),
	source_version VARCHAR(128),
	observed_at TIMESTAMP WITH TIME ZONE,
	location JSONB,
	title VARCHAR(255),
	artifact_id VARCHAR(64),
	authorization_scope JSONB
);

COMMENT ON TABLE evidence_refs IS '证据定位与授权范围';

COMMENT ON COLUMN evidence_refs.id IS '记录标识';

COMMENT ON COLUMN evidence_refs.channel_id IS '所属渠道标识';

COMMENT ON COLUMN evidence_refs.created_at IS '创建时间';

COMMENT ON COLUMN evidence_refs.updated_at IS '更新时间';

COMMENT ON COLUMN evidence_refs.revision IS '并发修订号';

COMMENT ON COLUMN evidence_refs.environment IS '所属环境';

COMMENT ON COLUMN evidence_refs.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN evidence_refs.subject_type IS '业务主体类型';

COMMENT ON COLUMN evidence_refs.subject_id IS '业务主体编号';

COMMENT ON COLUMN evidence_refs.source_type IS '来源类型';

COMMENT ON COLUMN evidence_refs.source_id IS '来源标识';

COMMENT ON COLUMN evidence_refs.source_version IS '来源版本';

COMMENT ON COLUMN evidence_refs.observed_at IS '观测时间';

COMMENT ON COLUMN evidence_refs.location IS '字段路径或文本位置';

COMMENT ON COLUMN evidence_refs.title IS '授权范围内的来源名称';

COMMENT ON COLUMN evidence_refs.artifact_id IS '内容产物标识';

COMMENT ON COLUMN evidence_refs.authorization_scope IS '授权范围摘要';

CREATE INDEX ix_evidence_refs_0 ON evidence_refs (channel_id, id);

CREATE INDEX ix_evidence_refs_1 ON evidence_refs (channel_id, source_type, source_id);

-- iam_revocations：认证撤销补偿记录。
CREATE TABLE iam_revocations (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	kind VARCHAR(32),
	target_id VARCHAR(128),
	cutoff_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE iam_revocations IS '认证撤销补偿记录';

COMMENT ON COLUMN iam_revocations.id IS '记录标识';

COMMENT ON COLUMN iam_revocations.channel_id IS '所属渠道标识';

COMMENT ON COLUMN iam_revocations.created_at IS '创建时间';

COMMENT ON COLUMN iam_revocations.updated_at IS '更新时间';

COMMENT ON COLUMN iam_revocations.revision IS '并发修订号';

COMMENT ON COLUMN iam_revocations.kind IS '撤销索引类别';

COMMENT ON COLUMN iam_revocations.target_id IS '撤销对象标识或令牌摘要';

COMMENT ON COLUMN iam_revocations.cutoff_at IS '撤销签发时间上界';

COMMENT ON COLUMN iam_revocations.completed_at IS '缓存补偿完成时间';

CREATE INDEX ix_iam_revocations_0 ON iam_revocations (channel_id, id);

CREATE INDEX ix_iam_revocations_1 ON iam_revocations (channel_id, kind, target_id);

CREATE INDEX ix_iam_revocations_2 ON iam_revocations (completed_at);

-- integration_tests：接入契约验证。
CREATE TABLE integration_tests (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	integration_id VARCHAR(64),
	config_revision BIGINT,
	cases JSONB,
	results JSONB,
	capabilities JSONB,
	state VARCHAR(32)
);

COMMENT ON TABLE integration_tests IS '接入契约验证';

COMMENT ON COLUMN integration_tests.id IS '记录标识';

COMMENT ON COLUMN integration_tests.channel_id IS '所属渠道标识';

COMMENT ON COLUMN integration_tests.created_at IS '创建时间';

COMMENT ON COLUMN integration_tests.updated_at IS '更新时间';

COMMENT ON COLUMN integration_tests.revision IS '并发修订号';

COMMENT ON COLUMN integration_tests.environment IS '所属环境';

COMMENT ON COLUMN integration_tests.data_scope_id IS '所属业务数据域';

COMMENT ON COLUMN integration_tests.integration_id IS '连接标识';

COMMENT ON COLUMN integration_tests.config_revision IS '测试对应配置修订';

COMMENT ON COLUMN integration_tests.cases IS '验证能力清单';

COMMENT ON COLUMN integration_tests.results IS '脱敏验证结论';

COMMENT ON COLUMN integration_tests.capabilities IS '通过验证的能力';

COMMENT ON COLUMN integration_tests.state IS '验证状态';

CREATE INDEX ix_integration_tests_0 ON integration_tests (channel_id, id);

CREATE INDEX ix_integration_tests_1 ON integration_tests (channel_id, environment, data_scope_id, integration_id);

-- integrations：业务系统适配连接。
CREATE TABLE integrations (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	name VARCHAR(128),
	adapter_code VARCHAR(64),
	adapter_version VARCHAR(64),
	business_endpoint VARCHAR(2048),
	credential_ref VARCHAR(64),
	allowed_operations JSONB,
	operation_paths JSONB,
	field_mapping JSONB,
	scope_mapping_ref VARCHAR(64),
	health VARCHAR(32),
	contract_version VARCHAR(64),
	status VARCHAR(32)
);

COMMENT ON TABLE integrations IS '业务系统适配连接';

COMMENT ON COLUMN integrations.id IS '记录标识';

COMMENT ON COLUMN integrations.channel_id IS '所属渠道标识';

COMMENT ON COLUMN integrations.created_at IS '创建时间';

COMMENT ON COLUMN integrations.updated_at IS '更新时间';

COMMENT ON COLUMN integrations.revision IS '并发修订号';

COMMENT ON COLUMN integrations.environment IS '所属环境';

COMMENT ON COLUMN integrations.data_scope_id IS '所属业务数据域';

COMMENT ON COLUMN integrations.name IS '接入名称';

COMMENT ON COLUMN integrations.adapter_code IS '适配器编码';

COMMENT ON COLUMN integrations.adapter_version IS '适配器版本';

COMMENT ON COLUMN integrations.business_endpoint IS '源服务地址';

COMMENT ON COLUMN integrations.credential_ref IS '服务凭据引用';

COMMENT ON COLUMN integrations.allowed_operations IS '已授权能力';

COMMENT ON COLUMN integrations.operation_paths IS '固定能力接口路径';

COMMENT ON COLUMN integrations.field_mapping IS '源字段转换配置';

COMMENT ON COLUMN integrations.scope_mapping_ref IS '渠道数据域映射引用';

COMMENT ON COLUMN integrations.health IS '连接健康状态';

COMMENT ON COLUMN integrations.contract_version IS '标准契约版本';

COMMENT ON COLUMN integrations.status IS '启用状态';

CREATE INDEX ix_integrations_0 ON integrations (channel_id, id);

CREATE INDEX ix_integrations_1 ON integrations (channel_id, environment, data_scope_id, status);

-- key_identity_index：系统渠道密钥身份索引。
CREATE TABLE key_identity_index (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	key_lookup_digest VARCHAR(64),
	key_id VARCHAR(64),
	target_channel_id VARCHAR(64)
);

COMMENT ON TABLE key_identity_index IS '系统渠道密钥身份索引';

COMMENT ON COLUMN key_identity_index.id IS '记录标识';

COMMENT ON COLUMN key_identity_index.channel_id IS '所属渠道标识';

COMMENT ON COLUMN key_identity_index.created_at IS '创建时间';

COMMENT ON COLUMN key_identity_index.updated_at IS '更新时间';

COMMENT ON COLUMN key_identity_index.revision IS '并发修订号';

COMMENT ON COLUMN key_identity_index.key_lookup_digest IS '完整密钥不可逆摘要';

COMMENT ON COLUMN key_identity_index.key_id IS '目标密钥标识';

COMMENT ON COLUMN key_identity_index.target_channel_id IS '目标渠道标识';

CREATE INDEX ix_key_identity_index_0 ON key_identity_index (channel_id, id);

CREATE INDEX ix_key_identity_index_1 ON key_identity_index (channel_id, key_lookup_digest);

-- key_rotations：密钥轮换记录。
CREATE TABLE key_rotations (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	old_key_id VARCHAR(64),
	new_key_id VARCHAR(64),
	overlap_until TIMESTAMP WITH TIME ZONE,
	operator_id VARCHAR(128)
);

COMMENT ON TABLE key_rotations IS '密钥轮换记录';

COMMENT ON COLUMN key_rotations.id IS '记录标识';

COMMENT ON COLUMN key_rotations.channel_id IS '所属渠道标识';

COMMENT ON COLUMN key_rotations.created_at IS '创建时间';

COMMENT ON COLUMN key_rotations.updated_at IS '更新时间';

COMMENT ON COLUMN key_rotations.revision IS '并发修订号';

COMMENT ON COLUMN key_rotations.old_key_id IS '原密钥标识';

COMMENT ON COLUMN key_rotations.new_key_id IS '新密钥标识';

COMMENT ON COLUMN key_rotations.overlap_until IS '重叠截止时间';

COMMENT ON COLUMN key_rotations.operator_id IS '操作人标识';

CREATE INDEX ix_key_rotations_0 ON key_rotations (channel_id, id);

CREATE INDEX ix_key_rotations_1 ON key_rotations (channel_id, old_key_id);

-- mcp_checks：握手与健康检查。
CREATE TABLE mcp_checks (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	connection_id VARCHAR(64),
	negotiated_version VARCHAR(64),
	server_info JSONB,
	capabilities JSONB,
	health_status VARCHAR(32),
	latency_ms INTEGER,
	error_category VARCHAR(64),
	connection_revision BIGINT,
	operation VARCHAR(32)
);

COMMENT ON TABLE mcp_checks IS '握手与健康检查';

COMMENT ON COLUMN mcp_checks.id IS '记录标识';

COMMENT ON COLUMN mcp_checks.channel_id IS '所属渠道标识';

COMMENT ON COLUMN mcp_checks.created_at IS '创建时间';

COMMENT ON COLUMN mcp_checks.updated_at IS '更新时间';

COMMENT ON COLUMN mcp_checks.revision IS '并发修订号';

COMMENT ON COLUMN mcp_checks.environment IS '所属环境';

COMMENT ON COLUMN mcp_checks.connection_id IS '连接标识';

COMMENT ON COLUMN mcp_checks.negotiated_version IS '协商协议版本';

COMMENT ON COLUMN mcp_checks.server_info IS '远端信息';

COMMENT ON COLUMN mcp_checks.capabilities IS '协商能力';

COMMENT ON COLUMN mcp_checks.health_status IS '健康状态';

COMMENT ON COLUMN mcp_checks.latency_ms IS '耗时毫秒';

COMMENT ON COLUMN mcp_checks.error_category IS '错误类别';

COMMENT ON COLUMN mcp_checks.connection_revision IS '检查时连接配置修订';

COMMENT ON COLUMN mcp_checks.operation IS '检查操作类型';

CREATE INDEX ix_mcp_checks_0 ON mcp_checks (channel_id, id);

CREATE INDEX ix_mcp_checks_1 ON mcp_checks (channel_id, connection_id, created_at);

-- mcp_connections：远程工具连接。
CREATE TABLE mcp_connections (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	name VARCHAR(128),
	transport VARCHAR(64),
	endpoint VARCHAR(2048),
	credential_ref VARCHAR(64),
	timeouts JSONB,
	status VARCHAR(32),
	health_status VARCHAR(32),
	configuration_revision BIGINT,
	credential_revision BIGINT,
	tested_revision BIGINT,
	discovered_revision BIGINT,
	failure_count INTEGER,
	health_policy JSONB,
	last_check_at TIMESTAMP WITH TIME ZONE,
	auth_failed BOOLEAN,
	health_actor_id VARCHAR(64),
	health_data_scope_id VARCHAR(64),
	next_check_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE mcp_connections IS '远程工具连接';

COMMENT ON COLUMN mcp_connections.id IS '记录标识';

COMMENT ON COLUMN mcp_connections.channel_id IS '所属渠道标识';

COMMENT ON COLUMN mcp_connections.created_at IS '创建时间';

COMMENT ON COLUMN mcp_connections.updated_at IS '更新时间';

COMMENT ON COLUMN mcp_connections.revision IS '并发修订号';

COMMENT ON COLUMN mcp_connections.environment IS '所属环境';

COMMENT ON COLUMN mcp_connections.name IS '连接名称';

COMMENT ON COLUMN mcp_connections.transport IS '传输类型';

COMMENT ON COLUMN mcp_connections.endpoint IS '服务地址';

COMMENT ON COLUMN mcp_connections.credential_ref IS '凭据引用';

COMMENT ON COLUMN mcp_connections.timeouts IS '超时策略';

COMMENT ON COLUMN mcp_connections.status IS '启用状态';

COMMENT ON COLUMN mcp_connections.health_status IS '健康状态';

COMMENT ON COLUMN mcp_connections.configuration_revision IS '连接配置修订';

COMMENT ON COLUMN mcp_connections.credential_revision IS '凭据版本';

COMMENT ON COLUMN mcp_connections.tested_revision IS '握手通过的配置修订';

COMMENT ON COLUMN mcp_connections.discovered_revision IS '发现通过的配置修订';

COMMENT ON COLUMN mcp_connections.failure_count IS '连续失败次数';

COMMENT ON COLUMN mcp_connections.health_policy IS '检查频率与失败阈值';

COMMENT ON COLUMN mcp_connections.last_check_at IS '最近检查时间';

COMMENT ON COLUMN mcp_connections.auth_failed IS '凭据失效阻断状态';

COMMENT ON COLUMN mcp_connections.health_actor_id IS '健康检查授权成员';

COMMENT ON COLUMN mcp_connections.health_data_scope_id IS '健康检查授权数据域';

COMMENT ON COLUMN mcp_connections.next_check_at IS '下次健康检查时间';

CREATE INDEX ix_mcp_connections_0 ON mcp_connections (channel_id, id);

CREATE INDEX ix_mcp_connections_1 ON mcp_connections (channel_id, environment, status);

CREATE INDEX ix_mcp_connections_2 ON mcp_connections (channel_id, environment, next_check_at);

-- mcp_discoveries：远端工具发现快照。
CREATE TABLE mcp_discoveries (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	connection_id VARCHAR(64),
	connection_revision BIGINT,
	tool_definitions JSONB,
	schema_hashes JSONB,
	negotiated_version VARCHAR(64),
	credential_revision BIGINT
);

COMMENT ON TABLE mcp_discoveries IS '远端工具发现快照';

COMMENT ON COLUMN mcp_discoveries.id IS '记录标识';

COMMENT ON COLUMN mcp_discoveries.channel_id IS '所属渠道标识';

COMMENT ON COLUMN mcp_discoveries.created_at IS '创建时间';

COMMENT ON COLUMN mcp_discoveries.updated_at IS '更新时间';

COMMENT ON COLUMN mcp_discoveries.revision IS '并发修订号';

COMMENT ON COLUMN mcp_discoveries.environment IS '所属环境';

COMMENT ON COLUMN mcp_discoveries.connection_id IS '连接标识';

COMMENT ON COLUMN mcp_discoveries.connection_revision IS '连接修订';

COMMENT ON COLUMN mcp_discoveries.tool_definitions IS '远端工具定义';

COMMENT ON COLUMN mcp_discoveries.schema_hashes IS '定义摘要';

COMMENT ON COLUMN mcp_discoveries.negotiated_version IS '发现协商协议版本';

COMMENT ON COLUMN mcp_discoveries.credential_revision IS '发现时凭据版本';

CREATE INDEX ix_mcp_discoveries_0 ON mcp_discoveries (channel_id, id);

CREATE INDEX ix_mcp_discoveries_1 ON mcp_discoveries (channel_id, connection_id, created_at);

-- mcp_imports：远端工具导入映射。
CREATE TABLE mcp_imports (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	connection_id VARCHAR(64),
	discovery_id VARCHAR(64),
	remote_tool_name VARCHAR(256),
	local_tool_id VARCHAR(64),
	schema_hash VARCHAR(64),
	imported_version VARCHAR(64),
	effect_type VARCHAR(32),
	name VARCHAR(128),
	input_schema JSONB,
	contract_status VARCHAR(32)
);

COMMENT ON TABLE mcp_imports IS '远端工具导入映射';

COMMENT ON COLUMN mcp_imports.id IS '记录标识';

COMMENT ON COLUMN mcp_imports.channel_id IS '所属渠道标识';

COMMENT ON COLUMN mcp_imports.created_at IS '创建时间';

COMMENT ON COLUMN mcp_imports.updated_at IS '更新时间';

COMMENT ON COLUMN mcp_imports.revision IS '并发修订号';

COMMENT ON COLUMN mcp_imports.environment IS '所属环境';

COMMENT ON COLUMN mcp_imports.connection_id IS '连接标识';

COMMENT ON COLUMN mcp_imports.discovery_id IS '发现快照';

COMMENT ON COLUMN mcp_imports.remote_tool_name IS '远端名称';

COMMENT ON COLUMN mcp_imports.local_tool_id IS '本地工具标识';

COMMENT ON COLUMN mcp_imports.schema_hash IS '远端结构摘要';

COMMENT ON COLUMN mcp_imports.imported_version IS '本地导入版本';

COMMENT ON COLUMN mcp_imports.effect_type IS '管理员核定影响类型';

COMMENT ON COLUMN mcp_imports.name IS '本地工具显示名称';

COMMENT ON COLUMN mcp_imports.input_schema IS '固定本地输入契约';

COMMENT ON COLUMN mcp_imports.contract_status IS '固定契约可用状态';

CREATE INDEX ix_mcp_imports_0 ON mcp_imports (channel_id, id);

CREATE INDEX ix_mcp_imports_1 ON mcp_imports (channel_id, connection_id, remote_tool_name);

CREATE INDEX ix_mcp_imports_2 ON mcp_imports (channel_id, environment, connection_id, discovery_id, remote_tool_name);

-- mcp_oauth_flows：MCP 一次性授权流程。
CREATE TABLE mcp_oauth_flows (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	connection_id VARCHAR(64),
	configuration_revision BIGINT,
	profile_id VARCHAR(64),
	profile_digest VARCHAR(64),
	ownership VARCHAR(16),
	owner_id VARCHAR(64),
	state VARCHAR(16),
	verifier_ref VARCHAR(64),
	expires_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE mcp_oauth_flows IS 'MCP 一次性授权流程';

COMMENT ON COLUMN mcp_oauth_flows.id IS '记录标识';

COMMENT ON COLUMN mcp_oauth_flows.channel_id IS '所属渠道标识';

COMMENT ON COLUMN mcp_oauth_flows.created_at IS '创建时间';

COMMENT ON COLUMN mcp_oauth_flows.updated_at IS '更新时间';

COMMENT ON COLUMN mcp_oauth_flows.revision IS '并发修订号';

COMMENT ON COLUMN mcp_oauth_flows.environment IS '所属环境';

COMMENT ON COLUMN mcp_oauth_flows.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN mcp_oauth_flows.subject_type IS '业务主体类型';

COMMENT ON COLUMN mcp_oauth_flows.subject_id IS '业务主体编号';

COMMENT ON COLUMN mcp_oauth_flows.connection_id IS '连接标识';

COMMENT ON COLUMN mcp_oauth_flows.configuration_revision IS '连接配置修订';

COMMENT ON COLUMN mcp_oauth_flows.profile_id IS '身份提供方配置';

COMMENT ON COLUMN mcp_oauth_flows.profile_digest IS '提供方配置摘要';

COMMENT ON COLUMN mcp_oauth_flows.ownership IS '凭据归属类型';

COMMENT ON COLUMN mcp_oauth_flows.owner_id IS '归属身份摘要';

COMMENT ON COLUMN mcp_oauth_flows.state IS '授权流程状态';

COMMENT ON COLUMN mcp_oauth_flows.verifier_ref IS '加密验证凭据引用';

COMMENT ON COLUMN mcp_oauth_flows.expires_at IS '授权流程截止时间';

CREATE INDEX ix_mcp_oauth_flows_0 ON mcp_oauth_flows (channel_id, id);

CREATE INDEX ix_mcp_oauth_flows_1 ON mcp_oauth_flows (channel_id, environment, connection_id);

-- mcp_oauth_tokens：MCP 分身份委托凭据。
CREATE TABLE mcp_oauth_tokens (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	connection_id VARCHAR(64),
	profile_id VARCHAR(64),
	profile_digest VARCHAR(64),
	ownership VARCHAR(16),
	owner_id VARCHAR(64),
	credential_ref VARCHAR(64),
	expires_at TIMESTAMP WITH TIME ZONE,
	state VARCHAR(16),
	refresh_until TIMESTAMP WITH TIME ZONE,
	refresh_nonce VARCHAR(64),
	authorized_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE mcp_oauth_tokens IS 'MCP 分身份委托凭据';

COMMENT ON COLUMN mcp_oauth_tokens.id IS '记录标识';

COMMENT ON COLUMN mcp_oauth_tokens.channel_id IS '所属渠道标识';

COMMENT ON COLUMN mcp_oauth_tokens.created_at IS '创建时间';

COMMENT ON COLUMN mcp_oauth_tokens.updated_at IS '更新时间';

COMMENT ON COLUMN mcp_oauth_tokens.revision IS '并发修订号';

COMMENT ON COLUMN mcp_oauth_tokens.environment IS '所属环境';

COMMENT ON COLUMN mcp_oauth_tokens.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN mcp_oauth_tokens.subject_type IS '业务主体类型';

COMMENT ON COLUMN mcp_oauth_tokens.subject_id IS '业务主体编号';

COMMENT ON COLUMN mcp_oauth_tokens.connection_id IS '连接标识';

COMMENT ON COLUMN mcp_oauth_tokens.profile_id IS '身份提供方配置';

COMMENT ON COLUMN mcp_oauth_tokens.profile_digest IS '提供方配置摘要';

COMMENT ON COLUMN mcp_oauth_tokens.ownership IS '凭据归属类型';

COMMENT ON COLUMN mcp_oauth_tokens.owner_id IS '归属身份摘要';

COMMENT ON COLUMN mcp_oauth_tokens.credential_ref IS '加密委托令牌引用';

COMMENT ON COLUMN mcp_oauth_tokens.expires_at IS '访问令牌截止时间';

COMMENT ON COLUMN mcp_oauth_tokens.state IS '委托状态';

COMMENT ON COLUMN mcp_oauth_tokens.refresh_until IS '刷新占用截止时间';

COMMENT ON COLUMN mcp_oauth_tokens.refresh_nonce IS '刷新互斥代次';

COMMENT ON COLUMN mcp_oauth_tokens.authorized_at IS '授权发起时间';

CREATE INDEX ix_mcp_oauth_tokens_0 ON mcp_oauth_tokens (channel_id, id);

CREATE INDEX ix_mcp_oauth_tokens_1 ON mcp_oauth_tokens (channel_id, environment, connection_id);

-- memories：主体结构化记忆。
CREATE TABLE memories (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	memory_type VARCHAR(64),
	key VARCHAR(128),
	display_name VARCHAR(128),
	value JSONB,
	status VARCHAR(32),
	expires_at TIMESTAMP WITH TIME ZONE,
	current_version_id VARCHAR(64),
	confirmed BOOLEAN,
	observed_at TIMESTAMP WITH TIME ZONE,
	trust_level INTEGER,
	usage_count BIGINT,
	subject_name VARCHAR(255),
	source_mode VARCHAR(16)
);

COMMENT ON TABLE memories IS '主体结构化记忆';

COMMENT ON COLUMN memories.id IS '记录标识';

COMMENT ON COLUMN memories.channel_id IS '所属渠道标识';

COMMENT ON COLUMN memories.created_at IS '创建时间';

COMMENT ON COLUMN memories.updated_at IS '更新时间';

COMMENT ON COLUMN memories.revision IS '并发修订号';

COMMENT ON COLUMN memories.environment IS '所属环境';

COMMENT ON COLUMN memories.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN memories.subject_type IS '业务主体类型';

COMMENT ON COLUMN memories.subject_id IS '业务主体编号';

COMMENT ON COLUMN memories.memory_type IS '记忆类别';

COMMENT ON COLUMN memories.key IS '记忆属性名';

COMMENT ON COLUMN memories.display_name IS '属性中文名';

COMMENT ON COLUMN memories.value IS '记忆值';

COMMENT ON COLUMN memories.status IS '确认及有效状态';

COMMENT ON COLUMN memories.expires_at IS '有效截止时间';

COMMENT ON COLUMN memories.current_version_id IS '当前版本';

COMMENT ON COLUMN memories.confirmed IS '是否经过明确确认';

COMMENT ON COLUMN memories.observed_at IS '最新有效依据时间';

COMMENT ON COLUMN memories.trust_level IS '有效来源可信等级';

COMMENT ON COLUMN memories.usage_count IS '实际使用次数';

COMMENT ON COLUMN memories.subject_name IS '主体可读名称';

COMMENT ON COLUMN memories.source_mode IS '来源有效性模式：独立依据或全部依赖';

CREATE INDEX ix_memories_0 ON memories (channel_id, id);

CREATE INDEX ix_memories_1 ON memories (channel_id, environment, data_scope_id, subject_type, subject_id, key, status);

-- memory_consolidations：会话归档与人物画像后台整理任务。
CREATE TABLE memory_consolidations (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	conversation_id VARCHAR(128),
	source_run_id VARCHAR(128),
	source_message_ids JSONB,
	memory_ids JSONB,
	generation_run_id VARCHAR(128),
	state VARCHAR(32),
	attempt INTEGER,
	next_attempt_at TIMESTAMP WITH TIME ZONE,
	error_code VARCHAR(128),
	settings JSONB,
	preference_revision INTEGER
);

COMMENT ON TABLE memory_consolidations IS '会话归档与人物画像后台整理任务';

COMMENT ON COLUMN memory_consolidations.id IS '记录标识';

COMMENT ON COLUMN memory_consolidations.channel_id IS '所属渠道标识';

COMMENT ON COLUMN memory_consolidations.created_at IS '创建时间';

COMMENT ON COLUMN memory_consolidations.updated_at IS '更新时间';

COMMENT ON COLUMN memory_consolidations.revision IS '并发修订号';

COMMENT ON COLUMN memory_consolidations.environment IS '所属环境';

COMMENT ON COLUMN memory_consolidations.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN memory_consolidations.subject_type IS '业务主体类型';

COMMENT ON COLUMN memory_consolidations.subject_id IS '业务主体编号';

COMMENT ON COLUMN memory_consolidations.conversation_id IS '来源会话标识';

COMMENT ON COLUMN memory_consolidations.source_run_id IS '恢复授权与冻结策略的来源运行';

COMMENT ON COLUMN memory_consolidations.source_message_ids IS '本批完整消息引用';

COMMENT ON COLUMN memory_consolidations.memory_ids IS '已生成的归档及画像引用';

COMMENT ON COLUMN memory_consolidations.generation_run_id IS '后台生成运行标识';

COMMENT ON COLUMN memory_consolidations.state IS '后台整理状态';

COMMENT ON COLUMN memory_consolidations.attempt IS '生成轮次';

COMMENT ON COLUMN memory_consolidations.next_attempt_at IS '下次允许整理时间';

COMMENT ON COLUMN memory_consolidations.error_code IS '最后失败原因编码';

COMMENT ON COLUMN memory_consolidations.settings IS '冻结策略与属性，不包含会话原文';

COMMENT ON COLUMN memory_consolidations.preference_revision IS '受理时主体偏好修订';

CREATE INDEX ix_memory_consolidations_0 ON memory_consolidations (channel_id, id);

CREATE INDEX ix_memory_consolidations_1 ON memory_consolidations (channel_id, state, next_attempt_at);

CREATE INDEX ix_memory_consolidations_2 ON memory_consolidations (channel_id, conversation_id);

-- memory_deletion_jobs：主体记忆删除清理意图。
CREATE TABLE memory_deletion_jobs (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	memory_ids JSONB,
	state VARCHAR(32),
	completed_at TIMESTAMP WITH TIME ZONE,
	kind VARCHAR(16)
);

COMMENT ON TABLE memory_deletion_jobs IS '主体记忆删除清理意图';

COMMENT ON COLUMN memory_deletion_jobs.id IS '记录标识';

COMMENT ON COLUMN memory_deletion_jobs.channel_id IS '所属渠道标识';

COMMENT ON COLUMN memory_deletion_jobs.created_at IS '创建时间';

COMMENT ON COLUMN memory_deletion_jobs.updated_at IS '更新时间';

COMMENT ON COLUMN memory_deletion_jobs.revision IS '并发修订号';

COMMENT ON COLUMN memory_deletion_jobs.environment IS '所属环境';

COMMENT ON COLUMN memory_deletion_jobs.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN memory_deletion_jobs.subject_type IS '业务主体类型';

COMMENT ON COLUMN memory_deletion_jobs.subject_id IS '业务主体编号';

COMMENT ON COLUMN memory_deletion_jobs.memory_ids IS '待清理记忆标识集合';

COMMENT ON COLUMN memory_deletion_jobs.state IS '清理进度';

COMMENT ON COLUMN memory_deletion_jobs.completed_at IS '完成时间';

COMMENT ON COLUMN memory_deletion_jobs.kind IS '单项遗忘或主体清空';

CREATE INDEX ix_memory_deletion_jobs_0 ON memory_deletion_jobs (channel_id, id);

CREATE INDEX ix_memory_deletion_jobs_1 ON memory_deletion_jobs (channel_id, environment, data_scope_id, subject_type, subject_id, state);

-- memory_embeddings：按主体和模型版本隔离的记忆向量。
CREATE TABLE memory_embeddings (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	memory_id VARCHAR(64),
	memory_version_id VARCHAR(64),
	model_version_id VARCHAR(64),
	run_id VARCHAR(64),
	dimensions INTEGER,
	embedding JSONB
);

COMMENT ON TABLE memory_embeddings IS '按主体和模型版本隔离的记忆向量';

COMMENT ON COLUMN memory_embeddings.id IS '记录标识';

COMMENT ON COLUMN memory_embeddings.channel_id IS '所属渠道标识';

COMMENT ON COLUMN memory_embeddings.created_at IS '创建时间';

COMMENT ON COLUMN memory_embeddings.updated_at IS '更新时间';

COMMENT ON COLUMN memory_embeddings.revision IS '并发修订号';

COMMENT ON COLUMN memory_embeddings.environment IS '所属环境';

COMMENT ON COLUMN memory_embeddings.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN memory_embeddings.subject_type IS '业务主体类型';

COMMENT ON COLUMN memory_embeddings.subject_id IS '业务主体编号';

COMMENT ON COLUMN memory_embeddings.memory_id IS '来源记忆标识';

COMMENT ON COLUMN memory_embeddings.memory_version_id IS '来源记忆版本';

COMMENT ON COLUMN memory_embeddings.model_version_id IS '向量模型版本';

COMMENT ON COLUMN memory_embeddings.run_id IS '生成向量的运行';

COMMENT ON COLUMN memory_embeddings.dimensions IS '向量维度';

COMMENT ON COLUMN memory_embeddings.embedding IS '记忆向量';

CREATE INDEX ix_memory_embeddings_0 ON memory_embeddings (channel_id, id);

CREATE INDEX ix_memory_embeddings_1 ON memory_embeddings (channel_id, environment, data_scope_id, subject_type, subject_id, model_version_id);

CREATE INDEX ix_memory_embeddings_2 ON memory_embeddings (channel_id, memory_id);

-- memory_policies：渠道与智能体记忆策略。
CREATE TABLE memory_policies (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	agent_id VARCHAR(64),
	allowed_types JSONB,
	write_mode VARCHAR(32),
	ttl_seconds INTEGER,
	max_items INTEGER,
	retrieval_limit INTEGER,
	read_enabled BOOLEAN,
	suggest_enabled BOOLEAN,
	failure_mode VARCHAR(16),
	attributes JSONB,
	consolidation JSONB
);

COMMENT ON TABLE memory_policies IS '渠道与智能体记忆策略';

COMMENT ON COLUMN memory_policies.id IS '记录标识';

COMMENT ON COLUMN memory_policies.channel_id IS '所属渠道标识';

COMMENT ON COLUMN memory_policies.created_at IS '创建时间';

COMMENT ON COLUMN memory_policies.updated_at IS '更新时间';

COMMENT ON COLUMN memory_policies.revision IS '并发修订号';

COMMENT ON COLUMN memory_policies.agent_id IS '智能体标识';

COMMENT ON COLUMN memory_policies.allowed_types IS '允许类别';

COMMENT ON COLUMN memory_policies.write_mode IS '写入方式';

COMMENT ON COLUMN memory_policies.ttl_seconds IS '保留秒数';

COMMENT ON COLUMN memory_policies.max_items IS '存储条数上限';

COMMENT ON COLUMN memory_policies.retrieval_limit IS '召回条数上限';

COMMENT ON COLUMN memory_policies.read_enabled IS '是否允许读取';

COMMENT ON COLUMN memory_policies.suggest_enabled IS '是否允许建议写入';

COMMENT ON COLUMN memory_policies.failure_mode IS '读取故障处理方式';

COMMENT ON COLUMN memory_policies.attributes IS '渠道可配置的画像属性定义';

COMMENT ON COLUMN memory_policies.consolidation IS '后台归档与画像整理策略';

CREATE INDEX ix_memory_policies_0 ON memory_policies (channel_id, id);

CREATE INDEX ix_memory_policies_1 ON memory_policies (channel_id, agent_id);

-- memory_preferences：主体长期记忆开关。
CREATE TABLE memory_preferences (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	enabled BOOLEAN,
	changed_by VARCHAR(128)
);

COMMENT ON TABLE memory_preferences IS '主体长期记忆开关';

COMMENT ON COLUMN memory_preferences.id IS '记录标识';

COMMENT ON COLUMN memory_preferences.channel_id IS '所属渠道标识';

COMMENT ON COLUMN memory_preferences.created_at IS '创建时间';

COMMENT ON COLUMN memory_preferences.updated_at IS '更新时间';

COMMENT ON COLUMN memory_preferences.revision IS '并发修订号';

COMMENT ON COLUMN memory_preferences.environment IS '所属环境';

COMMENT ON COLUMN memory_preferences.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN memory_preferences.subject_type IS '业务主体类型';

COMMENT ON COLUMN memory_preferences.subject_id IS '业务主体编号';

COMMENT ON COLUMN memory_preferences.enabled IS '是否启用';

COMMENT ON COLUMN memory_preferences.changed_by IS '变更主体';

CREATE INDEX ix_memory_preferences_0 ON memory_preferences (channel_id, id);

CREATE INDEX ix_memory_preferences_1 ON memory_preferences (channel_id, environment, data_scope_id, subject_type, subject_id);

-- memory_retrievals：运行记忆召回记录。
CREATE TABLE memory_retrievals (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	run_id VARCHAR(64),
	memory_refs JSONB,
	selection_reason JSONB,
	warnings JSONB,
	agent_id VARCHAR(64)
);

COMMENT ON TABLE memory_retrievals IS '运行记忆召回记录';

COMMENT ON COLUMN memory_retrievals.id IS '记录标识';

COMMENT ON COLUMN memory_retrievals.channel_id IS '所属渠道标识';

COMMENT ON COLUMN memory_retrievals.created_at IS '创建时间';

COMMENT ON COLUMN memory_retrievals.updated_at IS '更新时间';

COMMENT ON COLUMN memory_retrievals.revision IS '并发修订号';

COMMENT ON COLUMN memory_retrievals.environment IS '所属环境';

COMMENT ON COLUMN memory_retrievals.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN memory_retrievals.subject_type IS '业务主体类型';

COMMENT ON COLUMN memory_retrievals.subject_id IS '业务主体编号';

COMMENT ON COLUMN memory_retrievals.run_id IS '运行标识';

COMMENT ON COLUMN memory_retrievals.memory_refs IS '使用的记忆版本';

COMMENT ON COLUMN memory_retrievals.selection_reason IS '选择依据';

COMMENT ON COLUMN memory_retrievals.warnings IS '脱敏降级提示';

COMMENT ON COLUMN memory_retrievals.agent_id IS '使用记忆的智能体';

CREATE INDEX ix_memory_retrievals_0 ON memory_retrievals (channel_id, id);

CREATE INDEX ix_memory_retrievals_1 ON memory_retrievals (channel_id, run_id);

-- memory_sources：记忆有效来源。
CREATE TABLE memory_sources (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	memory_id VARCHAR(64),
	source_type VARCHAR(64),
	source_id VARCHAR(128),
	source_version VARCHAR(128),
	observed_at TIMESTAMP WITH TIME ZONE,
	evidence_id VARCHAR(64),
	status VARCHAR(32),
	authority VARCHAR(32),
	trust_level INTEGER
);

COMMENT ON TABLE memory_sources IS '记忆有效来源';

COMMENT ON COLUMN memory_sources.id IS '记录标识';

COMMENT ON COLUMN memory_sources.channel_id IS '所属渠道标识';

COMMENT ON COLUMN memory_sources.created_at IS '创建时间';

COMMENT ON COLUMN memory_sources.updated_at IS '更新时间';

COMMENT ON COLUMN memory_sources.revision IS '并发修订号';

COMMENT ON COLUMN memory_sources.environment IS '所属环境';

COMMENT ON COLUMN memory_sources.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN memory_sources.subject_type IS '业务主体类型';

COMMENT ON COLUMN memory_sources.subject_id IS '业务主体编号';

COMMENT ON COLUMN memory_sources.memory_id IS '记忆标识';

COMMENT ON COLUMN memory_sources.source_type IS '来源类型';

COMMENT ON COLUMN memory_sources.source_id IS '来源标识';

COMMENT ON COLUMN memory_sources.source_version IS '来源版本';

COMMENT ON COLUMN memory_sources.observed_at IS '观测时间';

COMMENT ON COLUMN memory_sources.evidence_id IS '证据标识';

COMMENT ON COLUMN memory_sources.status IS '来源状态';

COMMENT ON COLUMN memory_sources.authority IS '来源权限类型';

COMMENT ON COLUMN memory_sources.trust_level IS '来源可信等级';

CREATE INDEX ix_memory_sources_0 ON memory_sources (channel_id, id);

CREATE INDEX ix_memory_sources_1 ON memory_sources (channel_id, memory_id);

CREATE INDEX ix_memory_sources_2 ON memory_sources (channel_id, environment, data_scope_id, subject_type, subject_id, source_type, source_id);

-- memory_versions：记忆变更版本。
CREATE TABLE memory_versions (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	memory_id VARCHAR(64),
	previous_version_id VARCHAR(64),
	value JSONB,
	changed_by VARCHAR(128),
	reason VARCHAR(512),
	version_number INTEGER,
	status VARCHAR(32),
	source_ids JSONB
);

COMMENT ON TABLE memory_versions IS '记忆变更版本';

COMMENT ON COLUMN memory_versions.id IS '记录标识';

COMMENT ON COLUMN memory_versions.channel_id IS '所属渠道标识';

COMMENT ON COLUMN memory_versions.created_at IS '创建时间';

COMMENT ON COLUMN memory_versions.updated_at IS '更新时间';

COMMENT ON COLUMN memory_versions.revision IS '并发修订号';

COMMENT ON COLUMN memory_versions.environment IS '所属环境';

COMMENT ON COLUMN memory_versions.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN memory_versions.subject_type IS '业务主体类型';

COMMENT ON COLUMN memory_versions.subject_id IS '业务主体编号';

COMMENT ON COLUMN memory_versions.memory_id IS '记忆标识';

COMMENT ON COLUMN memory_versions.previous_version_id IS '前版本';

COMMENT ON COLUMN memory_versions.value IS '历史内容空槽位，不保存原文';

COMMENT ON COLUMN memory_versions.changed_by IS '变更主体';

COMMENT ON COLUMN memory_versions.reason IS '变更原因';

COMMENT ON COLUMN memory_versions.version_number IS '版本序号';

COMMENT ON COLUMN memory_versions.status IS '变更后状态';

COMMENT ON COLUMN memory_versions.source_ids IS '有效来源记录集合';

CREATE INDEX ix_memory_versions_0 ON memory_versions (channel_id, id);

CREATE INDEX ix_memory_versions_1 ON memory_versions (channel_id, memory_id, created_at);

-- messages：会话消息。
CREATE TABLE messages (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	conversation_id VARCHAR(64),
	role VARCHAR(32),
	content_parts JSONB,
	run_id VARCHAR(64),
	status VARCHAR(32),
	sequence BIGINT,
	turn_id VARCHAR(64),
	event_sequence BIGINT
);

COMMENT ON TABLE messages IS '会话消息';

COMMENT ON COLUMN messages.id IS '记录标识';

COMMENT ON COLUMN messages.channel_id IS '所属渠道标识';

COMMENT ON COLUMN messages.created_at IS '创建时间';

COMMENT ON COLUMN messages.updated_at IS '更新时间';

COMMENT ON COLUMN messages.revision IS '并发修订号';

COMMENT ON COLUMN messages.environment IS '所属环境';

COMMENT ON COLUMN messages.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN messages.subject_type IS '业务主体类型';

COMMENT ON COLUMN messages.subject_id IS '业务主体编号';

COMMENT ON COLUMN messages.conversation_id IS '会话标识';

COMMENT ON COLUMN messages.role IS '消息角色';

COMMENT ON COLUMN messages.content_parts IS '文本及附件引用';

COMMENT ON COLUMN messages.run_id IS '关联运行';

COMMENT ON COLUMN messages.status IS '消息完成状态';

COMMENT ON COLUMN messages.sequence IS '会话消息顺序';

COMMENT ON COLUMN messages.turn_id IS '关联轮次';

COMMENT ON COLUMN messages.event_sequence IS '已投影运行事件顺序';

CREATE INDEX ix_messages_0 ON messages (channel_id, id);

CREATE INDEX ix_messages_1 ON messages (channel_id, conversation_id, created_at, id);

CREATE INDEX ix_messages_2 ON messages (channel_id, conversation_id, sequence);

-- model_connections：模型供应商连接。
CREATE TABLE model_connections (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	name VARCHAR(128),
	protocol VARCHAR(64),
	endpoint VARCHAR(2048),
	credential_ref VARCHAR(64),
	timeout_seconds INTEGER,
	status VARCHAR(32),
	health_status VARCHAR(32),
	provider_id VARCHAR(64),
	current_version_id VARCHAR(64),
	health_reason VARCHAR(512),
	health_checked_at TIMESTAMP WITH TIME ZONE,
	validation_revision BIGINT
);

COMMENT ON TABLE model_connections IS '模型供应商连接';

COMMENT ON COLUMN model_connections.id IS '记录标识';

COMMENT ON COLUMN model_connections.channel_id IS '所属渠道标识';

COMMENT ON COLUMN model_connections.created_at IS '创建时间';

COMMENT ON COLUMN model_connections.updated_at IS '更新时间';

COMMENT ON COLUMN model_connections.revision IS '并发修订号';

COMMENT ON COLUMN model_connections.environment IS '所属环境';

COMMENT ON COLUMN model_connections.name IS '连接名称';

COMMENT ON COLUMN model_connections.protocol IS '协议类型';

COMMENT ON COLUMN model_connections.endpoint IS '服务地址';

COMMENT ON COLUMN model_connections.credential_ref IS '凭据标识';

COMMENT ON COLUMN model_connections.timeout_seconds IS '调用超时秒数';

COMMENT ON COLUMN model_connections.status IS '连接启用状态';

COMMENT ON COLUMN model_connections.health_status IS '最近健康状态';

COMMENT ON COLUMN model_connections.provider_id IS '供应商字典引用';

COMMENT ON COLUMN model_connections.current_version_id IS '当前连接版本标识';

COMMENT ON COLUMN model_connections.health_reason IS '最近健康异常原因';

COMMENT ON COLUMN model_connections.health_checked_at IS '最近健康检查时间';

COMMENT ON COLUMN model_connections.validation_revision IS '能力验证语义修订号';

CREATE INDEX ix_model_connections_0 ON model_connections (channel_id, id);

CREATE INDEX ix_model_connections_1 ON model_connections (channel_id, environment, status);

-- model_routes：模型路由资源。
CREATE TABLE model_routes (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	code VARCHAR(64),
	name VARCHAR(128),
	status VARCHAR(32)
);

COMMENT ON TABLE model_routes IS '模型路由资源';

COMMENT ON COLUMN model_routes.id IS '记录标识';

COMMENT ON COLUMN model_routes.channel_id IS '所属渠道标识';

COMMENT ON COLUMN model_routes.created_at IS '创建时间';

COMMENT ON COLUMN model_routes.updated_at IS '更新时间';

COMMENT ON COLUMN model_routes.revision IS '并发修订号';

COMMENT ON COLUMN model_routes.code IS '路由编码';

COMMENT ON COLUMN model_routes.name IS '路由名称';

COMMENT ON COLUMN model_routes.status IS '启用状态';

CREATE INDEX ix_model_routes_0 ON model_routes (channel_id, id);

CREATE INDEX ix_model_routes_1 ON model_routes (channel_id, code);

-- model_tests：模型验证记录。
CREATE TABLE model_tests (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	model_id VARCHAR(64),
	config_revision BIGINT,
	cases JSONB,
	results JSONB,
	latency_ms INTEGER,
	attempt_ids JSONB,
	config_digest VARCHAR(64),
	execution JSONB,
	state VARCHAR(32),
	run_id VARCHAR(64),
	error_code VARCHAR(64),
	reason VARCHAR(512)
);

COMMENT ON TABLE model_tests IS '模型验证记录';

COMMENT ON COLUMN model_tests.id IS '记录标识';

COMMENT ON COLUMN model_tests.channel_id IS '所属渠道标识';

COMMENT ON COLUMN model_tests.created_at IS '创建时间';

COMMENT ON COLUMN model_tests.updated_at IS '更新时间';

COMMENT ON COLUMN model_tests.revision IS '并发修订号';

COMMENT ON COLUMN model_tests.environment IS '所属环境';

COMMENT ON COLUMN model_tests.model_id IS '模型标识';

COMMENT ON COLUMN model_tests.config_revision IS '配置修订号';

COMMENT ON COLUMN model_tests.cases IS '验证用例';

COMMENT ON COLUMN model_tests.results IS '验证结果';

COMMENT ON COLUMN model_tests.latency_ms IS '耗时毫秒';

COMMENT ON COLUMN model_tests.attempt_ids IS '实际尝试引用';

COMMENT ON COLUMN model_tests.config_digest IS '冻结配置摘要';

COMMENT ON COLUMN model_tests.execution IS '冻结调试执行描述';

COMMENT ON COLUMN model_tests.state IS '验证执行状态';

COMMENT ON COLUMN model_tests.run_id IS '统一调试运行标识';

COMMENT ON COLUMN model_tests.error_code IS '验证失败类别';

COMMENT ON COLUMN model_tests.reason IS '验证失败原因';

CREATE INDEX ix_model_tests_0 ON model_tests (channel_id, id);

CREATE INDEX ix_model_tests_1 ON model_tests (channel_id, model_id, created_at);

-- models：模型映射。
CREATE TABLE models (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	model_code VARCHAR(64),
	name VARCHAR(128),
	connection_id VARCHAR(64),
	provider_model_name VARCHAR(256),
	context_limit INTEGER,
	status VARCHAR(32),
	capabilities JSONB,
	verified_at TIMESTAMP WITH TIME ZONE,
	current_version_id VARCHAR(64),
	parameters JSONB,
	parameter_allowlist JSONB,
	validation_revision BIGINT
);

COMMENT ON TABLE models IS '模型映射';

COMMENT ON COLUMN models.id IS '记录标识';

COMMENT ON COLUMN models.channel_id IS '所属渠道标识';

COMMENT ON COLUMN models.created_at IS '创建时间';

COMMENT ON COLUMN models.updated_at IS '更新时间';

COMMENT ON COLUMN models.revision IS '并发修订号';

COMMENT ON COLUMN models.model_code IS '稳定模型编码';

COMMENT ON COLUMN models.name IS '模型名称';

COMMENT ON COLUMN models.connection_id IS '连接标识';

COMMENT ON COLUMN models.provider_model_name IS '供应商模型名';

COMMENT ON COLUMN models.context_limit IS '上下文上限';

COMMENT ON COLUMN models.status IS '启用状态';

COMMENT ON COLUMN models.capabilities IS '按能力记录支持及验证状态';

COMMENT ON COLUMN models.verified_at IS '验证时间';

COMMENT ON COLUMN models.current_version_id IS '当前模型映射版本标识';

COMMENT ON COLUMN models.parameters IS '模型默认参数';

COMMENT ON COLUMN models.parameter_allowlist IS '模型允许的参数清单';

COMMENT ON COLUMN models.validation_revision IS '能力验证语义修订号';

CREATE INDEX ix_models_0 ON models (channel_id, id);

CREATE INDEX ix_models_1 ON models (channel_id, model_code);

-- platform_accounts：平台账号。
CREATE TABLE platform_accounts (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	login_name VARCHAR(128),
	display_name VARCHAR(128),
	password_hash VARCHAR(512),
	platform_roles JSONB,
	status VARCHAR(32),
	must_change_password BOOLEAN,
	credential_updated_at TIMESTAMP WITH TIME ZONE,
	credential_version BIGINT
);

COMMENT ON TABLE platform_accounts IS '平台账号';

COMMENT ON COLUMN platform_accounts.id IS '记录标识';

COMMENT ON COLUMN platform_accounts.channel_id IS '所属渠道标识';

COMMENT ON COLUMN platform_accounts.created_at IS '创建时间';

COMMENT ON COLUMN platform_accounts.updated_at IS '更新时间';

COMMENT ON COLUMN platform_accounts.revision IS '并发修订号';

COMMENT ON COLUMN platform_accounts.login_name IS '规范化登录名';

COMMENT ON COLUMN platform_accounts.display_name IS '显示名称';

COMMENT ON COLUMN platform_accounts.password_hash IS '密码安全摘要';

COMMENT ON COLUMN platform_accounts.platform_roles IS '平台角色清单';

COMMENT ON COLUMN platform_accounts.status IS '账号状态';

COMMENT ON COLUMN platform_accounts.must_change_password IS '首次修改密码标记';

COMMENT ON COLUMN platform_accounts.credential_updated_at IS '凭据更新时间';

COMMENT ON COLUMN platform_accounts.credential_version IS '凭据撤销代次';

CREATE INDEX ix_platform_accounts_0 ON platform_accounts (channel_id, id);

CREATE INDEX ix_platform_accounts_1 ON platform_accounts (channel_id, login_name);

-- platform_limits：系统渠道平台总准入限额。
CREATE TABLE platform_limits (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	limit_code VARCHAR(64),
	name VARCHAR(128),
	kind VARCHAR(32),
	period VARCHAR(32),
	timezone VARCHAR(64),
	limit_value NUMERIC(24, 8),
	unit VARCHAR(32),
	status VARCHAR(32),
	replaces_id VARCHAR(64),
	effective_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE platform_limits IS '系统渠道平台总准入限额';

COMMENT ON COLUMN platform_limits.id IS '记录标识';

COMMENT ON COLUMN platform_limits.channel_id IS '所属渠道标识';

COMMENT ON COLUMN platform_limits.created_at IS '创建时间';

COMMENT ON COLUMN platform_limits.updated_at IS '更新时间';

COMMENT ON COLUMN platform_limits.revision IS '并发修订号';

COMMENT ON COLUMN platform_limits.limit_code IS '平台限额编码';

COMMENT ON COLUMN platform_limits.name IS '限额名称';

COMMENT ON COLUMN platform_limits.kind IS '并发或请求数量类别';

COMMENT ON COLUMN platform_limits.period IS '限额周期';

COMMENT ON COLUMN platform_limits.timezone IS '周期时区';

COMMENT ON COLUMN platform_limits.limit_value IS '限额数量';

COMMENT ON COLUMN platform_limits.unit IS '计量单位';

COMMENT ON COLUMN platform_limits.status IS '限额启用状态';

COMMENT ON COLUMN platform_limits.replaces_id IS '前一个限额版本标识';

COMMENT ON COLUMN platform_limits.effective_at IS '该限额版本生效时间';

CREATE INDEX ix_platform_limits_0 ON platform_limits (channel_id, id);

CREATE INDEX ix_platform_limits_1 ON platform_limits (channel_id, limit_code);

CREATE INDEX ix_platform_limits_current ON platform_limits (channel_id, limit_code, created_at);

-- platform_quota_occupancies：平台配额运行占用。
CREATE TABLE platform_quota_occupancies (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	target_channel_id VARCHAR(64),
	run_id VARCHAR(64),
	limit_id VARCHAR(64),
	limit_code VARCHAR(64),
	period_start TIMESTAMP WITH TIME ZONE,
	unit VARCHAR(32),
	status VARCHAR(32)
);

COMMENT ON TABLE platform_quota_occupancies IS '平台配额运行占用';

COMMENT ON COLUMN platform_quota_occupancies.id IS '记录标识';

COMMENT ON COLUMN platform_quota_occupancies.channel_id IS '所属渠道标识';

COMMENT ON COLUMN platform_quota_occupancies.created_at IS '创建时间';

COMMENT ON COLUMN platform_quota_occupancies.updated_at IS '更新时间';

COMMENT ON COLUMN platform_quota_occupancies.revision IS '并发修订号';

COMMENT ON COLUMN platform_quota_occupancies.target_channel_id IS '实际业务渠道标识';

COMMENT ON COLUMN platform_quota_occupancies.run_id IS '实际运行标识';

COMMENT ON COLUMN platform_quota_occupancies.limit_id IS '平台限额版本';

COMMENT ON COLUMN platform_quota_occupancies.limit_code IS '稳定平台限额编码';

COMMENT ON COLUMN platform_quota_occupancies.period_start IS '计数周期起点';

COMMENT ON COLUMN platform_quota_occupancies.unit IS '请求量或并发单位';

COMMENT ON COLUMN platform_quota_occupancies.status IS '占用状态';

CREATE INDEX ix_platform_quota_held ON platform_quota_occupancies (channel_id, limit_code, status);

CREATE INDEX ix_platform_quota_occupancies_0 ON platform_quota_occupancies (channel_id, id);

CREATE INDEX ix_platform_quota_occupancies_1 ON platform_quota_occupancies (channel_id, limit_code, period_start);

CREATE INDEX ix_platform_quota_occupancies_2 ON platform_quota_occupancies (channel_id, target_channel_id, run_id);

CREATE INDEX ix_platform_quota_period ON platform_quota_occupancies (channel_id, limit_code, created_at);

-- price_versions：模型价格版本。
CREATE TABLE price_versions (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	model_id VARCHAR(64),
	currency VARCHAR(3),
	price_items JSONB,
	unit VARCHAR(64),
	effective_at TIMESTAMP WITH TIME ZONE,
	source VARCHAR(1024),
	name VARCHAR(128),
	subset_relations JSONB
);

COMMENT ON TABLE price_versions IS '模型价格版本';

COMMENT ON COLUMN price_versions.id IS '记录标识';

COMMENT ON COLUMN price_versions.channel_id IS '所属渠道标识';

COMMENT ON COLUMN price_versions.created_at IS '创建时间';

COMMENT ON COLUMN price_versions.updated_at IS '更新时间';

COMMENT ON COLUMN price_versions.revision IS '并发修订号';

COMMENT ON COLUMN price_versions.model_id IS '模型标识';

COMMENT ON COLUMN price_versions.currency IS '币种';

COMMENT ON COLUMN price_versions.price_items IS '各计价维度和子集关系';

COMMENT ON COLUMN price_versions.unit IS '计价单位';

COMMENT ON COLUMN price_versions.effective_at IS '价格生效时间';

COMMENT ON COLUMN price_versions.source IS '价格来源';

COMMENT ON COLUMN price_versions.name IS '价格版本名称';

COMMENT ON COLUMN price_versions.subset_relations IS '适配器计量子集关系';

CREATE INDEX ix_price_versions_0 ON price_versions (channel_id, id);

CREATE INDEX ix_price_versions_1 ON price_versions (channel_id, model_id, effective_at);

-- prompt_samples：提示词调试样例。
CREATE TABLE prompt_samples (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	prompt_id VARCHAR(64),
	title VARCHAR(128),
	input JSONB,
	expected_constraints JSONB
);

COMMENT ON TABLE prompt_samples IS '提示词调试样例';

COMMENT ON COLUMN prompt_samples.id IS '记录标识';

COMMENT ON COLUMN prompt_samples.channel_id IS '所属渠道标识';

COMMENT ON COLUMN prompt_samples.created_at IS '创建时间';

COMMENT ON COLUMN prompt_samples.updated_at IS '更新时间';

COMMENT ON COLUMN prompt_samples.revision IS '并发修订号';

COMMENT ON COLUMN prompt_samples.environment IS '所属环境';

COMMENT ON COLUMN prompt_samples.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN prompt_samples.subject_type IS '业务主体类型';

COMMENT ON COLUMN prompt_samples.subject_id IS '业务主体编号';

COMMENT ON COLUMN prompt_samples.prompt_id IS '提示词资源标识';

COMMENT ON COLUMN prompt_samples.title IS '样例名称';

COMMENT ON COLUMN prompt_samples.input IS '样例输入';

COMMENT ON COLUMN prompt_samples.expected_constraints IS '预期断言';

CREATE INDEX ix_prompt_samples_0 ON prompt_samples (channel_id, id);

CREATE INDEX ix_prompt_samples_1 ON prompt_samples (channel_id, prompt_id);

-- prompt_tests：提示词调试记录。
CREATE TABLE prompt_tests (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	version_id VARCHAR(64),
	draft_revision BIGINT,
	release_snapshot_id VARCHAR(64),
	model_route_version VARCHAR(64),
	rendered_input_ref VARCHAR(64),
	run_id VARCHAR(64),
	frozen_version JSONB,
	sample_snapshot JSONB,
	rendered_input JSONB,
	descriptor_digest VARCHAR(64),
	sample_id VARCHAR(64),
	model_route_name VARCHAR(128),
	status VARCHAR(32)
);

COMMENT ON TABLE prompt_tests IS '提示词调试记录';

COMMENT ON COLUMN prompt_tests.id IS '记录标识';

COMMENT ON COLUMN prompt_tests.channel_id IS '所属渠道标识';

COMMENT ON COLUMN prompt_tests.created_at IS '创建时间';

COMMENT ON COLUMN prompt_tests.updated_at IS '更新时间';

COMMENT ON COLUMN prompt_tests.revision IS '并发修订号';

COMMENT ON COLUMN prompt_tests.environment IS '所属环境';

COMMENT ON COLUMN prompt_tests.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN prompt_tests.subject_type IS '业务主体类型';

COMMENT ON COLUMN prompt_tests.subject_id IS '业务主体编号';

COMMENT ON COLUMN prompt_tests.version_id IS '版本标识';

COMMENT ON COLUMN prompt_tests.draft_revision IS '草稿修订';

COMMENT ON COLUMN prompt_tests.release_snapshot_id IS '冻结快照标识';

COMMENT ON COLUMN prompt_tests.model_route_version IS '模型路由版本';

COMMENT ON COLUMN prompt_tests.rendered_input_ref IS '渲染内容引用';

COMMENT ON COLUMN prompt_tests.run_id IS '调试运行标识';

COMMENT ON COLUMN prompt_tests.frozen_version IS '调试时固定的提示词版本';

COMMENT ON COLUMN prompt_tests.sample_snapshot IS '调试时固定的样例与预期断言';

COMMENT ON COLUMN prompt_tests.rendered_input IS '保持来源分区的完整渲染快照';

COMMENT ON COLUMN prompt_tests.descriptor_digest IS '调试执行描述摘要';

COMMENT ON COLUMN prompt_tests.sample_id IS '调试样例标识';

COMMENT ON COLUMN prompt_tests.model_route_name IS '调试时的模型路由名称';

COMMENT ON COLUMN prompt_tests.status IS '调试受理状态';

CREATE INDEX ix_prompt_tests_0 ON prompt_tests (channel_id, id);

CREATE INDEX ix_prompt_tests_1 ON prompt_tests (channel_id, version_id);

CREATE INDEX ix_prompt_tests_2 ON prompt_tests (channel_id, environment, version_id, descriptor_digest);

-- prompts：提示词资源。
CREATE TABLE prompts (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	prompt_code VARCHAR(64),
	name VARCHAR(128),
	purpose VARCHAR(512),
	owner VARCHAR(128),
	status VARCHAR(32)
);

COMMENT ON TABLE prompts IS '提示词资源';

COMMENT ON COLUMN prompts.id IS '记录标识';

COMMENT ON COLUMN prompts.channel_id IS '所属渠道标识';

COMMENT ON COLUMN prompts.created_at IS '创建时间';

COMMENT ON COLUMN prompts.updated_at IS '更新时间';

COMMENT ON COLUMN prompts.revision IS '并发修订号';

COMMENT ON COLUMN prompts.prompt_code IS '提示词编码';

COMMENT ON COLUMN prompts.name IS '提示词名称';

COMMENT ON COLUMN prompts.purpose IS '使用用途';

COMMENT ON COLUMN prompts.owner IS '负责人标识';

COMMENT ON COLUMN prompts.status IS '资源状态';

CREATE INDEX ix_prompts_0 ON prompts (channel_id, id);

CREATE INDEX ix_prompts_1 ON prompts (channel_id, prompt_code);

-- provider_catalog：供应商字典。
CREATE TABLE provider_catalog (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	code VARCHAR(64),
	name VARCHAR(128),
	protocols JSONB,
	template_content JSONB
);

COMMENT ON TABLE provider_catalog IS '供应商字典';

COMMENT ON COLUMN provider_catalog.id IS '记录标识';

COMMENT ON COLUMN provider_catalog.channel_id IS '所属渠道标识';

COMMENT ON COLUMN provider_catalog.created_at IS '创建时间';

COMMENT ON COLUMN provider_catalog.updated_at IS '更新时间';

COMMENT ON COLUMN provider_catalog.revision IS '并发修订号';

COMMENT ON COLUMN provider_catalog.code IS '供应商编码';

COMMENT ON COLUMN provider_catalog.name IS '供应商名称';

COMMENT ON COLUMN provider_catalog.protocols IS '支持协议族';

COMMENT ON COLUMN provider_catalog.template_content IS '无凭据连接模板';

CREATE INDEX ix_provider_catalog_0 ON provider_catalog (channel_id, id);

CREATE INDEX ix_provider_catalog_1 ON provider_catalog (channel_id, code);

-- provider_statements：供应商账单核查批次。
CREATE TABLE provider_statements (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	name VARCHAR(128),
	source_digest VARCHAR(64),
	version VARCHAR(64),
	connection_id VARCHAR(64),
	currency VARCHAR(3),
	start_at TIMESTAMP WITH TIME ZONE,
	end_at TIMESTAMP WITH TIME ZONE,
	lines JSONB,
	owner_key VARCHAR(64)
);

COMMENT ON TABLE provider_statements IS '供应商账单核查批次';

COMMENT ON COLUMN provider_statements.id IS '记录标识';

COMMENT ON COLUMN provider_statements.channel_id IS '所属渠道标识';

COMMENT ON COLUMN provider_statements.created_at IS '创建时间';

COMMENT ON COLUMN provider_statements.updated_at IS '更新时间';

COMMENT ON COLUMN provider_statements.revision IS '并发修订号';

COMMENT ON COLUMN provider_statements.environment IS '所属环境';

COMMENT ON COLUMN provider_statements.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN provider_statements.subject_type IS '业务主体类型';

COMMENT ON COLUMN provider_statements.subject_id IS '业务主体编号';

COMMENT ON COLUMN provider_statements.name IS '账单来源名称';

COMMENT ON COLUMN provider_statements.source_digest IS '导入来源摘要';

COMMENT ON COLUMN provider_statements.version IS '来源账单版本';

COMMENT ON COLUMN provider_statements.connection_id IS '模型连接标识';

COMMENT ON COLUMN provider_statements.currency IS '账单币种';

COMMENT ON COLUMN provider_statements.start_at IS '核查开始时间';

COMMENT ON COLUMN provider_statements.end_at IS '核查结束时间';

COMMENT ON COLUMN provider_statements.lines IS '规范化供应商记录';

COMMENT ON COLUMN provider_statements.owner_key IS '创建身份摘要';

CREATE INDEX ix_provider_statements_0 ON provider_statements (channel_id, id);

-- recovery_barriers：内容恢复屏障。
CREATE TABLE recovery_barriers (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	state VARCHAR(32),
	recovery_id VARCHAR(64),
	marker_digest VARCHAR(64),
	verified_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE recovery_barriers IS '内容恢复屏障';

COMMENT ON COLUMN recovery_barriers.id IS '记录标识';

COMMENT ON COLUMN recovery_barriers.channel_id IS '所属渠道标识';

COMMENT ON COLUMN recovery_barriers.created_at IS '创建时间';

COMMENT ON COLUMN recovery_barriers.updated_at IS '更新时间';

COMMENT ON COLUMN recovery_barriers.revision IS '并发修订号';

COMMENT ON COLUMN recovery_barriers.environment IS '所属环境';

COMMENT ON COLUMN recovery_barriers.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN recovery_barriers.subject_type IS '业务主体类型';

COMMENT ON COLUMN recovery_barriers.subject_id IS '业务主体编号';

COMMENT ON COLUMN recovery_barriers.state IS '恢复屏障状态';

COMMENT ON COLUMN recovery_barriers.recovery_id IS '本次恢复标识';

COMMENT ON COLUMN recovery_barriers.marker_digest IS '已校验删除账本摘要';

COMMENT ON COLUMN recovery_barriers.verified_at IS '删除账本核对时间';

CREATE INDEX ix_recovery_barriers_0 ON recovery_barriers (channel_id, id);

CREATE INDEX ix_recovery_barriers_1 ON recovery_barriers (channel_id, environment, data_scope_id, subject_type, subject_id);

-- release_mappings：环境生效版本映射。
CREATE TABLE release_mappings (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	resource_type VARCHAR(64),
	resource_id VARCHAR(64),
	version_id VARCHAR(64),
	published_by VARCHAR(128),
	release_note VARCHAR(1024)
);

COMMENT ON TABLE release_mappings IS '环境生效版本映射';

COMMENT ON COLUMN release_mappings.id IS '记录标识';

COMMENT ON COLUMN release_mappings.channel_id IS '所属渠道标识';

COMMENT ON COLUMN release_mappings.created_at IS '创建时间';

COMMENT ON COLUMN release_mappings.updated_at IS '更新时间';

COMMENT ON COLUMN release_mappings.revision IS '并发修订号';

COMMENT ON COLUMN release_mappings.environment IS '所属环境';

COMMENT ON COLUMN release_mappings.resource_type IS '资源类型';

COMMENT ON COLUMN release_mappings.resource_id IS '稳定资源标识';

COMMENT ON COLUMN release_mappings.version_id IS '生效版本标识';

COMMENT ON COLUMN release_mappings.published_by IS '发布主体标识';

COMMENT ON COLUMN release_mappings.release_note IS '发布说明';

CREATE INDEX ix_release_mappings_0 ON release_mappings (channel_id, id);

CREATE INDEX ix_release_mappings_1 ON release_mappings (channel_id, environment, resource_type, resource_id);

-- release_snapshots：执行依赖冻结快照。
CREATE TABLE release_snapshots (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	run_id VARCHAR(64),
	purpose VARCHAR(32),
	versions JSONB,
	dependencies_digest VARCHAR(64),
	output_schema JSONB
);

COMMENT ON TABLE release_snapshots IS '执行依赖冻结快照';

COMMENT ON COLUMN release_snapshots.id IS '记录标识';

COMMENT ON COLUMN release_snapshots.channel_id IS '所属渠道标识';

COMMENT ON COLUMN release_snapshots.created_at IS '创建时间';

COMMENT ON COLUMN release_snapshots.updated_at IS '更新时间';

COMMENT ON COLUMN release_snapshots.revision IS '并发修订号';

COMMENT ON COLUMN release_snapshots.environment IS '所属环境';

COMMENT ON COLUMN release_snapshots.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN release_snapshots.subject_type IS '业务主体类型';

COMMENT ON COLUMN release_snapshots.subject_id IS '业务主体编号';

COMMENT ON COLUMN release_snapshots.run_id IS '所属运行标识';

COMMENT ON COLUMN release_snapshots.purpose IS '使用用途';

COMMENT ON COLUMN release_snapshots.versions IS '具体版本及草稿内容快照';

COMMENT ON COLUMN release_snapshots.dependencies_digest IS '全量依赖摘要';

COMMENT ON COLUMN release_snapshots.output_schema IS '固定输出结构';

CREATE INDEX ix_release_snapshots_0 ON release_snapshots (channel_id, id);

CREATE INDEX ix_release_snapshots_1 ON release_snapshots (channel_id, environment, run_id);

-- resource_grants：资源授权。
CREATE TABLE resource_grants (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	grantee_type VARCHAR(64),
	grantee_id VARCHAR(128),
	resource_type VARCHAR(64),
	resource_id VARCHAR(64),
	allowed_actions JSONB,
	environments JSONB,
	data_scopes JSONB
);

COMMENT ON TABLE resource_grants IS '资源授权';

COMMENT ON COLUMN resource_grants.id IS '记录标识';

COMMENT ON COLUMN resource_grants.channel_id IS '所属渠道标识';

COMMENT ON COLUMN resource_grants.created_at IS '创建时间';

COMMENT ON COLUMN resource_grants.updated_at IS '更新时间';

COMMENT ON COLUMN resource_grants.revision IS '并发修订号';

COMMENT ON COLUMN resource_grants.grantee_type IS '受权主体类型';

COMMENT ON COLUMN resource_grants.grantee_id IS '受权主体标识';

COMMENT ON COLUMN resource_grants.resource_type IS '资源类型';

COMMENT ON COLUMN resource_grants.resource_id IS '资源标识';

COMMENT ON COLUMN resource_grants.allowed_actions IS '允许动作';

COMMENT ON COLUMN resource_grants.environments IS '授权环境';

COMMENT ON COLUMN resource_grants.data_scopes IS '授权数据域';

CREATE INDEX ix_resource_grants_0 ON resource_grants (channel_id, id);

CREATE INDEX ix_resource_grants_1 ON resource_grants (channel_id, grantee_type, grantee_id);

CREATE INDEX ix_resource_grants_2 ON resource_grants (channel_id, resource_type, resource_id);

-- resource_references：版本引用关系。
CREATE TABLE resource_references (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	source_version_id VARCHAR(64),
	target_version_id VARCHAR(64),
	target_resource_type VARCHAR(64)
);

COMMENT ON TABLE resource_references IS '版本引用关系';

COMMENT ON COLUMN resource_references.id IS '记录标识';

COMMENT ON COLUMN resource_references.channel_id IS '所属渠道标识';

COMMENT ON COLUMN resource_references.created_at IS '创建时间';

COMMENT ON COLUMN resource_references.updated_at IS '更新时间';

COMMENT ON COLUMN resource_references.revision IS '并发修订号';

COMMENT ON COLUMN resource_references.source_version_id IS '引用方版本标识';

COMMENT ON COLUMN resource_references.target_version_id IS '被引用版本标识';

COMMENT ON COLUMN resource_references.target_resource_type IS '被引用资源类型';

CREATE INDEX ix_resource_references_0 ON resource_references (channel_id, id);

CREATE INDEX ix_resource_references_1 ON resource_references (channel_id, target_version_id);

CREATE INDEX ix_resource_references_2 ON resource_references (channel_id, source_version_id);

-- resource_versions：资源版本与草稿。
CREATE TABLE resource_versions (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	resource_type VARCHAR(64),
	resource_id VARCHAR(64),
	version_label VARCHAR(128),
	state VARCHAR(32),
	content JSONB,
	content_digest VARCHAR(64),
	dependencies JSONB,
	dependencies_digest VARCHAR(64),
	output_schema JSONB,
	created_by VARCHAR(128)
);

COMMENT ON TABLE resource_versions IS '资源版本与草稿';

COMMENT ON COLUMN resource_versions.id IS '记录标识';

COMMENT ON COLUMN resource_versions.channel_id IS '所属渠道标识';

COMMENT ON COLUMN resource_versions.created_at IS '创建时间';

COMMENT ON COLUMN resource_versions.updated_at IS '更新时间';

COMMENT ON COLUMN resource_versions.revision IS '并发修订号';

COMMENT ON COLUMN resource_versions.resource_type IS '资源类型';

COMMENT ON COLUMN resource_versions.resource_id IS '稳定资源标识';

COMMENT ON COLUMN resource_versions.version_label IS '可读版本名称';

COMMENT ON COLUMN resource_versions.state IS '版本状态';

COMMENT ON COLUMN resource_versions.content IS '版本内容';

COMMENT ON COLUMN resource_versions.content_digest IS '规范化内容摘要';

COMMENT ON COLUMN resource_versions.dependencies IS '固定依赖清单';

COMMENT ON COLUMN resource_versions.dependencies_digest IS '依赖摘要';

COMMENT ON COLUMN resource_versions.output_schema IS '输出结构定义';

COMMENT ON COLUMN resource_versions.created_by IS '创建主体标识';

CREATE INDEX ix_resource_versions_0 ON resource_versions (channel_id, id);

CREATE INDEX ix_resource_versions_1 ON resource_versions (channel_id, resource_type, resource_id, state);

-- run_contents：运行敏感内容引用。
CREATE TABLE run_contents (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	run_id VARCHAR(64),
	kind VARCHAR(32),
	payload JSONB
);

COMMENT ON TABLE run_contents IS '运行敏感内容引用';

COMMENT ON COLUMN run_contents.id IS '记录标识';

COMMENT ON COLUMN run_contents.channel_id IS '所属渠道标识';

COMMENT ON COLUMN run_contents.created_at IS '创建时间';

COMMENT ON COLUMN run_contents.updated_at IS '更新时间';

COMMENT ON COLUMN run_contents.revision IS '并发修订号';

COMMENT ON COLUMN run_contents.environment IS '所属环境';

COMMENT ON COLUMN run_contents.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN run_contents.subject_type IS '业务主体类型';

COMMENT ON COLUMN run_contents.subject_id IS '业务主体编号';

COMMENT ON COLUMN run_contents.run_id IS '所属运行';

COMMENT ON COLUMN run_contents.kind IS '内容用途';

COMMENT ON COLUMN run_contents.payload IS '受控内容正文';

CREATE INDEX ix_run_contents_0 ON run_contents (channel_id, id);

CREATE INDEX ix_run_contents_1 ON run_contents (channel_id, run_id);

-- run_events：可补发运行事件。
CREATE TABLE run_events (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	run_id VARCHAR(64),
	sequence BIGINT,
	event_type VARCHAR(64),
	payload_ref VARCHAR(64),
	expires_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE run_events IS '可补发运行事件';

COMMENT ON COLUMN run_events.id IS '记录标识';

COMMENT ON COLUMN run_events.channel_id IS '所属渠道标识';

COMMENT ON COLUMN run_events.created_at IS '创建时间';

COMMENT ON COLUMN run_events.updated_at IS '更新时间';

COMMENT ON COLUMN run_events.revision IS '并发修订号';

COMMENT ON COLUMN run_events.environment IS '所属环境';

COMMENT ON COLUMN run_events.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN run_events.subject_type IS '业务主体类型';

COMMENT ON COLUMN run_events.subject_id IS '业务主体编号';

COMMENT ON COLUMN run_events.run_id IS '运行标识';

COMMENT ON COLUMN run_events.sequence IS '运行内单调序号';

COMMENT ON COLUMN run_events.event_type IS '事件类别';

COMMENT ON COLUMN run_events.payload_ref IS '事件内容引用';

COMMENT ON COLUMN run_events.expires_at IS '事件失效时间';

CREATE INDEX ix_run_events_0 ON run_events (channel_id, id);

CREATE INDEX ix_run_events_1 ON run_events (channel_id, run_id, sequence);

-- run_idempotency：接入请求幂等。
CREATE TABLE run_idempotency (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	client_id VARCHAR(64),
	agent_id VARCHAR(64),
	key VARCHAR(128),
	request_digest VARCHAR(64),
	run_id VARCHAR(64),
	expires_at TIMESTAMP WITH TIME ZONE,
	identity_type VARCHAR(32),
	identity_id VARCHAR(128),
	scope_digest VARCHAR(64)
);

COMMENT ON TABLE run_idempotency IS '接入请求幂等';

COMMENT ON COLUMN run_idempotency.id IS '记录标识';

COMMENT ON COLUMN run_idempotency.channel_id IS '所属渠道标识';

COMMENT ON COLUMN run_idempotency.created_at IS '创建时间';

COMMENT ON COLUMN run_idempotency.updated_at IS '更新时间';

COMMENT ON COLUMN run_idempotency.revision IS '并发修订号';

COMMENT ON COLUMN run_idempotency.environment IS '所属环境';

COMMENT ON COLUMN run_idempotency.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN run_idempotency.subject_type IS '业务主体类型';

COMMENT ON COLUMN run_idempotency.subject_id IS '业务主体编号';

COMMENT ON COLUMN run_idempotency.client_id IS '接入服务稳定标识';

COMMENT ON COLUMN run_idempotency.agent_id IS '智能体标识';

COMMENT ON COLUMN run_idempotency.key IS '调用方幂等键';

COMMENT ON COLUMN run_idempotency.request_digest IS '语义请求摘要';

COMMENT ON COLUMN run_idempotency.run_id IS '首次受理运行';

COMMENT ON COLUMN run_idempotency.expires_at IS '最早可清理时间';

COMMENT ON COLUMN run_idempotency.identity_type IS '稳定身份来源类型';

COMMENT ON COLUMN run_idempotency.identity_id IS '稳定调用服务或管理操作者';

COMMENT ON COLUMN run_idempotency.scope_digest IS '幂等范围摘要';

CREATE INDEX ix_run_idempotency_0 ON run_idempotency (channel_id, id);

CREATE INDEX ix_run_idempotency_1 ON run_idempotency (channel_id, environment, data_scope_id, subject_type, subject_id, client_id, agent_id, key);

CREATE INDEX ix_run_idempotency_2 ON run_idempotency (channel_id, scope_digest, key);

-- run_leases：工作进程执行租约。
CREATE TABLE run_leases (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	run_id VARCHAR(64),
	worker_id VARCHAR(128),
	lease_version BIGINT,
	heartbeat_at TIMESTAMP WITH TIME ZONE,
	expires_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE run_leases IS '工作进程执行租约';

COMMENT ON COLUMN run_leases.id IS '记录标识';

COMMENT ON COLUMN run_leases.channel_id IS '所属渠道标识';

COMMENT ON COLUMN run_leases.created_at IS '创建时间';

COMMENT ON COLUMN run_leases.updated_at IS '更新时间';

COMMENT ON COLUMN run_leases.revision IS '并发修订号';

COMMENT ON COLUMN run_leases.environment IS '所属环境';

COMMENT ON COLUMN run_leases.run_id IS '运行标识';

COMMENT ON COLUMN run_leases.worker_id IS '进程标识';

COMMENT ON COLUMN run_leases.lease_version IS '租约代次';

COMMENT ON COLUMN run_leases.heartbeat_at IS '最近续租时间';

COMMENT ON COLUMN run_leases.expires_at IS '租约到期时间';

CREATE INDEX ix_run_leases_0 ON run_leases (channel_id, id);

CREATE INDEX ix_run_leases_1 ON run_leases (channel_id, run_id);

-- run_occupancies：运行会话执行占用。
CREATE TABLE run_occupancies (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	conversation_id VARCHAR(64),
	run_id VARCHAR(64),
	state VARCHAR(32)
);

COMMENT ON TABLE run_occupancies IS '运行会话执行占用';

COMMENT ON COLUMN run_occupancies.id IS '记录标识';

COMMENT ON COLUMN run_occupancies.channel_id IS '所属渠道标识';

COMMENT ON COLUMN run_occupancies.created_at IS '创建时间';

COMMENT ON COLUMN run_occupancies.updated_at IS '更新时间';

COMMENT ON COLUMN run_occupancies.revision IS '并发修订号';

COMMENT ON COLUMN run_occupancies.environment IS '所属环境';

COMMENT ON COLUMN run_occupancies.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN run_occupancies.subject_type IS '业务主体类型';

COMMENT ON COLUMN run_occupancies.subject_id IS '业务主体编号';

COMMENT ON COLUMN run_occupancies.conversation_id IS '占用会话';

COMMENT ON COLUMN run_occupancies.run_id IS '占用运行';

COMMENT ON COLUMN run_occupancies.state IS '占用状态';

CREATE INDEX ix_run_occupancies_0 ON run_occupancies (channel_id, id);

CREATE INDEX ix_run_occupancies_1 ON run_occupancies (channel_id, environment, conversation_id);

-- run_recoveries：执行租约恢复判断记录。
CREATE TABLE run_recoveries (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	run_id VARCHAR(64),
	lease_version BIGINT,
	decision VARCHAR(32),
	reason VARCHAR(64)
);

COMMENT ON TABLE run_recoveries IS '执行租约恢复判断记录';

COMMENT ON COLUMN run_recoveries.id IS '记录标识';

COMMENT ON COLUMN run_recoveries.channel_id IS '所属渠道标识';

COMMENT ON COLUMN run_recoveries.created_at IS '创建时间';

COMMENT ON COLUMN run_recoveries.updated_at IS '更新时间';

COMMENT ON COLUMN run_recoveries.revision IS '并发修订号';

COMMENT ON COLUMN run_recoveries.environment IS '所属环境';

COMMENT ON COLUMN run_recoveries.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN run_recoveries.subject_type IS '业务主体类型';

COMMENT ON COLUMN run_recoveries.subject_id IS '业务主体编号';

COMMENT ON COLUMN run_recoveries.run_id IS '所属运行';

COMMENT ON COLUMN run_recoveries.lease_version IS '失效租约代次';

COMMENT ON COLUMN run_recoveries.decision IS '恢复判断结果';

COMMENT ON COLUMN run_recoveries.reason IS '脱敏原因类别';

CREATE INDEX ix_run_recoveries_0 ON run_recoveries (channel_id, id);

CREATE INDEX ix_run_recoveries_1 ON run_recoveries (channel_id, run_id, lease_version);

-- run_steps：执行步骤。
CREATE TABLE run_steps (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	run_id VARCHAR(64),
	node_key VARCHAR(128),
	state VARCHAR(32),
	input_ref VARCHAR(64),
	output_ref VARCHAR(64),
	checkpoint_ref VARCHAR(64),
	sequence BIGINT,
	attempt_count INTEGER,
	lease_version BIGINT
);

COMMENT ON TABLE run_steps IS '执行步骤';

COMMENT ON COLUMN run_steps.id IS '记录标识';

COMMENT ON COLUMN run_steps.channel_id IS '所属渠道标识';

COMMENT ON COLUMN run_steps.created_at IS '创建时间';

COMMENT ON COLUMN run_steps.updated_at IS '更新时间';

COMMENT ON COLUMN run_steps.revision IS '并发修订号';

COMMENT ON COLUMN run_steps.environment IS '所属环境';

COMMENT ON COLUMN run_steps.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN run_steps.subject_type IS '业务主体类型';

COMMENT ON COLUMN run_steps.subject_id IS '业务主体编号';

COMMENT ON COLUMN run_steps.run_id IS '运行标识';

COMMENT ON COLUMN run_steps.node_key IS '流程节点';

COMMENT ON COLUMN run_steps.state IS '步骤状态';

COMMENT ON COLUMN run_steps.input_ref IS '输入引用';

COMMENT ON COLUMN run_steps.output_ref IS '输出引用';

COMMENT ON COLUMN run_steps.checkpoint_ref IS '恢复点';

COMMENT ON COLUMN run_steps.sequence IS '步骤顺序';

COMMENT ON COLUMN run_steps.attempt_count IS '实际尝试累计次数';

COMMENT ON COLUMN run_steps.lease_version IS '最近有效提交租约代次';

CREATE INDEX ix_run_steps_0 ON run_steps (channel_id, id);

CREATE INDEX ix_run_steps_1 ON run_steps (channel_id, run_id, sequence);

-- runs：逻辑执行任务。
CREATE TABLE runs (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	source_type VARCHAR(32),
	client_id VARCHAR(64),
	key_id VARCHAR(64),
	actor_id VARCHAR(128),
	agent_id VARCHAR(64),
	agent_version_id VARCHAR(64),
	release_snapshot_id VARCHAR(64),
	purpose VARCHAR(32),
	state VARCHAR(32),
	deadline TIMESTAMP WITH TIME ZONE,
	conversation_id VARCHAR(64),
	parent_run_id VARCHAR(64),
	input_ref VARCHAR(64),
	result_ref VARCHAR(64),
	partial_output_ref VARCHAR(64),
	error JSONB,
	completed_at TIMESTAMP WITH TIME ZONE,
	agent_code VARCHAR(128),
	agent_name VARCHAR(128),
	identity JSONB,
	execution_policy JSONB,
	timeout_seconds INTEGER,
	timeout_source VARCHAR(128),
	event_sequence BIGINT,
	resources_released BOOLEAN,
	recovery_count INTEGER
);

COMMENT ON TABLE runs IS '逻辑执行任务';

COMMENT ON COLUMN runs.id IS '记录标识';

COMMENT ON COLUMN runs.channel_id IS '所属渠道标识';

COMMENT ON COLUMN runs.created_at IS '创建时间';

COMMENT ON COLUMN runs.updated_at IS '更新时间';

COMMENT ON COLUMN runs.revision IS '并发修订号';

COMMENT ON COLUMN runs.environment IS '所属环境';

COMMENT ON COLUMN runs.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN runs.subject_type IS '业务主体类型';

COMMENT ON COLUMN runs.subject_id IS '业务主体编号';

COMMENT ON COLUMN runs.source_type IS '发起来源';

COMMENT ON COLUMN runs.client_id IS '接入服务';

COMMENT ON COLUMN runs.key_id IS '渠道密钥';

COMMENT ON COLUMN runs.actor_id IS '管理发起人';

COMMENT ON COLUMN runs.agent_id IS '智能体';

COMMENT ON COLUMN runs.agent_version_id IS '智能体版本';

COMMENT ON COLUMN runs.release_snapshot_id IS '冻结依赖快照';

COMMENT ON COLUMN runs.purpose IS '调用用途';

COMMENT ON COLUMN runs.state IS '技术状态';

COMMENT ON COLUMN runs.deadline IS '全程截止时间';

COMMENT ON COLUMN runs.conversation_id IS '会话标识';

COMMENT ON COLUMN runs.parent_run_id IS '来源运行';

COMMENT ON COLUMN runs.input_ref IS '输入内容引用';

COMMENT ON COLUMN runs.result_ref IS '正式结果引用';

COMMENT ON COLUMN runs.partial_output_ref IS '部分输出引用';

COMMENT ON COLUMN runs.error IS '脱敏错误';

COMMENT ON COLUMN runs.completed_at IS '终结时间';

COMMENT ON COLUMN runs.agent_code IS '智能体调用编码';

COMMENT ON COLUMN runs.agent_name IS '受理时智能体名称';

COMMENT ON COLUMN runs.identity IS '不含访问令牌的原始身份';

COMMENT ON COLUMN runs.execution_policy IS '冻结执行限额与步骤策略';

COMMENT ON COLUMN runs.timeout_seconds IS '全程时限秒数';

COMMENT ON COLUMN runs.timeout_source IS '时限配置来源';

COMMENT ON COLUMN runs.event_sequence IS '最后事件序号';

COMMENT ON COLUMN runs.resources_released IS '终态占用已释放';

COMMENT ON COLUMN runs.recovery_count IS '租约失效恢复次数';

CREATE INDEX ix_runs_0 ON runs (channel_id, id);

CREATE INDEX ix_runs_1 ON runs (channel_id, environment, state, created_at);

-- service_clients：业务接入服务。
CREATE TABLE service_clients (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	name VARCHAR(128),
	scopes JSONB,
	status VARCHAR(32),
	data_scopes JSONB
);

COMMENT ON TABLE service_clients IS '业务接入服务';

COMMENT ON COLUMN service_clients.id IS '记录标识';

COMMENT ON COLUMN service_clients.channel_id IS '所属渠道标识';

COMMENT ON COLUMN service_clients.created_at IS '创建时间';

COMMENT ON COLUMN service_clients.updated_at IS '更新时间';

COMMENT ON COLUMN service_clients.revision IS '并发修订号';

COMMENT ON COLUMN service_clients.environment IS '所属环境';

COMMENT ON COLUMN service_clients.name IS '服务名称';

COMMENT ON COLUMN service_clients.scopes IS '权限上限';

COMMENT ON COLUMN service_clients.status IS '服务状态';

COMMENT ON COLUMN service_clients.data_scopes IS '授权业务数据域清单';

CREATE INDEX ix_service_clients_0 ON service_clients (channel_id, id);

CREATE INDEX ix_service_clients_1 ON service_clients (channel_id, environment, status);

-- skill_files：技能版本文件清单。
CREATE TABLE skill_files (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	version_id VARCHAR(64),
	relative_path VARCHAR(1024),
	content_type VARCHAR(128),
	size_bytes BIGINT,
	sha256 VARCHAR(64),
	artifact_id VARCHAR(64),
	loadable BOOLEAN,
	unavailable_reason TEXT
);

COMMENT ON TABLE skill_files IS '技能版本文件清单';

COMMENT ON COLUMN skill_files.id IS '记录标识';

COMMENT ON COLUMN skill_files.channel_id IS '所属渠道标识';

COMMENT ON COLUMN skill_files.created_at IS '创建时间';

COMMENT ON COLUMN skill_files.updated_at IS '更新时间';

COMMENT ON COLUMN skill_files.revision IS '并发修订号';

COMMENT ON COLUMN skill_files.version_id IS '技能版本';

COMMENT ON COLUMN skill_files.relative_path IS '包内路径';

COMMENT ON COLUMN skill_files.content_type IS '媒体类型';

COMMENT ON COLUMN skill_files.size_bytes IS '文件字节数';

COMMENT ON COLUMN skill_files.sha256 IS '文件摘要';

COMMENT ON COLUMN skill_files.artifact_id IS '受控内容引用';

COMMENT ON COLUMN skill_files.loadable IS '当前是否可加载';

COMMENT ON COLUMN skill_files.unavailable_reason IS '不可加载原因';

CREATE INDEX ix_skill_files_0 ON skill_files (channel_id, id);

CREATE INDEX ix_skill_files_1 ON skill_files (channel_id, version_id, relative_path);

-- skill_tests：技能加载测试。
CREATE TABLE skill_tests (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	version_id VARCHAR(64),
	release_snapshot_id VARCHAR(64),
	selected_files JSONB,
	run_id VARCHAR(64),
	result JSONB,
	context_snapshot JSONB
);

COMMENT ON TABLE skill_tests IS '技能加载测试';

COMMENT ON COLUMN skill_tests.id IS '记录标识';

COMMENT ON COLUMN skill_tests.channel_id IS '所属渠道标识';

COMMENT ON COLUMN skill_tests.created_at IS '创建时间';

COMMENT ON COLUMN skill_tests.updated_at IS '更新时间';

COMMENT ON COLUMN skill_tests.revision IS '并发修订号';

COMMENT ON COLUMN skill_tests.environment IS '所属环境';

COMMENT ON COLUMN skill_tests.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN skill_tests.subject_type IS '业务主体类型';

COMMENT ON COLUMN skill_tests.subject_id IS '业务主体编号';

COMMENT ON COLUMN skill_tests.version_id IS '技能版本';

COMMENT ON COLUMN skill_tests.release_snapshot_id IS '上下文冻结快照';

COMMENT ON COLUMN skill_tests.selected_files IS '实际加载文件';

COMMENT ON COLUMN skill_tests.run_id IS '运行标识';

COMMENT ON COLUMN skill_tests.result IS '测试结果';

COMMENT ON COLUMN skill_tests.context_snapshot IS '加载输入与版本冻结快照';

CREATE INDEX ix_skill_tests_0 ON skill_tests (channel_id, id);

CREATE INDEX ix_skill_tests_1 ON skill_tests (channel_id, version_id);

-- skills：技能资源。
CREATE TABLE skills (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	skill_code VARCHAR(64),
	name VARCHAR(128),
	description TEXT,
	owner VARCHAR(128),
	tags JSONB,
	status VARCHAR(32)
);

COMMENT ON TABLE skills IS '技能资源';

COMMENT ON COLUMN skills.id IS '记录标识';

COMMENT ON COLUMN skills.channel_id IS '所属渠道标识';

COMMENT ON COLUMN skills.created_at IS '创建时间';

COMMENT ON COLUMN skills.updated_at IS '更新时间';

COMMENT ON COLUMN skills.revision IS '并发修订号';

COMMENT ON COLUMN skills.skill_code IS '技能编码';

COMMENT ON COLUMN skills.name IS '技能名称';

COMMENT ON COLUMN skills.description IS '用途说明';

COMMENT ON COLUMN skills.owner IS '负责人';

COMMENT ON COLUMN skills.tags IS '发现标签';

COMMENT ON COLUMN skills.status IS '启用状态';

CREATE INDEX ix_skills_0 ON skills (channel_id, id);

CREATE INDEX ix_skills_1 ON skills (channel_id, skill_code);

-- source_links：内容来源与派生关系。
CREATE TABLE source_links (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	source_type VARCHAR(64),
	source_id VARCHAR(128),
	derived_type VARCHAR(64),
	derived_id VARCHAR(128),
	source_version VARCHAR(128)
);

COMMENT ON TABLE source_links IS '内容来源与派生关系';

COMMENT ON COLUMN source_links.id IS '记录标识';

COMMENT ON COLUMN source_links.channel_id IS '所属渠道标识';

COMMENT ON COLUMN source_links.created_at IS '创建时间';

COMMENT ON COLUMN source_links.updated_at IS '更新时间';

COMMENT ON COLUMN source_links.revision IS '并发修订号';

COMMENT ON COLUMN source_links.environment IS '所属环境';

COMMENT ON COLUMN source_links.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN source_links.subject_type IS '业务主体类型';

COMMENT ON COLUMN source_links.subject_id IS '业务主体编号';

COMMENT ON COLUMN source_links.source_type IS '来源类型';

COMMENT ON COLUMN source_links.source_id IS '来源标识';

COMMENT ON COLUMN source_links.derived_type IS '派生类型';

COMMENT ON COLUMN source_links.derived_id IS '派生标识';

COMMENT ON COLUMN source_links.source_version IS '来源版本';

CREATE INDEX ix_source_links_0 ON source_links (channel_id, id);

CREATE INDEX ix_source_links_1 ON source_links (channel_id, environment, source_type, source_id);

CREATE INDEX ix_source_links_2 ON source_links (channel_id, environment, derived_type, derived_id);

-- subject_review_bindings：当前主体复核的固定 MCP 绑定。
CREATE TABLE subject_review_bindings (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	client_id VARCHAR(64),
	connection_id VARCHAR(64),
	discovery_id VARCHAR(64),
	remote_tool_name VARCHAR(256),
	tool_name VARCHAR(128),
	connection_revision BIGINT,
	schema_hash VARCHAR(128),
	timeout_seconds INTEGER,
	enabled BOOLEAN
);

COMMENT ON TABLE subject_review_bindings IS '当前主体复核的固定 MCP 绑定';

COMMENT ON COLUMN subject_review_bindings.id IS '记录标识';

COMMENT ON COLUMN subject_review_bindings.channel_id IS '所属渠道标识';

COMMENT ON COLUMN subject_review_bindings.created_at IS '创建时间';

COMMENT ON COLUMN subject_review_bindings.updated_at IS '更新时间';

COMMENT ON COLUMN subject_review_bindings.revision IS '并发修订号';

COMMENT ON COLUMN subject_review_bindings.environment IS '所属环境';

COMMENT ON COLUMN subject_review_bindings.data_scope_id IS '所属业务数据域';

COMMENT ON COLUMN subject_review_bindings.client_id IS '受限接入服务标识';

COMMENT ON COLUMN subject_review_bindings.connection_id IS '固定身份复核连接';

COMMENT ON COLUMN subject_review_bindings.discovery_id IS '授权时发现快照';

COMMENT ON COLUMN subject_review_bindings.remote_tool_name IS '专用身份复核工具名';

COMMENT ON COLUMN subject_review_bindings.tool_name IS '身份工具显示名称';

COMMENT ON COLUMN subject_review_bindings.connection_revision IS '授权时连接配置修订';

COMMENT ON COLUMN subject_review_bindings.schema_hash IS '授权时身份工具契约摘要';

COMMENT ON COLUMN subject_review_bindings.timeout_seconds IS '身份复核超时秒数';

COMMENT ON COLUMN subject_review_bindings.enabled IS '是否允许身份复核';

CREATE INDEX ix_subject_review_bindings_0 ON subject_review_bindings (channel_id, id);

CREATE INDEX ix_subject_review_bindings_1 ON subject_review_bindings (channel_id, environment, data_scope_id, client_id);

-- tool_calls：工具实际调用。
CREATE TABLE tool_calls (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	run_id VARCHAR(64),
	step_id VARCHAR(64),
	attempt_id VARCHAR(64),
	tool_version_id VARCHAR(64),
	args_digest VARCHAR(64),
	state VARCHAR(32),
	source_request_id VARCHAR(256),
	result_ref VARCHAR(64),
	latency_ms INTEGER,
	error JSONB,
	tool_id VARCHAR(64),
	redacted_arguments JSONB,
	result_summary JSONB,
	evidence_ids JSONB,
	attempt JSONB
);

COMMENT ON TABLE tool_calls IS '工具实际调用';

COMMENT ON COLUMN tool_calls.id IS '记录标识';

COMMENT ON COLUMN tool_calls.channel_id IS '所属渠道标识';

COMMENT ON COLUMN tool_calls.created_at IS '创建时间';

COMMENT ON COLUMN tool_calls.updated_at IS '更新时间';

COMMENT ON COLUMN tool_calls.revision IS '并发修订号';

COMMENT ON COLUMN tool_calls.environment IS '所属环境';

COMMENT ON COLUMN tool_calls.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN tool_calls.subject_type IS '业务主体类型';

COMMENT ON COLUMN tool_calls.subject_id IS '业务主体编号';

COMMENT ON COLUMN tool_calls.run_id IS '运行标识';

COMMENT ON COLUMN tool_calls.step_id IS '步骤标识';

COMMENT ON COLUMN tool_calls.attempt_id IS '尝试标识';

COMMENT ON COLUMN tool_calls.tool_version_id IS '工具版本';

COMMENT ON COLUMN tool_calls.args_digest IS '参数摘要';

COMMENT ON COLUMN tool_calls.state IS '调用状态';

COMMENT ON COLUMN tool_calls.source_request_id IS '源请求标识';

COMMENT ON COLUMN tool_calls.result_ref IS '结果内容引用';

COMMENT ON COLUMN tool_calls.latency_ms IS '耗时毫秒';

COMMENT ON COLUMN tool_calls.error IS '脱敏错误';

COMMENT ON COLUMN tool_calls.tool_id IS '工具资源标识';

COMMENT ON COLUMN tool_calls.redacted_arguments IS '脱敏输入参数';

COMMENT ON COLUMN tool_calls.result_summary IS '结果结构摘要';

COMMENT ON COLUMN tool_calls.evidence_ids IS '有效证据标识集合';

COMMENT ON COLUMN tool_calls.attempt IS '独立尝试状态';

CREATE INDEX ix_tool_calls_0 ON tool_calls (channel_id, id);

CREATE INDEX ix_tool_calls_1 ON tool_calls (channel_id, run_id);

CREATE INDEX ix_tool_calls_2 ON tool_calls (channel_id, attempt_id);

-- tools：工具资源。
CREATE TABLE tools (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	tool_code VARCHAR(64),
	name VARCHAR(128),
	description TEXT,
	source_type VARCHAR(32),
	owner VARCHAR(128),
	status VARCHAR(32)
);

COMMENT ON TABLE tools IS '工具资源';

COMMENT ON COLUMN tools.id IS '记录标识';

COMMENT ON COLUMN tools.channel_id IS '所属渠道标识';

COMMENT ON COLUMN tools.created_at IS '创建时间';

COMMENT ON COLUMN tools.updated_at IS '更新时间';

COMMENT ON COLUMN tools.revision IS '并发修订号';

COMMENT ON COLUMN tools.tool_code IS '调用编码';

COMMENT ON COLUMN tools.name IS '工具名称';

COMMENT ON COLUMN tools.description IS '用途说明';

COMMENT ON COLUMN tools.source_type IS '来源类型';

COMMENT ON COLUMN tools.owner IS '负责人';

COMMENT ON COLUMN tools.status IS '启用状态';

CREATE INDEX ix_tools_0 ON tools (channel_id, id);

CREATE INDEX ix_tools_1 ON tools (channel_id, tool_code);

-- usage_adjustments：用量计价修正轨迹。
CREATE TABLE usage_adjustments (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	usage_id VARCHAR(64),
	event_id VARCHAR(64),
	previous_revision BIGINT,
	amount_delta NUMERIC(24, 8),
	currency VARCHAR(3),
	reason VARCHAR(512),
	calculation JSONB
);

COMMENT ON TABLE usage_adjustments IS '用量计价修正轨迹';

COMMENT ON COLUMN usage_adjustments.id IS '记录标识';

COMMENT ON COLUMN usage_adjustments.channel_id IS '所属渠道标识';

COMMENT ON COLUMN usage_adjustments.created_at IS '创建时间';

COMMENT ON COLUMN usage_adjustments.updated_at IS '更新时间';

COMMENT ON COLUMN usage_adjustments.revision IS '并发修订号';

COMMENT ON COLUMN usage_adjustments.usage_id IS '账本标识';

COMMENT ON COLUMN usage_adjustments.event_id IS '来源事件标识';

COMMENT ON COLUMN usage_adjustments.previous_revision IS '前次修订';

COMMENT ON COLUMN usage_adjustments.amount_delta IS '金额变动';

COMMENT ON COLUMN usage_adjustments.currency IS '币种';

COMMENT ON COLUMN usage_adjustments.reason IS '修正原因';

COMMENT ON COLUMN usage_adjustments.calculation IS '核算依据';

CREATE INDEX ix_usage_adjustments_0 ON usage_adjustments (channel_id, id);

CREATE INDEX ix_usage_adjustments_1 ON usage_adjustments (channel_id, usage_id, created_at);

-- usage_aggregates：可重算用量聚合。
CREATE TABLE usage_aggregates (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	dimensions JSONB,
	dimensions_digest VARCHAR(64),
	period_start TIMESTAMP WITH TIME ZONE,
	currency VARCHAR(3),
	totals JSONB,
	ledger_watermark TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE usage_aggregates IS '可重算用量聚合';

COMMENT ON COLUMN usage_aggregates.id IS '记录标识';

COMMENT ON COLUMN usage_aggregates.channel_id IS '所属渠道标识';

COMMENT ON COLUMN usage_aggregates.created_at IS '创建时间';

COMMENT ON COLUMN usage_aggregates.updated_at IS '更新时间';

COMMENT ON COLUMN usage_aggregates.revision IS '并发修订号';

COMMENT ON COLUMN usage_aggregates.dimensions IS '统计维度';

COMMENT ON COLUMN usage_aggregates.dimensions_digest IS '统计范围摘要';

COMMENT ON COLUMN usage_aggregates.period_start IS '周期开始';

COMMENT ON COLUMN usage_aggregates.currency IS '币种';

COMMENT ON COLUMN usage_aggregates.totals IS '数量及完整性分组';

COMMENT ON COLUMN usage_aggregates.ledger_watermark IS '账本处理水位';

CREATE INDEX ix_usage_aggregates_0 ON usage_aggregates (channel_id, id);

CREATE INDEX ix_usage_aggregates_1 ON usage_aggregates (channel_id, dimensions_digest, period_start, currency);

-- usage_events：供应商用量来源事件。
CREATE TABLE usage_events (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	attempt_id VARCHAR(64),
	connection_id VARCHAR(64),
	source_request_id VARCHAR(256),
	event_version BIGINT,
	raw_usage JSONB,
	usage_status VARCHAR(32),
	observed_at TIMESTAMP WITH TIME ZONE,
	event_payload JSONB,
	payload_digest VARCHAR(64),
	applied BOOLEAN
);

COMMENT ON TABLE usage_events IS '供应商用量来源事件';

COMMENT ON COLUMN usage_events.id IS '记录标识';

COMMENT ON COLUMN usage_events.channel_id IS '所属渠道标识';

COMMENT ON COLUMN usage_events.created_at IS '创建时间';

COMMENT ON COLUMN usage_events.updated_at IS '更新时间';

COMMENT ON COLUMN usage_events.revision IS '并发修订号';

COMMENT ON COLUMN usage_events.attempt_id IS '尝试标识';

COMMENT ON COLUMN usage_events.connection_id IS '供应商连接标识';

COMMENT ON COLUMN usage_events.source_request_id IS '供应商请求标识';

COMMENT ON COLUMN usage_events.event_version IS '事件版本';

COMMENT ON COLUMN usage_events.raw_usage IS '原始计量值与子集口径';

COMMENT ON COLUMN usage_events.usage_status IS '事件完整性';

COMMENT ON COLUMN usage_events.observed_at IS '观测时间';

COMMENT ON COLUMN usage_events.event_payload IS '经契约验证的完整计量事件';

COMMENT ON COLUMN usage_events.payload_digest IS '事件内容摘要';

COMMENT ON COLUMN usage_events.applied IS '是否成为当前有效计量';

CREATE INDEX ix_usage_events_0 ON usage_events (channel_id, id);

CREATE INDEX ix_usage_events_1 ON usage_events (channel_id, connection_id, source_request_id, event_version);

-- usage_exchange_rates：核算展示汇率版本。
CREATE TABLE usage_exchange_rates (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	base_currency VARCHAR(3),
	quote_currency VARCHAR(3),
	rate NUMERIC(24, 8),
	effective_at TIMESTAMP WITH TIME ZONE,
	source VARCHAR(1024)
);

COMMENT ON TABLE usage_exchange_rates IS '核算展示汇率版本';

COMMENT ON COLUMN usage_exchange_rates.id IS '记录标识';

COMMENT ON COLUMN usage_exchange_rates.channel_id IS '所属渠道标识';

COMMENT ON COLUMN usage_exchange_rates.created_at IS '创建时间';

COMMENT ON COLUMN usage_exchange_rates.updated_at IS '更新时间';

COMMENT ON COLUMN usage_exchange_rates.revision IS '并发修订号';

COMMENT ON COLUMN usage_exchange_rates.base_currency IS '原始币种';

COMMENT ON COLUMN usage_exchange_rates.quote_currency IS '折算币种';

COMMENT ON COLUMN usage_exchange_rates.rate IS '折算汇率';

COMMENT ON COLUMN usage_exchange_rates.effective_at IS '汇率日期';

COMMENT ON COLUMN usage_exchange_rates.source IS '汇率来源';

CREATE INDEX ix_usage_exchange_rates_0 ON usage_exchange_rates (channel_id, id);

CREATE INDEX ix_usage_exchange_rates_1 ON usage_exchange_rates (channel_id, base_currency, quote_currency, effective_at);

-- usage_exports：用量导出请求。
CREATE TABLE usage_exports (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	requested_by VARCHAR(128),
	filters JSONB,
	timezone VARCHAR(64),
	channel_range JSONB,
	state VARCHAR(32),
	artifact_id VARCHAR(64),
	scope_snapshot JSONB,
	object_key VARCHAR(1024),
	metadata JSONB,
	error_message VARCHAR(512),
	expires_at TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE usage_exports IS '用量导出请求';

COMMENT ON COLUMN usage_exports.id IS '记录标识';

COMMENT ON COLUMN usage_exports.channel_id IS '所属渠道标识';

COMMENT ON COLUMN usage_exports.created_at IS '创建时间';

COMMENT ON COLUMN usage_exports.updated_at IS '更新时间';

COMMENT ON COLUMN usage_exports.revision IS '并发修订号';

COMMENT ON COLUMN usage_exports.environment IS '所属环境';

COMMENT ON COLUMN usage_exports.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN usage_exports.subject_type IS '业务主体类型';

COMMENT ON COLUMN usage_exports.subject_id IS '业务主体编号';

COMMENT ON COLUMN usage_exports.requested_by IS '导出人';

COMMENT ON COLUMN usage_exports.filters IS '授权筛选条件';

COMMENT ON COLUMN usage_exports.timezone IS '业务时区';

COMMENT ON COLUMN usage_exports.channel_range IS '明确授权的渠道集合';

COMMENT ON COLUMN usage_exports.state IS '导出状态';

COMMENT ON COLUMN usage_exports.artifact_id IS '产物标识';

COMMENT ON COLUMN usage_exports.scope_snapshot IS '创建时受信查询范围';

COMMENT ON COLUMN usage_exports.object_key IS '渠道隔离的私有产物路径';

COMMENT ON COLUMN usage_exports.metadata IS '计量口径及价格完整性';

COMMENT ON COLUMN usage_exports.error_message IS '导出失败原因';

COMMENT ON COLUMN usage_exports.expires_at IS '导出失效时间';

CREATE INDEX ix_usage_exports_0 ON usage_exports (channel_id, id);

CREATE INDEX ix_usage_exports_1 ON usage_exports (channel_id, created_at);

-- usage_records：实际尝试用量账本。
CREATE TABLE usage_records (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	run_id VARCHAR(64),
	attempt_id VARCHAR(64),
	source_type VARCHAR(32),
	client_id VARCHAR(64),
	key_id VARCHAR(64),
	actor_id VARCHAR(128),
	agent_id VARCHAR(64),
	model_id VARCHAR(64),
	connection_id VARCHAR(64),
	purpose VARCHAR(32),
	input_tokens BIGINT,
	output_tokens BIGINT,
	cached_tokens BIGINT,
	reasoning_tokens BIGINT,
	raw_usage_ref VARCHAR(64),
	usage_status VARCHAR(32),
	pricing_status VARCHAR(32),
	price_version_id VARCHAR(64),
	amount NUMERIC(24, 8),
	currency VARCHAR(3),
	snapshot JSONB,
	normalized_tokens JSONB,
	subset_relations JSONB,
	calculation JSONB,
	upper_tokens JSONB,
	upper_amount NUMERIC(24, 8),
	state VARCHAR(32),
	sent_at TIMESTAMP WITH TIME ZONE,
	outcome VARCHAR(32),
	latest_event_version BIGINT,
	latest_event_id VARCHAR(64),
	final_reported BOOLEAN,
	source_request_id VARCHAR(256)
);

COMMENT ON TABLE usage_records IS '实际尝试用量账本';

COMMENT ON COLUMN usage_records.id IS '记录标识';

COMMENT ON COLUMN usage_records.channel_id IS '所属渠道标识';

COMMENT ON COLUMN usage_records.created_at IS '创建时间';

COMMENT ON COLUMN usage_records.updated_at IS '更新时间';

COMMENT ON COLUMN usage_records.revision IS '并发修订号';

COMMENT ON COLUMN usage_records.environment IS '所属环境';

COMMENT ON COLUMN usage_records.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN usage_records.subject_type IS '业务主体类型';

COMMENT ON COLUMN usage_records.subject_id IS '业务主体编号';

COMMENT ON COLUMN usage_records.run_id IS '运行标识';

COMMENT ON COLUMN usage_records.attempt_id IS '实际尝试标识';

COMMENT ON COLUMN usage_records.source_type IS '来源类型';

COMMENT ON COLUMN usage_records.client_id IS '接入服务标识';

COMMENT ON COLUMN usage_records.key_id IS '渠道密钥标识';

COMMENT ON COLUMN usage_records.actor_id IS '管理主体标识';

COMMENT ON COLUMN usage_records.agent_id IS '智能体标识';

COMMENT ON COLUMN usage_records.model_id IS '模型标识';

COMMENT ON COLUMN usage_records.connection_id IS '供应商连接标识';

COMMENT ON COLUMN usage_records.purpose IS '调用用途';

COMMENT ON COLUMN usage_records.input_tokens IS '输入数量';

COMMENT ON COLUMN usage_records.output_tokens IS '输出数量';

COMMENT ON COLUMN usage_records.cached_tokens IS '缓存子集数量';

COMMENT ON COLUMN usage_records.reasoning_tokens IS '推理子集数量';

COMMENT ON COLUMN usage_records.raw_usage_ref IS '原始用量引用';

COMMENT ON COLUMN usage_records.usage_status IS '用量完整性';

COMMENT ON COLUMN usage_records.pricing_status IS '计价完整性';

COMMENT ON COLUMN usage_records.price_version_id IS '价格版本标识';

COMMENT ON COLUMN usage_records.amount IS '核算金额';

COMMENT ON COLUMN usage_records.currency IS '币种';

COMMENT ON COLUMN usage_records.snapshot IS '受信运行来源及可读名称快照';

COMMENT ON COLUMN usage_records.normalized_tokens IS '按维度归一化用量';

COMMENT ON COLUMN usage_records.subset_relations IS '计量子集关系';

COMMENT ON COLUMN usage_records.calculation IS '当前核算公式及依据';

COMMENT ON COLUMN usage_records.upper_tokens IS '调用前核准的计量上限';

COMMENT ON COLUMN usage_records.upper_amount IS '调用前预占金额';

COMMENT ON COLUMN usage_records.state IS '实际调用结算状态';

COMMENT ON COLUMN usage_records.sent_at IS '供应商调用发送前登记时间';

COMMENT ON COLUMN usage_records.outcome IS '实际尝试结果';

COMMENT ON COLUMN usage_records.latest_event_version IS '当前有效来源事件版本';

COMMENT ON COLUMN usage_records.latest_event_id IS '当前有效来源事件';

COMMENT ON COLUMN usage_records.final_reported IS '是否收到供应商最终用量';

COMMENT ON COLUMN usage_records.source_request_id IS '固定供应商请求标识';

CREATE INDEX ix_usage_records_0 ON usage_records (channel_id, id);

CREATE INDEX ix_usage_records_1 ON usage_records (channel_id, attempt_id);

CREATE INDEX ix_usage_records_2 ON usage_records (channel_id, created_at);

-- webhook_deliveries：持久化事件投递。
CREATE TABLE webhook_deliveries (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	endpoint_id VARCHAR(64),
	event_id VARCHAR(64),
	kind VARCHAR(64),
	payload JSONB,
	owner_key VARCHAR(64),
	state VARCHAR(32),
	attempts BIGINT,
	next_at TIMESTAMP WITH TIME ZONE,
	lease_until TIMESTAMP WITH TIME ZONE,
	lease_nonce VARCHAR(64),
	error JSONB,
	http_status BIGINT,
	cycle_attempts BIGINT
);

COMMENT ON TABLE webhook_deliveries IS '持久化事件投递';

COMMENT ON COLUMN webhook_deliveries.id IS '记录标识';

COMMENT ON COLUMN webhook_deliveries.channel_id IS '所属渠道标识';

COMMENT ON COLUMN webhook_deliveries.created_at IS '创建时间';

COMMENT ON COLUMN webhook_deliveries.updated_at IS '更新时间';

COMMENT ON COLUMN webhook_deliveries.revision IS '并发修订号';

COMMENT ON COLUMN webhook_deliveries.environment IS '所属环境';

COMMENT ON COLUMN webhook_deliveries.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN webhook_deliveries.subject_type IS '业务主体类型';

COMMENT ON COLUMN webhook_deliveries.subject_id IS '业务主体编号';

COMMENT ON COLUMN webhook_deliveries.endpoint_id IS '投递端点标识';

COMMENT ON COLUMN webhook_deliveries.event_id IS '稳定事件编号';

COMMENT ON COLUMN webhook_deliveries.kind IS '事件类型';

COMMENT ON COLUMN webhook_deliveries.payload IS '最小事件正文';

COMMENT ON COLUMN webhook_deliveries.owner_key IS '执行身份摘要';

COMMENT ON COLUMN webhook_deliveries.state IS '当前处理状态';

COMMENT ON COLUMN webhook_deliveries.attempts IS '累计投递次数';

COMMENT ON COLUMN webhook_deliveries.next_at IS '下次尝试时间';

COMMENT ON COLUMN webhook_deliveries.lease_until IS '投递租约到期';

COMMENT ON COLUMN webhook_deliveries.lease_nonce IS '投递租约凭据';

COMMENT ON COLUMN webhook_deliveries.error IS '最近投递错误';

COMMENT ON COLUMN webhook_deliveries.http_status IS '最近响应状态';

COMMENT ON COLUMN webhook_deliveries.cycle_attempts IS '本轮自动投递次数，人工重投重新计数';

CREATE INDEX ix_webhook_deliveries_0 ON webhook_deliveries (channel_id, id);

CREATE INDEX ix_webhook_deliveries_1 ON webhook_deliveries (channel_id, environment, data_scope_id, subject_type, subject_id);

CREATE INDEX ix_webhook_deliveries_2 ON webhook_deliveries (channel_id, state, next_at);

-- webhook_endpoints：事件投递端点。
CREATE TABLE webhook_endpoints (
	id VARCHAR(64),
	channel_id VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE,
	revision BIGINT,
	environment VARCHAR(16),
	data_scope_id VARCHAR(64),
	subject_type VARCHAR(64),
	subject_id VARCHAR(128),
	name VARCHAR(128),
	url TEXT,
	secret_ref VARCHAR(64),
	events JSONB,
	owner_key VARCHAR(64),
	identity JSONB,
	state VARCHAR(32),
	client_ids JSONB
);

COMMENT ON TABLE webhook_endpoints IS '事件投递端点';

COMMENT ON COLUMN webhook_endpoints.id IS '记录标识';

COMMENT ON COLUMN webhook_endpoints.channel_id IS '所属渠道标识';

COMMENT ON COLUMN webhook_endpoints.created_at IS '创建时间';

COMMENT ON COLUMN webhook_endpoints.updated_at IS '更新时间';

COMMENT ON COLUMN webhook_endpoints.revision IS '并发修订号';

COMMENT ON COLUMN webhook_endpoints.environment IS '所属环境';

COMMENT ON COLUMN webhook_endpoints.data_scope_id IS '业务数据域标识';

COMMENT ON COLUMN webhook_endpoints.subject_type IS '业务主体类型';

COMMENT ON COLUMN webhook_endpoints.subject_id IS '业务主体编号';

COMMENT ON COLUMN webhook_endpoints.name IS '端点名称';

COMMENT ON COLUMN webhook_endpoints.url IS '固定接收地址';

COMMENT ON COLUMN webhook_endpoints.secret_ref IS '签名密钥密文引用';

COMMENT ON COLUMN webhook_endpoints.events IS '订阅事件类型';

COMMENT ON COLUMN webhook_endpoints.owner_key IS '执行身份摘要';

COMMENT ON COLUMN webhook_endpoints.identity IS '原执行身份快照';

COMMENT ON COLUMN webhook_endpoints.state IS '当前处理状态';

COMMENT ON COLUMN webhook_endpoints.client_ids IS '订阅的调用服务列表；空列表仅包含配置者运行';

CREATE INDEX ix_webhook_endpoints_0 ON webhook_endpoints (channel_id, id);

CREATE INDEX ix_webhook_endpoints_1 ON webhook_endpoints (channel_id, environment, data_scope_id, subject_type, subject_id);

-- 初始化平台系统渠道；管理员与内置角色由账号初始化服务创建。
INSERT INTO channels (id, channel_id, created_at, updated_at, revision, channel_code, name, status, owner, archived_at, retention_policy, budget_policy_refs, rate_limit_policy_refs, business_type) VALUES ('system', 'system', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1, 'system', '平台系统渠道', 'ACTIVE', '平台', NULL, CAST('{"retention_days": 365}' AS JSONB), CAST('[]' AS JSONB), CAST('[]' AS JSONB), 'system');

-- 写入已完成的迁移基线，后续升级从此修订继续。
INSERT INTO creativity_alembic_version (version_num, channel_id) VALUES ('0034_admission_indexes', 'system');

COMMIT;
