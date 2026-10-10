-- Creativity 表结构初始化归档，适用于 MySQL 8.0 空数据库。
-- 模型版本：2.1.0；配套数据归档迁移基线：0049_soft_delete。
-- 初始建库基线：alembic/mysql_versions/0048_mysql_milvus.py；后续修订在其上追加。
-- 包含 111 张表、1724 个字段、249 个普通索引及全部中文注释。
-- 本文件不写初始化数据；完成后必须执行 sql/init_data.sql，再启动服务或迁移。
-- 生成命令：make sql；一致性检查：make sql-check。请勿手工修改生成内容。
-- 执行方式见 sql/README.md；表创建在连接的当前 schema。
-- MySQL DDL 隐式提交；失败时保留已建表，须清理专用新库后重试，不能覆盖已有库。

BEGIN;
SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_BACKSLASH_ESCAPES,NO_ENGINE_SUBSTITUTION';
SET time_zone = '+00:00';
SELECT GET_LOCK(SHA2(CONCAT('creativity:migrate:', DATABASE()), 256), -1);

-- creativity_alembic_version：平台数据库迁移版本记录。
CREATE TABLE creativity_alembic_version (
	version_num VARCHAR(64) COMMENT '当前数据库迁移修订编号',
	channel_id VARCHAR(64) COMMENT '迁移记录所属系统渠道',
	is_deleted BOOL COMMENT '是否已逻辑删除'
)COMMENT='平台数据库迁移版本记录';

-- admissions：运行准入配额占用。
CREATE TABLE admissions (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	run_id VARCHAR(64) COMMENT '运行标识',
	policy_refs JSON COMMENT '命中配额策略',
	status VARCHAR(32) COMMENT '占用状态',
	expires_at DATETIME(6) COMMENT '核查时间',
	snapshot JSON COMMENT '运行来源及候选模型快照',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_admissions_0 (channel_id, id),
	INDEX ix_admissions_1 (channel_id, run_id),
	INDEX ix_admissions_active (channel_id, status, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='运行准入配额占用' COLLATE utf8mb4_0900_bin;

-- agent_candidates：冻结智能体候选快照。
CREATE TABLE agent_candidates (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	agent_id VARCHAR(64) COMMENT '智能体标识',
	source_version_id VARCHAR(64) COMMENT '来源版本标识',
	source_revision BIGINT COMMENT '来源草稿修订号',
	purpose VARCHAR(32) COMMENT '候选用途',
	content_digest VARCHAR(64) COMMENT '内容摘要',
	dependencies_digest VARCHAR(64) COMMENT '完整依赖摘要',
	candidate_digest VARCHAR(64) COMMENT '候选组合摘要',
	spec JSON COMMENT '不可变执行定义',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_agent_candidates_0 (channel_id, id),
	INDEX ix_agent_candidates_1 (channel_id, agent_id, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='冻结智能体候选快照' COLLATE utf8mb4_0900_bin;

-- agent_environment_states：智能体各环境停用状态。
CREATE TABLE agent_environment_states (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '目标环境',
	agent_id VARCHAR(64) COMMENT '智能体标识',
	status VARCHAR(32) COMMENT '当前环境启用状态',
	reason VARCHAR(1024) COMMENT '状态变更原因',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_agent_environment_states_0 (channel_id, id),
	INDEX ix_agent_environment_states_1 (channel_id, environment, agent_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='智能体各环境停用状态' COLLATE utf8mb4_0900_bin;

-- agent_release_records：智能体发布检查与操作记录。
CREATE TABLE agent_release_records (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '目标环境',
	agent_id VARCHAR(64) COMMENT '智能体标识',
	version_id VARCHAR(64) COMMENT '生效版本标识',
	version_label VARCHAR(128) COMMENT '版本名称',
	previous_version_id VARCHAR(64) COMMENT '原生效版本标识',
	source_revision BIGINT COMMENT '发布来源修订号',
	operation VARCHAR(32) COMMENT '操作类型',
	note VARCHAR(1024) COMMENT '操作说明',
	actor_id VARCHAR(128) COMMENT '操作人标识',
	actor_name VARCHAR(128) COMMENT '操作人名称',
	content_digest VARCHAR(64) COMMENT '内容摘要',
	dependencies_digest VARCHAR(64) COMMENT '完整依赖摘要',
	evidence_refs JSON COMMENT '评测报告引用',
	checks JSON COMMENT '发布检查证据',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_agent_release_records_0 (channel_id, id),
	INDEX ix_agent_release_records_1 (channel_id, environment, agent_id, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='智能体发布检查与操作记录' COLLATE utf8mb4_0900_bin;

-- agents：智能体资源。
CREATE TABLE agents (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	agent_code VARCHAR(64) COMMENT '调用编码',
	name VARCHAR(128) COMMENT '智能体名称',
	description LONGTEXT COMMENT '用途说明',
	owner VARCHAR(128) COMMENT '负责人标识',
	status VARCHAR(32) COMMENT '启用状态',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_agents_0 (channel_id, id),
	INDEX ix_agents_1 (channel_id, agent_code)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='智能体资源' COLLATE utf8mb4_0900_bin;

-- alert_rules：外部告警规则。
CREATE TABLE alert_rules (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	name VARCHAR(128) COMMENT '规则名称',
	kind VARCHAR(32) COMMENT '监测类型',
	threshold BIGINT COMMENT '触发次数阈值',
	window_seconds BIGINT COMMENT '监测窗口秒数',
	endpoint_id VARCHAR(64) COMMENT '告警投递端点',
	owner_key VARCHAR(64) COMMENT '创建身份摘要',
	identity JSON COMMENT '原执行身份快照',
	state VARCHAR(32) COMMENT '启停状态',
	active BOOL COMMENT '当前是否告警',
	generation BIGINT COMMENT '触发周期序号',
	`last_value` BIGINT COMMENT '最近观察次数',
	pending_events JSON COMMENT '待生成投递事件',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	client_ids JSON COMMENT '订阅的调用服务列表；空列表仅包含配置者运行',
	INDEX ix_alert_rules_0 (channel_id, id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='外部告警规则' COLLATE utf8mb4_0900_bin;

-- artifacts：受控文件与产物元数据。
CREATE TABLE artifacts (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	name VARCHAR(255) COMMENT '文件显示名称',
	content_type VARCHAR(128) COMMENT '内容媒体类型',
	object_key VARCHAR(1024) COMMENT '私有对象路径',
	size_bytes BIGINT COMMENT '文件字节数',
	sha256 VARCHAR(64) COMMENT '内容摘要',
	state VARCHAR(32) COMMENT '暂存及可用状态',
	expires_at DATETIME(6) COMMENT '保存到期时间',
	upload_expires_at DATETIME(6) COMMENT '暂存到期时间',
	registered_at DATETIME(6) COMMENT '登记完成时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_artifacts_0 (channel_id, id),
	INDEX ix_artifacts_1 (channel_id, environment, state, upload_expires_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='受控文件与产物元数据' COLLATE utf8mb4_0900_bin;

-- attempts：外部实际尝试。
CREATE TABLE attempts (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	run_id VARCHAR(64) COMMENT '运行标识',
	step_id VARCHAR(64) COMMENT '步骤标识',
	kind VARCHAR(32) COMMENT '模型或工具类别',
	target_version_id VARCHAR(64) COMMENT '实际依赖版本',
	provider_credential_id VARCHAR(64) COMMENT '实际供应商凭据',
	source_request_id VARCHAR(256) COMMENT '源请求标识',
	state VARCHAR(32) COMMENT '尝试状态',
	started_at DATETIME(6) COMMENT '开始时间',
	finished_at DATETIME(6) COMMENT '结束时间',
	error JSON COMMENT '脱敏错误',
	usage_id VARCHAR(64) COMMENT '用量账本引用',
	lease_version BIGINT COMMENT '调用所属租约代次',
	sent_at DATETIME(6) COMMENT '外部发送意图登记时间',
	retryable BOOL COMMENT '明确失败是否允许有限重试',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_attempts_0 (channel_id, id),
	INDEX ix_attempts_1 (channel_id, run_id),
	INDEX ix_attempts_2 (channel_id, step_id, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='外部实际尝试' COLLATE utf8mb4_0900_bin;

-- audit_events：操作审计元数据。
CREATE TABLE audit_events (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	actor_id VARCHAR(128) COMMENT '操作主体标识',
	action VARCHAR(128) COMMENT '操作名称',
	target_type VARCHAR(64) COMMENT '对象类型',
	target_id VARCHAR(128) COMMENT '对象标识',
	request_id VARCHAR(64) COMMENT '请求标识',
	outcome VARCHAR(32) COMMENT '操作结果',
	summary JSON COMMENT '脱敏变更摘要',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_audit_events_0 (channel_id, id),
	INDEX ix_audit_events_1 (channel_id, created_at),
	INDEX ix_audit_events_2 (channel_id, target_type, target_id),
	INDEX ix_audit_events_directory (channel_id, created_at, id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='操作审计元数据' COLLATE utf8mb4_0900_bin;

-- automation_batches：批量运行受理批次。
CREATE TABLE automation_batches (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	name VARCHAR(128) COMMENT '批次名称',
	request_digest VARCHAR(64) COMMENT '受理请求摘要',
	item_ids JSON COMMENT '批次条目关联',
	owner_key VARCHAR(64) COMMENT '执行身份摘要',
	identity JSON COMMENT '原执行身份快照',
	state VARCHAR(32) COMMENT '当前处理状态',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_automation_batches_0 (channel_id, id),
	INDEX ix_automation_batches_1 (channel_id, environment, subject_type, subject_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='批量运行受理批次' COLLATE utf8mb4_0900_bin;

-- automation_items：逐项运行派发记录。
CREATE TABLE automation_items (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	batch_id VARCHAR(64) COMMENT '来源批次标识',
	schedule_id VARCHAR(64) COMMENT '来源计划标识',
	event_id VARCHAR(128) COMMENT '外部事件或窗口编号',
	request_digest VARCHAR(64) COMMENT '条目语义摘要',
	request JSON COMMENT '受理运行输入',
	owner_key VARCHAR(64) COMMENT '执行身份摘要',
	identity JSON COMMENT '原执行身份快照',
	state VARCHAR(32) COMMENT '当前处理状态',
	lease_until DATETIME(6) COMMENT '派发租约到期',
	lease_nonce VARCHAR(64) COMMENT '派发租约凭据',
	attempts BIGINT COMMENT '受理尝试次数',
	run_id VARCHAR(64) COMMENT '关联运行标识',
	error JSON COMMENT '最近受理错误',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_automation_items_0 (channel_id, id),
	INDEX ix_automation_items_1 (channel_id, environment, subject_type, subject_id),
	INDEX ix_automation_items_2 (channel_id, state, lease_until)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='逐项运行派发记录' COLLATE utf8mb4_0900_bin;

-- automation_schedules：通用运行定时计划。
CREATE TABLE automation_schedules (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	name VARCHAR(128) COMMENT '计划名称',
	spec JSON COMMENT '周期与运行输入',
	owner_key VARCHAR(64) COMMENT '执行身份摘要',
	identity JSON COMMENT '原执行身份快照',
	state VARCHAR(32) COMMENT '当前处理状态',
	next_at DATETIME(6) COMMENT '下次触发时间',
	last_error JSON COMMENT '最近派发错误',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_automation_schedules_0 (channel_id, id),
	INDEX ix_automation_schedules_1 (channel_id, environment, subject_type, subject_id),
	INDEX ix_automation_schedules_2 (channel_id, state, next_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='通用运行定时计划' COLLATE utf8mb4_0900_bin;

-- budget_alerts：预算阈值提醒。
CREATE TABLE budget_alerts (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	rule_id VARCHAR(64) COMMENT '规则标识',
	period_start DATETIME(6) COMMENT '周期开始',
	threshold NUMERIC(12, 6) COMMENT '触发阈值',
	scope_key VARCHAR(64) COMMENT '预算范围摘要',
	status VARCHAR(32) COMMENT '提醒状态',
	first_triggered_at DATETIME(6) COMMENT '首次触发时间',
	resolved_at DATETIME(6) COMMENT '解除时间',
	transitions JSON COMMENT '解除及再次触发轨迹',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_budget_alerts_0 (channel_id, id),
	INDEX ix_budget_alerts_1 (channel_id, rule_id, period_start, threshold, scope_key)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='预算阈值提醒' COLLATE utf8mb4_0900_bin;

-- budget_policies：预算策略。
CREATE TABLE budget_policies (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	scope_type VARCHAR(64) COMMENT '预算对象类型',
	scope_id VARCHAR(128) COMMENT '预算对象标识',
	period VARCHAR(32) COMMENT '预算周期',
	timezone VARCHAR(64) COMMENT '业务时区',
	currency VARCHAR(3) COMMENT '币种',
	limit_value NUMERIC(24, 8) COMMENT '限额',
	unit VARCHAR(32) COMMENT '金额或数量单位',
	mode VARCHAR(32) COMMENT '控制模式',
	thresholds JSON COMMENT '提醒阈值',
	status VARCHAR(32) COMMENT '策略状态',
	name VARCHAR(128) COMMENT '预算名称',
	version_id VARCHAR(64) COMMENT '当前不可变策略版本',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_budget_policies_0 (channel_id, id),
	INDEX ix_budget_policies_1 (channel_id, scope_type, scope_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='预算策略' COLLATE utf8mb4_0900_bin;

-- budget_reservations：预算尝试预占。
CREATE TABLE budget_reservations (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	policy_id VARCHAR(64) COMMENT '预算策略标识',
	policy_revision BIGINT COMMENT '策略修订',
	period_start DATETIME(6) COMMENT '周期开始',
	run_id VARCHAR(64) COMMENT '运行标识',
	attempt_id VARCHAR(64) COMMENT '实际尝试标识',
	reserved_amount NUMERIC(24, 8) COMMENT '预占数量或金额',
	settled_amount NUMERIC(24, 8) COMMENT '结算数量或金额',
	currency VARCHAR(3) COMMENT '币种',
	status VARCHAR(32) COMMENT '预占状态',
	expires_at DATETIME(6) COMMENT '待核查时间',
	policy_version_id VARCHAR(64) COMMENT '预占时固定的预算策略版本',
	unit VARCHAR(32) COMMENT '占用计量单位',
	scope_snapshot JSON COMMENT '预算命中对象与周期快照',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_budget_reservations_0 (channel_id, id),
	INDEX ix_budget_reservations_1 (channel_id, policy_id, period_start),
	INDEX ix_budget_reservations_2 (channel_id, attempt_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='预算尝试预占' COLLATE utf8mb4_0900_bin;

-- builtin_roles：内置角色。
CREATE TABLE builtin_roles (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	role_code VARCHAR(64) COMMENT '角色编码',
	name VARCHAR(128) COMMENT '角色名称',
	allowed_actions JSON COMMENT '允许动作',
	grant_scope VARCHAR(32) COMMENT '授权类别',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	account_assignable BOOL COMMENT '可用于账号管理',
	menu_ids JSON COMMENT '可见菜单节点清单，空值沿用按动作生成',
	INDEX ix_builtin_roles_0 (channel_id, id),
	INDEX ix_builtin_roles_1 (channel_id, role_code)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='内置角色' COLLATE utf8mb4_0900_bin;

-- channel_code_index：系统渠道的渠道编码定位索引。
CREATE TABLE channel_code_index (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	channel_code VARCHAR(64) COMMENT '规范化渠道编码',
	target_channel_id VARCHAR(64) COMMENT '实际业务渠道标识',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_channel_code_index_0 (channel_id, id),
	INDEX ix_channel_code_index_1 (channel_id, channel_code)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='系统渠道的渠道编码定位索引' COLLATE utf8mb4_0900_bin;

-- channel_environments：渠道环境。
CREATE TABLE channel_environments (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	name VARCHAR(128) COMMENT '环境名称',
	status VARCHAR(32) COMMENT '环境状态',
	release_policy JSON COMMENT '发布策略',
	retention_policy JSON COMMENT '保存策略',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_channel_environments_0 (channel_id, id),
	INDEX ix_channel_environments_1 (channel_id, environment)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='渠道环境' COLLATE utf8mb4_0900_bin;

-- channel_keys：渠道接入密钥。
CREATE TABLE channel_keys (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	name VARCHAR(128) COMMENT '密钥名称',
	client_id VARCHAR(64) COMMENT '接入服务标识',
	secret_digest VARCHAR(64) COMMENT '不可逆密钥摘要',
	prefix VARCHAR(16) COMMENT '辨认前缀',
	suffix VARCHAR(8) COMMENT '辨认末尾',
	scopes JSON COMMENT '权限上限',
	status VARCHAR(32) COMMENT '密钥状态',
	expires_at DATETIME(6) COMMENT '失效时间',
	last_used_at DATETIME(6) COMMENT '最近使用时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_channel_keys_0 (channel_id, id),
	INDEX ix_channel_keys_1 (channel_id, client_id, status)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='渠道接入密钥' COLLATE utf8mb4_0900_bin;

-- channel_lifecycle_events：渠道生命周期交接事件。
CREATE TABLE channel_lifecycle_events (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	event_type VARCHAR(64) COMMENT '生命周期事件类型',
	target_type VARCHAR(64) COMMENT '变更对象类型',
	target_id VARCHAR(64) COMMENT '变更对象标识',
	environment VARCHAR(16) COMMENT '受影响环境',
	payload JSON COMMENT '变更事实与原始归属',
	acknowledgements JSON COMMENT '已处理模块及时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_channel_lifecycle_events_0 (channel_id, id),
	INDEX ix_channel_lifecycle_events_1 (channel_id, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='渠道生命周期交接事件' COLLATE utf8mb4_0900_bin;

-- channel_memberships：渠道成员关系。
CREATE TABLE channel_memberships (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	user_id VARCHAR(64) COMMENT '平台账号标识',
	roles JSON COMMENT '角色清单',
	environments JSON COMMENT '授权环境',
	status VARCHAR(32) COMMENT '成员状态',
	granted_by VARCHAR(128) COMMENT '授权人标识',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_channel_memberships_0 (channel_id, id),
	INDEX ix_channel_memberships_1 (channel_id, user_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='渠道成员关系' COLLATE utf8mb4_0900_bin;

-- channels：渠道主档。
CREATE TABLE channels (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	channel_code VARCHAR(64) COMMENT '渠道稳定编码',
	name VARCHAR(128) COMMENT '渠道名称',
	status VARCHAR(32) COMMENT '渠道状态',
	owner VARCHAR(128) COMMENT '负责人名称',
	archived_at DATETIME(6) COMMENT '归档时间',
	retention_policy JSON COMMENT '保存策略',
	budget_policy_refs JSON COMMENT '预算策略引用',
	rate_limit_policy_refs JSON COMMENT '限流策略引用',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_channels_0 (channel_id, id),
	INDEX ix_channels_1 (channel_id, channel_code)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='渠道主档' COLLATE utf8mb4_0900_bin;

-- checkpoints：自有流程恢复点。
CREATE TABLE checkpoints (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	run_id VARCHAR(64) COMMENT '运行标识',
	namespace VARCHAR(128) COMMENT '流程命名空间',
	checkpoint_key VARCHAR(128) COMMENT '恢复点逻辑标识',
	parent_key VARCHAR(128) COMMENT '父恢复点',
	lease_version BIGINT COMMENT '提交租约代次',
	release_snapshot_id VARCHAR(64) COMMENT '固定依赖快照',
	state_ref VARCHAR(64) COMMENT '状态内容引用',
	metadata JSON COMMENT '无原文恢复元数据',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_checkpoints_0 (channel_id, id),
	INDEX ix_checkpoints_1 (channel_id, run_id, namespace, checkpoint_key)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='自有流程恢复点' COLLATE utf8mb4_0900_bin;

-- context_snapshots：实际模型上下文快照。
CREATE TABLE context_snapshots (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	run_id VARCHAR(64) COMMENT '运行标识',
	included_message_ids JSON COMMENT '实际包含消息',
	summary_version VARCHAR(64) COMMENT '摘要版本',
	memory_refs JSON COMMENT '记忆具体版本',
	truncation JSON COMMENT '删减原因及范围',
	conversation_id VARCHAR(64) COMMENT '所属会话',
	summary_id VARCHAR(64) COMMENT '引用摘要标识',
	summary_source_ids JSON COMMENT '摘要来源消息集合',
	policy_version VARCHAR(32) COMMENT '上下文选择策略版本',
	required_characters BIGINT COMMENT '必要指令和当前任务字符数',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_context_snapshots_0 (channel_id, id),
	INDEX ix_context_snapshots_1 (channel_id, run_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='实际模型上下文快照' COLLATE utf8mb4_0900_bin;

-- conversation_summaries：可溯源会话摘要。
CREATE TABLE conversation_summaries (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	conversation_id VARCHAR(64) COMMENT '会话标识',
	source_message_ids JSON COMMENT '来源消息集合',
	version BIGINT COMMENT '摘要版本',
	content LONGTEXT COMMENT '摘要内容',
	status VARCHAR(32) COMMENT '摘要有效状态',
	truncation JSON COMMENT '摘要删减记录',
	generation_run_id VARCHAR(64) COMMENT '受控摘要生成运行',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_conversation_summaries_0 (channel_id, id),
	INDEX ix_conversation_summaries_1 (channel_id, conversation_id, version)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='可溯源会话摘要' COLLATE utf8mb4_0900_bin;

-- conversation_turns：会话轮次与消息幂等。
CREATE TABLE conversation_turns (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	conversation_id VARCHAR(64) COMMENT '会话标识',
	client_message_id VARCHAR(128) COMMENT '客户端消息标识',
	request_digest VARCHAR(64) COMMENT '语义请求摘要',
	user_message_id VARCHAR(64) COMMENT '用户消息标识',
	run_id VARCHAR(64) COMMENT '运行标识',
	sequence BIGINT COMMENT '会话内顺序',
	assistant_message_id VARCHAR(64) COMMENT '助手消息标识',
	agent_version_id VARCHAR(64) COMMENT '本轮冻结智能体版本',
	version_label VARCHAR(128) COMMENT '本轮版本名称',
	input JSON COMMENT '不可变业务输入',
	input_schema JSON COMMENT '本轮输入契约',
	output_schema JSON COMMENT '本轮输出契约',
	source_run_id VARCHAR(64) COMMENT '普通追问来源运行',
	confirmed_conditions JSON COMMENT '上一轮已确认条件',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_conversation_turns_0 (channel_id, id),
	INDEX ix_conversation_turns_1 (channel_id, conversation_id, client_message_id),
	INDEX ix_conversation_turns_2 (channel_id, conversation_id, sequence)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='会话轮次与消息幂等' COLLATE utf8mb4_0900_bin;

-- conversations：业务会话。
CREATE TABLE conversations (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	agent_id VARCHAR(64) COMMENT '智能体标识',
	title VARCHAR(255) COMMENT '会话标题',
	status VARCHAR(32) COMMENT '会话状态',
	active_run_id VARCHAR(64) COMMENT '当前生成运行',
	expires_at DATETIME(6) COMMENT '保留到期时间',
	agent_code VARCHAR(128) COMMENT '智能体调用编码',
	agent_name VARCHAR(128) COMMENT '智能体名称',
	subject_name VARCHAR(128) COMMENT '主体名称',
	input_schema JSON COMMENT '已接受的输入契约',
	next_sequence BIGINT COMMENT '下一条消息顺序',
	next_turn_sequence BIGINT COMMENT '下一轮顺序',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_conversations_0 (channel_id, id),
	INDEX ix_conversations_1 (channel_id, environment, subject_type, subject_id, updated_at),
	INDEX ix_conversations_2 (channel_id, environment, created_at, id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='业务会话' COLLATE utf8mb4_0900_bin;

-- credentials：加密服务凭据。
CREATE TABLE credentials (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	purpose VARCHAR(32) COMMENT '凭据用途',
	ciphertext BLOB COMMENT '认证加密密文',
	key_version VARCHAR(64) COMMENT '加密密钥版本',
	state VARCHAR(32) COMMENT '凭据状态',
	secret_value LONGTEXT COMMENT 'MCP 凭据原文',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_credentials_0 (channel_id, id),
	INDEX ix_credentials_1 (channel_id, environment, purpose, state)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='加密服务凭据' COLLATE utf8mb4_0900_bin;

-- custom_roles：平台与渠道自定义角色。
CREATE TABLE custom_roles (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	name VARCHAR(128) COMMENT '角色名称',
	allowed_actions JSON COMMENT '角色动作上限',
	state VARCHAR(32) COMMENT '启停状态',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	menu_ids JSON COMMENT '可见菜单节点清单，空值沿用按动作生成',
	grant_scope VARCHAR(32) COMMENT '授权类别',
	INDEX ix_custom_roles_0 (channel_id, id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='平台与渠道自定义角色' COLLATE utf8mb4_0900_bin;

-- delegation_keys：委托签名验证密钥。
CREATE TABLE delegation_keys (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	client_id VARCHAR(64) COMMENT '接入服务标识',
	key_reference VARCHAR(64) COMMENT '独立签名密文引用',
	algorithm VARCHAR(32) COMMENT '固定签名算法',
	issuer VARCHAR(128) COMMENT '可信签发者',
	audience VARCHAR(128) COMMENT '委托受众',
	max_ttl_seconds INTEGER COMMENT '最长委托有效秒数',
	clock_skew_seconds INTEGER COMMENT '允许时钟偏差秒数',
	status VARCHAR(32) COMMENT '密钥状态',
	not_before DATETIME(6) COMMENT '密钥生效时间',
	expires_at DATETIME(6) COMMENT '密钥到期时间',
	rotated_from VARCHAR(64) COMMENT '轮换前密钥编号',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_delegation_keys_0 (channel_id, id),
	INDEX ix_delegation_keys_1 (channel_id, environment, client_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='委托签名验证密钥' COLLATE utf8mb4_0900_bin;

-- delegation_nonces：已验证业务委托与请求防重放。
CREATE TABLE delegation_nonces (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	client_id VARCHAR(64) COMMENT '稳定接入服务标识',
	kid VARCHAR(64) COMMENT '验签密钥编号',
	nonce_digest VARCHAR(64) COMMENT '随机数摘要',
	request_digest VARCHAR(64) COMMENT '实际请求绑定摘要',
	claims_digest VARCHAR(64) COMMENT '完整委托声明摘要',
	claims JSON COMMENT '验签后权限声明',
	resolved_scope JSON COMMENT '验签后渠道环境主体',
	expires_at DATETIME(6) COMMENT '防重放声明有效时间',
	retain_until DATETIME(6) COMMENT '防重放记录最早清理时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_delegation_nonces_0 (channel_id, id),
	INDEX ix_delegation_nonces_1 (channel_id, environment, client_id, nonce_digest),
	INDEX ix_delegation_nonces_2 (channel_id, environment, retain_until)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='已验证业务委托与请求防重放' COLLATE utf8mb4_0900_bin;

-- deletion_jobs：删除传播任务。
CREATE TABLE deletion_jobs (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	marker_id VARCHAR(64) COMMENT '删除标记',
	scope_description JSON COMMENT '待清理范围元数据',
	affected_resources JSON COMMENT '影响引用清单',
	state VARCHAR(32) COMMENT '清理状态',
	completed_at DATETIME(6) COMMENT '完成时间',
	conversation_id VARCHAR(64) COMMENT '删除目标会话',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_deletion_jobs_0 (channel_id, id),
	INDEX ix_deletion_jobs_1 (channel_id, state, created_at),
	INDEX ix_deletion_jobs_2 (channel_id, conversation_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='删除传播任务' COLLATE utf8mb4_0900_bin;

-- deletion_markers：不可恢复使用的删除标记。
CREATE TABLE deletion_markers (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	target_type VARCHAR(64) COMMENT '删除对象类型',
	target_id VARCHAR(128) COMMENT '删除对象标识',
	reason_code VARCHAR(64) COMMENT '删除原因类别',
	requested_by VARCHAR(128) COMMENT '删除申请主体',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_deletion_markers_0 (channel_id, id),
	INDEX ix_deletion_markers_1 (channel_id, environment, target_type, target_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='不可恢复使用的删除标记' COLLATE utf8mb4_0900_bin;

-- deletion_receipts：删除清理完成证明。
CREATE TABLE deletion_receipts (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	job_id VARCHAR(64) COMMENT '删除任务',
	marker_digest VARCHAR(64) COMMENT '删除清单摘要',
	counts JSON COMMENT '各类已完成数量',
	proof_digest VARCHAR(64) COMMENT '完成证明摘要',
	completed_at DATETIME(6) COMMENT '完成时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_deletion_receipts_0 (channel_id, id),
	INDEX ix_deletion_receipts_1 (channel_id, job_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='删除清理完成证明' COLLATE utf8mb4_0900_bin;

-- deletion_work_items：可重试模块清理步骤。
CREATE TABLE deletion_work_items (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	job_id VARCHAR(64) COMMENT '删除任务',
	handler_key VARCHAR(128) COMMENT '登记处理器',
	target_type VARCHAR(64) COMMENT '对象类型',
	target_id VARCHAR(128) COMMENT '对象标识',
	state VARCHAR(32) COMMENT '步骤状态',
	attempts INTEGER COMMENT '尝试次数',
	next_attempt_at DATETIME(6) COMMENT '重试时间',
	last_error VARCHAR(64) COMMENT '脱敏错误类别',
	lease_token VARCHAR(64) COMMENT '执行租约令牌',
	lease_until DATETIME(6) COMMENT '执行租约到期时间',
	completed_at DATETIME(6) COMMENT '完成时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_deletion_work_items_0 (channel_id, id),
	INDEX ix_deletion_work_items_1 (channel_id, job_id, handler_key, target_id),
	INDEX ix_deletion_work_items_2 (channel_id, state, next_attempt_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='可重试模块清理步骤' COLLATE utf8mb4_0900_bin;

-- dispatch_outbox：可靠调度投递意图。
CREATE TABLE dispatch_outbox (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	run_id VARCHAR(64) COMMENT '运行标识',
	state VARCHAR(32) COMMENT '投递状态',
	dispatch_attempts INTEGER COMMENT '投递次数',
	next_attempt_at DATETIME(6) COMMENT '下次补偿时间',
	last_error VARCHAR(64) COMMENT '脱敏错误类别',
	delivery_version BIGINT COMMENT '本次投递声明代次',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_dispatch_outbox_0 (channel_id, id),
	INDEX ix_dispatch_outbox_1 (channel_id, state, next_attempt_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='可靠调度投递意图' COLLATE utf8mb4_0900_bin;

-- evaluation_cases：不可变评测样本。
CREATE TABLE evaluation_cases (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	dataset_id VARCHAR(64) COMMENT '所属样本集',
	case_key VARCHAR(128) COMMENT '跨版本样本定位键',
	title VARCHAR(255) COMMENT '样本标题',
	payload JSON COMMENT '输入、断言、标签、人工结论和来源',
	fixture_id VARCHAR(64) COMMENT '固定工具数据引用',
	previous_case_id VARCHAR(64) COMMENT '修改前样本引用',
	invalidated BOOL COMMENT '来源已失效',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_evaluation_cases_0 (channel_id, id),
	INDEX ix_evaluation_cases_1 (channel_id, dataset_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='不可变评测样本' COLLATE utf8mb4_0900_bin;

-- evaluation_dataset_versions：不可变评测样本及标签版本。
CREATE TABLE evaluation_dataset_versions (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	dataset_id VARCHAR(64) COMMENT '所属样本集',
	version_label VARCHAR(128) COMMENT '版本名称',
	content_digest VARCHAR(64) COMMENT '数据和标签摘要',
	case_ids JSON COMMENT '固定样本清单',
	reference_versions JSON COMMENT '参考资料版本',
	captured_at DATETIME(6) COMMENT '固定数据时间',
	reference_digests JSON COMMENT '参考资料版本内容摘要',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_evaluation_dataset_versions_0 (channel_id, id),
	INDEX ix_evaluation_dataset_versions_1 (channel_id, dataset_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='不可变评测样本及标签版本' COLLATE utf8mb4_0900_bin;

-- evaluation_datasets：评测样本集。
CREATE TABLE evaluation_datasets (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	name VARCHAR(128) COMMENT '样本集名称',
	scenario VARCHAR(64) COMMENT '适用能力类别',
	owner VARCHAR(128) COMMENT '负责人',
	applicability LONGTEXT COMMENT '适用范围',
	current_version_id VARCHAR(64) COMMENT '当前样本版本',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_evaluation_datasets_0 (channel_id, id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='评测样本集' COLLATE utf8mb4_0900_bin;

-- evaluation_fixtures：评测工具夹具。
CREATE TABLE evaluation_fixtures (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	payload JSON COMMENT '按工具版本及参数匹配的固定结果',
	captured_at DATETIME(6) COMMENT '夹具采集时间',
	invalidated BOOL COMMENT '夹具来源已失效',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_evaluation_fixtures_0 (channel_id, id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='评测工具夹具' COLLATE utf8mb4_0900_bin;

-- evaluation_reports：可追溯评测报告。
CREATE TABLE evaluation_reports (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	evaluation_id VARCHAR(64) COMMENT '所属评测',
	report_digest VARCHAR(64) COMMENT '报告证据摘要',
	payload JSON COMMENT '覆盖、差异、阻断、用量与耗时',
	reproducible BOOL COMMENT '来源和固定数据可复现',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_evaluation_reports_0 (channel_id, id),
	INDEX ix_evaluation_reports_1 (channel_id, evaluation_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='可追溯评测报告' COLLATE utf8mb4_0900_bin;

-- evaluation_results：评测单例及重跑记录。
CREATE TABLE evaluation_results (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	evaluation_id VARCHAR(64) COMMENT '所属评测',
	case_id VARCHAR(64) COMMENT '固定样本',
	candidate_id VARCHAR(64) COMMENT '冻结候选',
	attempt_number INTEGER COMMENT '样本重跑序号',
	run_id VARCHAR(64) COMMENT '统一运行',
	state VARCHAR(32) COMMENT '单例状态',
	judgment JSON COMMENT '确定性与语义判定',
	human_label JSON COMMENT '独立人工结论',
	claimed_at DATETIME(6) COMMENT '派发占位时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_evaluation_results_0 (channel_id, id),
	INDEX ix_evaluation_results_1 (channel_id, evaluation_id),
	INDEX ix_evaluation_results_2 (channel_id, run_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='评测单例及重跑记录' COLLATE utf8mb4_0900_bin;

-- evaluations：批量评测调度任务。
CREATE TABLE evaluations (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	name VARCHAR(128) COMMENT '任务名称',
	dataset_version_id VARCHAR(64) COMMENT '固定样本版本',
	dataset_digest VARCHAR(64) COMMENT '固定数据和标签摘要',
	candidate_snapshots JSON COMMENT '冻结候选及完整依赖清单',
	baseline_evaluation_id VARCHAR(64) COMMENT '历史基线评测',
	baseline_candidate_id VARCHAR(64) COMMENT '基线候选',
	execution_mode VARCHAR(32) COMMENT '工具数据执行模式',
	config JSON COMMENT '并发、预算、阈值和发布评测配置',
	identity JSON COMMENT '原始调用身份',
	state VARCHAR(32) COMMENT '调度状态',
	human_review JSON COMMENT '独立报告人工审阅',
	expires_at DATETIME(6) COMMENT '发布证据有效期',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_evaluations_0 (channel_id, id),
	INDEX ix_evaluations_1 (channel_id, state)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='批量评测调度任务' COLLATE utf8mb4_0900_bin;

-- evidence_refs：证据定位与授权范围。
CREATE TABLE evidence_refs (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	source_type VARCHAR(64) COMMENT '来源类型',
	source_id VARCHAR(128) COMMENT '来源标识',
	source_version VARCHAR(128) COMMENT '来源版本',
	observed_at DATETIME(6) COMMENT '观测时间',
	location JSON COMMENT '字段路径或文本位置',
	title VARCHAR(255) COMMENT '授权范围内的来源名称',
	artifact_id VARCHAR(64) COMMENT '内容产物标识',
	authorization_scope JSON COMMENT '授权范围摘要',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_evidence_refs_0 (channel_id, id),
	INDEX ix_evidence_refs_1 (channel_id, source_type, source_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='证据定位与授权范围' COLLATE utf8mb4_0900_bin;

-- iam_menus：平台菜单目录。
CREATE TABLE iam_menus (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	name VARCHAR(128) COMMENT '菜单名称',
	kind VARCHAR(16) COMMENT '节点类型',
	parent_id VARCHAR(64) COMMENT '父节点标识',
	page_key VARCHAR(64) COMMENT '已注册页面标识',
	action_key VARCHAR(64) COMMENT '按钮动作标识',
	workspace VARCHAR(16) COMMENT '适用工作区',
	sort_order INTEGER COMMENT '显示顺序',
	visible BOOL COMMENT '菜单可见标记',
	active BOOL COMMENT '启用标记',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_iam_menus_0 (channel_id, id),
	INDEX ix_iam_menus_1 (channel_id, parent_id, sort_order)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='平台菜单目录' COLLATE utf8mb4_0900_bin;

-- iam_revocations：认证撤销补偿记录。
CREATE TABLE iam_revocations (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	kind VARCHAR(32) COMMENT '撤销索引类别',
	target_id VARCHAR(128) COMMENT '撤销对象标识或令牌摘要',
	cutoff_at DATETIME(6) COMMENT '撤销签发时间上界',
	completed_at DATETIME(6) COMMENT '缓存补偿完成时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_iam_revocations_0 (channel_id, id),
	INDEX ix_iam_revocations_1 (channel_id, kind, target_id),
	INDEX ix_iam_revocations_2 (completed_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='认证撤销补偿记录' COLLATE utf8mb4_0900_bin;

-- key_identity_index：系统渠道密钥身份索引。
CREATE TABLE key_identity_index (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	key_lookup_digest VARCHAR(64) COMMENT '完整密钥不可逆摘要',
	key_id VARCHAR(64) COMMENT '目标密钥标识',
	target_channel_id VARCHAR(64) COMMENT '目标渠道标识',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_key_identity_index_0 (channel_id, id),
	INDEX ix_key_identity_index_1 (channel_id, key_lookup_digest)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='系统渠道密钥身份索引' COLLATE utf8mb4_0900_bin;

-- key_rotations：密钥轮换记录。
CREATE TABLE key_rotations (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	old_key_id VARCHAR(64) COMMENT '原密钥标识',
	new_key_id VARCHAR(64) COMMENT '新密钥标识',
	overlap_until DATETIME(6) COMMENT '重叠截止时间',
	operator_id VARCHAR(128) COMMENT '操作人标识',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_key_rotations_0 (channel_id, id),
	INDEX ix_key_rotations_1 (channel_id, old_key_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='密钥轮换记录' COLLATE utf8mb4_0900_bin;

-- mcp_checks：握手与健康检查。
CREATE TABLE mcp_checks (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	connection_id VARCHAR(64) COMMENT '连接标识',
	negotiated_version VARCHAR(64) COMMENT '协商协议版本',
	server_info JSON COMMENT '远端信息',
	capabilities JSON COMMENT '协商能力',
	health_status VARCHAR(32) COMMENT '健康状态',
	latency_ms INTEGER COMMENT '耗时毫秒',
	error_category VARCHAR(64) COMMENT '错误类别',
	connection_revision BIGINT COMMENT '检查时连接配置修订',
	operation VARCHAR(32) COMMENT '检查操作类型',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_mcp_checks_0 (channel_id, id),
	INDEX ix_mcp_checks_1 (channel_id, connection_id, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='握手与健康检查' COLLATE utf8mb4_0900_bin;

-- mcp_connections：远程工具连接。
CREATE TABLE mcp_connections (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	name VARCHAR(128) COMMENT '连接名称',
	transport VARCHAR(64) COMMENT '传输类型',
	endpoint VARCHAR(2048) COMMENT '服务地址',
	credential_ref VARCHAR(64) COMMENT '凭据引用',
	timeouts JSON COMMENT '超时策略',
	status VARCHAR(32) COMMENT '启用状态',
	health_status VARCHAR(32) COMMENT '健康状态',
	configuration_revision BIGINT COMMENT '连接配置修订',
	credential_revision BIGINT COMMENT '凭据版本',
	tested_revision BIGINT COMMENT '握手通过的配置修订',
	discovered_revision BIGINT COMMENT '发现通过的配置修订',
	failure_count INTEGER COMMENT '连续失败次数',
	health_policy JSON COMMENT '检查频率与失败阈值',
	last_check_at DATETIME(6) COMMENT '最近检查时间',
	auth_failed BOOL COMMENT '凭据失效阻断状态',
	health_actor_id VARCHAR(64) COMMENT '健康检查授权成员',
	next_check_at DATETIME(6) COMMENT '下次健康检查时间',
	authentication JSON COMMENT '鉴权方式、令牌地址与应用标识；凭据单独保存',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_mcp_connections_0 (channel_id, id),
	INDEX ix_mcp_connections_1 (channel_id, environment, status),
	INDEX ix_mcp_connections_2 (channel_id, environment, next_check_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='远程工具连接' COLLATE utf8mb4_0900_bin;

-- mcp_discoveries：远端工具发现快照。
CREATE TABLE mcp_discoveries (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	connection_id VARCHAR(64) COMMENT '连接标识',
	connection_revision BIGINT COMMENT '连接修订',
	tool_definitions JSON COMMENT '远端工具定义',
	schema_hashes JSON COMMENT '定义摘要',
	negotiated_version VARCHAR(64) COMMENT '发现协商协议版本',
	credential_revision BIGINT COMMENT '发现时凭据版本',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_mcp_discoveries_0 (channel_id, id),
	INDEX ix_mcp_discoveries_1 (channel_id, connection_id, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='远端工具发现快照' COLLATE utf8mb4_0900_bin;

-- mcp_imports：远端工具导入映射。
CREATE TABLE mcp_imports (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	connection_id VARCHAR(64) COMMENT '连接标识',
	discovery_id VARCHAR(64) COMMENT '发现快照',
	remote_tool_name VARCHAR(256) COMMENT '远端名称',
	local_tool_id VARCHAR(64) COMMENT '本地工具标识',
	schema_hash VARCHAR(64) COMMENT '远端结构摘要',
	imported_version VARCHAR(64) COMMENT '本地导入版本',
	effect_type VARCHAR(32) COMMENT '管理员核定影响类型',
	name VARCHAR(128) COMMENT '本地工具显示名称',
	input_schema JSON COMMENT '固定本地输入契约',
	contract_status VARCHAR(32) COMMENT '固定契约可用状态',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_mcp_imports_0 (channel_id, id),
	INDEX ix_mcp_imports_1 (channel_id, connection_id, remote_tool_name),
	INDEX ix_mcp_imports_2 (channel_id, environment, connection_id, discovery_id, remote_tool_name)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='远端工具导入映射' COLLATE utf8mb4_0900_bin;

-- mcp_oauth_flows：MCP 一次性授权流程。
CREATE TABLE mcp_oauth_flows (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	connection_id VARCHAR(64) COMMENT '连接标识',
	configuration_revision BIGINT COMMENT '连接配置修订',
	profile_id VARCHAR(64) COMMENT '身份提供方配置',
	profile_digest VARCHAR(64) COMMENT '提供方配置摘要',
	ownership VARCHAR(16) COMMENT '凭据归属类型',
	owner_id VARCHAR(64) COMMENT '归属身份摘要',
	state VARCHAR(16) COMMENT '授权流程状态',
	verifier_ref VARCHAR(64) COMMENT 'PKCE 验证凭据引用',
	expires_at DATETIME(6) COMMENT '授权流程截止时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_mcp_oauth_flows_0 (channel_id, id),
	INDEX ix_mcp_oauth_flows_1 (channel_id, environment, connection_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='MCP 一次性授权流程' COLLATE utf8mb4_0900_bin;

-- mcp_oauth_tokens：MCP 分身份委托凭据。
CREATE TABLE mcp_oauth_tokens (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	connection_id VARCHAR(64) COMMENT '连接标识',
	profile_id VARCHAR(64) COMMENT '身份提供方配置',
	profile_digest VARCHAR(64) COMMENT '提供方配置摘要',
	ownership VARCHAR(16) COMMENT '凭据归属类型',
	owner_id VARCHAR(64) COMMENT '归属身份摘要',
	credential_ref VARCHAR(64) COMMENT '委托令牌凭据引用',
	expires_at DATETIME(6) COMMENT '访问令牌截止时间',
	state VARCHAR(16) COMMENT '委托状态',
	refresh_until DATETIME(6) COMMENT '刷新占用截止时间',
	refresh_nonce VARCHAR(64) COMMENT '刷新互斥代次',
	authorized_at DATETIME(6) COMMENT '授权发起时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_mcp_oauth_tokens_0 (channel_id, id),
	INDEX ix_mcp_oauth_tokens_1 (channel_id, environment, connection_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='MCP 分身份委托凭据' COLLATE utf8mb4_0900_bin;

-- memories：主体结构化记忆。
CREATE TABLE memories (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	memory_type VARCHAR(64) COMMENT '记忆类别',
	`key` VARCHAR(128) COMMENT '记忆属性名',
	display_name VARCHAR(128) COMMENT '属性中文名',
	value JSON COMMENT '记忆值',
	status VARCHAR(32) COMMENT '确认及有效状态',
	expires_at DATETIME(6) COMMENT '有效截止时间',
	current_version_id VARCHAR(64) COMMENT '当前版本',
	confirmed BOOL COMMENT '是否经过明确确认',
	observed_at DATETIME(6) COMMENT '最新有效依据时间',
	trust_level INTEGER COMMENT '有效来源可信等级',
	usage_count BIGINT COMMENT '实际使用次数',
	subject_name VARCHAR(255) COMMENT '主体可读名称',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	source_mode VARCHAR(16) COMMENT '来源有效性模式：独立依据或全部依赖',
	INDEX ix_memories_0 (channel_id, id),
	INDEX ix_memories_1 (channel_id, environment, subject_type, subject_id, `key`, status)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='主体结构化记忆' COLLATE utf8mb4_0900_bin;

-- memory_consolidations：会话归档与人物画像后台整理任务。
CREATE TABLE memory_consolidations (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	conversation_id VARCHAR(128) COMMENT '来源会话标识',
	source_run_id VARCHAR(128) COMMENT '恢复授权与冻结策略的来源运行',
	source_message_ids JSON COMMENT '本批完整消息引用',
	memory_ids JSON COMMENT '已生成的归档及画像引用',
	generation_run_id VARCHAR(128) COMMENT '后台生成运行标识',
	state VARCHAR(32) COMMENT '后台整理状态',
	attempt INTEGER COMMENT '生成轮次',
	next_attempt_at DATETIME(6) COMMENT '下次允许整理时间',
	error_code VARCHAR(128) COMMENT '最后失败原因编码',
	settings JSON COMMENT '冻结策略与属性，不包含会话原文',
	preference_revision INTEGER COMMENT '受理时主体偏好修订',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_memory_consolidations_0 (channel_id, id),
	INDEX ix_memory_consolidations_1 (channel_id, state, next_attempt_at),
	INDEX ix_memory_consolidations_2 (channel_id, conversation_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='会话归档与人物画像后台整理任务' COLLATE utf8mb4_0900_bin;

-- memory_deletion_jobs：主体记忆删除清理意图。
CREATE TABLE memory_deletion_jobs (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	memory_ids JSON COMMENT '待清理记忆标识集合',
	state VARCHAR(32) COMMENT '清理进度',
	completed_at DATETIME(6) COMMENT '完成时间',
	kind VARCHAR(16) COMMENT '单项遗忘或主体清空',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_memory_deletion_jobs_0 (channel_id, id),
	INDEX ix_memory_deletion_jobs_1 (channel_id, environment, subject_type, subject_id, state)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='主体记忆删除清理意图' COLLATE utf8mb4_0900_bin;

-- memory_embeddings：按主体和模型版本隔离的记忆向量。
CREATE TABLE memory_embeddings (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	memory_id VARCHAR(64) COMMENT '来源记忆标识',
	memory_version_id VARCHAR(64) COMMENT '来源记忆版本',
	model_version_id VARCHAR(64) COMMENT '向量模型版本',
	run_id VARCHAR(64) COMMENT '生成向量的运行',
	dimensions INTEGER COMMENT '向量维度',
	embedding JSON COMMENT '记忆向量',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_memory_embeddings_0 (channel_id, id),
	INDEX ix_memory_embeddings_1 (channel_id, environment, subject_type, subject_id, model_version_id),
	INDEX ix_memory_embeddings_2 (channel_id, memory_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='按主体和模型版本隔离的记忆向量' COLLATE utf8mb4_0900_bin;

-- memory_index_tasks：Milvus 向量同步与删除持久化任务。
CREATE TABLE memory_index_tasks (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	dimensions INTEGER COMMENT '向量维度',
	operation VARCHAR(16) COMMENT '待同步的写入或删除操作',
	state VARCHAR(16) COMMENT '同步任务状态',
	lease_token VARCHAR(64) COMMENT '当前执行租约标识',
	lease_until DATETIME(6) COMMENT '租约失效时间',
	next_attempt_at DATETIME(6) COMMENT '下次同步时间',
	attempts INTEGER COMMENT '连续失败次数',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_memory_index_tasks_0 (channel_id, id),
	INDEX ix_memory_index_tasks_1 (channel_id, next_attempt_at, state),
	INDEX ix_memory_index_tasks_2 (channel_id, environment, subject_type, subject_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='Milvus 向量同步与删除持久化任务' COLLATE utf8mb4_0900_bin;

-- memory_policies：渠道与智能体记忆策略。
CREATE TABLE memory_policies (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	agent_id VARCHAR(64) COMMENT '智能体标识',
	allowed_types JSON COMMENT '允许类别',
	write_mode VARCHAR(32) COMMENT '写入方式',
	ttl_seconds INTEGER COMMENT '保留秒数',
	max_items INTEGER COMMENT '存储条数上限',
	retrieval_limit INTEGER COMMENT '召回条数上限',
	read_enabled BOOL COMMENT '是否允许读取',
	suggest_enabled BOOL COMMENT '是否允许建议写入',
	failure_mode VARCHAR(16) COMMENT '读取故障处理方式',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	attributes JSON COMMENT '渠道可配置的画像属性定义',
	consolidation JSON COMMENT '后台归档与画像整理策略',
	INDEX ix_memory_policies_0 (channel_id, id),
	INDEX ix_memory_policies_1 (channel_id, agent_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='渠道与智能体记忆策略' COLLATE utf8mb4_0900_bin;

-- memory_preferences：主体长期记忆开关。
CREATE TABLE memory_preferences (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	enabled BOOL COMMENT '是否启用',
	changed_by VARCHAR(128) COMMENT '变更主体',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_memory_preferences_0 (channel_id, id),
	INDEX ix_memory_preferences_1 (channel_id, environment, subject_type, subject_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='主体长期记忆开关' COLLATE utf8mb4_0900_bin;

-- memory_retrievals：运行记忆召回记录。
CREATE TABLE memory_retrievals (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	run_id VARCHAR(64) COMMENT '运行标识',
	memory_refs JSON COMMENT '使用的记忆版本',
	selection_reason JSON COMMENT '选择依据',
	warnings JSON COMMENT '脱敏降级提示',
	agent_id VARCHAR(64) COMMENT '使用记忆的智能体',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_memory_retrievals_0 (channel_id, id),
	INDEX ix_memory_retrievals_1 (channel_id, run_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='运行记忆召回记录' COLLATE utf8mb4_0900_bin;

-- memory_sources：记忆有效来源。
CREATE TABLE memory_sources (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	memory_id VARCHAR(64) COMMENT '记忆标识',
	source_type VARCHAR(64) COMMENT '来源类型',
	source_id VARCHAR(128) COMMENT '来源标识',
	source_version VARCHAR(128) COMMENT '来源版本',
	observed_at DATETIME(6) COMMENT '观测时间',
	evidence_id VARCHAR(64) COMMENT '证据标识',
	status VARCHAR(32) COMMENT '来源状态',
	authority VARCHAR(32) COMMENT '来源权限类型',
	trust_level INTEGER COMMENT '来源可信等级',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_memory_sources_0 (channel_id, id),
	INDEX ix_memory_sources_1 (channel_id, memory_id),
	INDEX ix_memory_sources_2 (channel_id, environment, subject_type, subject_id, source_type, source_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='记忆有效来源' COLLATE utf8mb4_0900_bin;

-- memory_versions：记忆变更版本。
CREATE TABLE memory_versions (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	memory_id VARCHAR(64) COMMENT '记忆标识',
	previous_version_id VARCHAR(64) COMMENT '前版本',
	value JSON COMMENT '历史内容空槽位，不保存原文',
	changed_by VARCHAR(128) COMMENT '变更主体',
	reason VARCHAR(512) COMMENT '变更原因',
	version_number INTEGER COMMENT '版本序号',
	status VARCHAR(32) COMMENT '变更后状态',
	source_ids JSON COMMENT '有效来源记录集合',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_memory_versions_0 (channel_id, id),
	INDEX ix_memory_versions_1 (channel_id, memory_id, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='记忆变更版本' COLLATE utf8mb4_0900_bin;

-- messages：会话消息。
CREATE TABLE messages (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	conversation_id VARCHAR(64) COMMENT '会话标识',
	`role` VARCHAR(32) COMMENT '消息角色',
	content_parts JSON COMMENT '文本及附件引用',
	run_id VARCHAR(64) COMMENT '关联运行',
	status VARCHAR(32) COMMENT '消息完成状态',
	sequence BIGINT COMMENT '会话消息顺序',
	turn_id VARCHAR(64) COMMENT '关联轮次',
	event_sequence BIGINT COMMENT '已投影运行事件顺序',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_messages_0 (channel_id, id),
	INDEX ix_messages_1 (channel_id, conversation_id, created_at, id),
	INDEX ix_messages_2 (channel_id, conversation_id, sequence)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='会话消息' COLLATE utf8mb4_0900_bin;

-- model_connections：模型供应商连接。
CREATE TABLE model_connections (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	name VARCHAR(128) COMMENT '连接名称',
	protocol VARCHAR(64) COMMENT '协议类型',
	endpoint VARCHAR(2048) COMMENT '服务地址',
	credential_ref VARCHAR(64) COMMENT '凭据标识',
	timeout_seconds INTEGER COMMENT '调用超时秒数',
	status VARCHAR(32) COMMENT '连接启用状态',
	health_status VARCHAR(32) COMMENT '最近健康状态',
	provider_id VARCHAR(64) COMMENT '供应商字典引用',
	current_version_id VARCHAR(64) COMMENT '当前连接版本标识',
	health_reason VARCHAR(512) COMMENT '最近健康异常原因',
	health_checked_at DATETIME(6) COMMENT '最近健康检查时间',
	validation_revision BIGINT COMMENT '能力验证语义修订号',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_model_connections_0 (channel_id, id),
	INDEX ix_model_connections_1 (channel_id, environment, status)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='模型供应商连接' COLLATE utf8mb4_0900_bin;

-- model_routes：模型路由资源。
CREATE TABLE model_routes (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	code VARCHAR(64) COMMENT '路由编码',
	name VARCHAR(128) COMMENT '路由名称',
	status VARCHAR(32) COMMENT '启用状态',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_model_routes_0 (channel_id, id),
	INDEX ix_model_routes_1 (channel_id, code)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='模型路由资源' COLLATE utf8mb4_0900_bin;

-- model_tests：模型验证记录。
CREATE TABLE model_tests (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	model_id VARCHAR(64) COMMENT '模型标识',
	config_revision BIGINT COMMENT '配置修订号',
	cases JSON COMMENT '验证用例',
	results JSON COMMENT '验证结果',
	latency_ms INTEGER COMMENT '耗时毫秒',
	attempt_ids JSON COMMENT '实际尝试引用',
	config_digest VARCHAR(64) COMMENT '冻结配置摘要',
	execution JSON COMMENT '冻结调试执行描述',
	state VARCHAR(32) COMMENT '验证执行状态',
	run_id VARCHAR(64) COMMENT '统一调试运行标识',
	error_code VARCHAR(64) COMMENT '验证失败类别',
	reason VARCHAR(512) COMMENT '验证失败原因',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_model_tests_0 (channel_id, id),
	INDEX ix_model_tests_1 (channel_id, model_id, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='模型验证记录' COLLATE utf8mb4_0900_bin;

-- models：模型映射。
CREATE TABLE models (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	model_code VARCHAR(64) COMMENT '稳定模型编码',
	name VARCHAR(128) COMMENT '模型名称',
	connection_id VARCHAR(64) COMMENT '连接标识',
	provider_model_name VARCHAR(256) COMMENT '供应商模型名',
	context_limit INTEGER COMMENT '上下文上限',
	status VARCHAR(32) COMMENT '启用状态',
	capabilities JSON COMMENT '按能力记录支持及验证状态',
	verified_at DATETIME(6) COMMENT '验证时间',
	current_version_id VARCHAR(64) COMMENT '当前模型映射版本标识',
	parameters JSON COMMENT '模型默认参数',
	parameter_allowlist JSON COMMENT '模型允许的参数清单',
	validation_revision BIGINT COMMENT '能力验证语义修订号',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_models_0 (channel_id, id),
	INDEX ix_models_1 (channel_id, model_code)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='模型映射' COLLATE utf8mb4_0900_bin;

-- platform_accounts：平台账号。
CREATE TABLE platform_accounts (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	login_name VARCHAR(128) COMMENT '规范化登录名',
	display_name VARCHAR(128) COMMENT '显示名称',
	password_hash VARCHAR(512) COMMENT '密码安全摘要',
	platform_roles JSON COMMENT '平台角色清单',
	status VARCHAR(32) COMMENT '账号状态',
	must_change_password BOOL COMMENT '首次修改密码标记',
	credential_updated_at DATETIME(6) COMMENT '凭据更新时间',
	credential_version BIGINT COMMENT '凭据撤销代次',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	role_id VARCHAR(64) COMMENT '账号选择的管理角色',
	role_ids JSON COMMENT '账号选择的管理角色清单，空值兼容旧单角色',
	INDEX ix_platform_accounts_0 (channel_id, id),
	INDEX ix_platform_accounts_1 (channel_id, login_name),
	INDEX ix_platform_accounts_directory (channel_id, status, login_name, id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='平台账号' COLLATE utf8mb4_0900_bin;

-- platform_limits：系统渠道平台总准入限额。
CREATE TABLE platform_limits (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	limit_code VARCHAR(64) COMMENT '平台限额编码',
	name VARCHAR(128) COMMENT '限额名称',
	kind VARCHAR(32) COMMENT '并发或请求数量类别',
	period VARCHAR(32) COMMENT '限额周期',
	timezone VARCHAR(64) COMMENT '周期时区',
	limit_value NUMERIC(24, 8) COMMENT '限额数量',
	unit VARCHAR(32) COMMENT '计量单位',
	status VARCHAR(32) COMMENT '限额启用状态',
	replaces_id VARCHAR(64) COMMENT '前一个限额版本标识',
	effective_at DATETIME(6) COMMENT '该限额版本生效时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_platform_limits_0 (channel_id, id),
	INDEX ix_platform_limits_1 (channel_id, limit_code),
	INDEX ix_platform_limits_current (channel_id, limit_code, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='系统渠道平台总准入限额' COLLATE utf8mb4_0900_bin;

-- platform_quota_occupancies：平台配额运行占用。
CREATE TABLE platform_quota_occupancies (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	target_channel_id VARCHAR(64) COMMENT '实际业务渠道标识',
	run_id VARCHAR(64) COMMENT '实际运行标识',
	limit_id VARCHAR(64) COMMENT '平台限额版本',
	limit_code VARCHAR(64) COMMENT '稳定平台限额编码',
	period_start DATETIME(6) COMMENT '计数周期起点',
	unit VARCHAR(32) COMMENT '请求量或并发单位',
	status VARCHAR(32) COMMENT '占用状态',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_platform_quota_held (channel_id, limit_code, status),
	INDEX ix_platform_quota_occupancies_0 (channel_id, id),
	INDEX ix_platform_quota_occupancies_1 (channel_id, limit_code, period_start),
	INDEX ix_platform_quota_occupancies_2 (channel_id, target_channel_id, run_id),
	INDEX ix_platform_quota_period (channel_id, limit_code, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='平台配额运行占用' COLLATE utf8mb4_0900_bin;

-- price_versions：模型价格版本。
CREATE TABLE price_versions (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	model_id VARCHAR(64) COMMENT '模型标识',
	currency VARCHAR(3) COMMENT '币种',
	price_items JSON COMMENT '各计价维度和子集关系',
	unit VARCHAR(64) COMMENT '计价单位',
	effective_at DATETIME(6) COMMENT '价格生效时间',
	source VARCHAR(1024) COMMENT '价格来源',
	name VARCHAR(128) COMMENT '价格版本名称',
	subset_relations JSON COMMENT '适配器计量子集关系',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_price_versions_0 (channel_id, id),
	INDEX ix_price_versions_1 (channel_id, model_id, effective_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='模型价格版本' COLLATE utf8mb4_0900_bin;

-- prompt_samples：提示词调试样例。
CREATE TABLE prompt_samples (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	prompt_id VARCHAR(64) COMMENT '提示词资源标识',
	title VARCHAR(128) COMMENT '样例名称',
	input JSON COMMENT '样例输入',
	expected_constraints JSON COMMENT '预期断言',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_prompt_samples_0 (channel_id, id),
	INDEX ix_prompt_samples_1 (channel_id, prompt_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='提示词调试样例' COLLATE utf8mb4_0900_bin;

-- prompt_tests：提示词调试记录。
CREATE TABLE prompt_tests (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	version_id VARCHAR(64) COMMENT '版本标识',
	draft_revision BIGINT COMMENT '草稿修订',
	release_snapshot_id VARCHAR(64) COMMENT '冻结快照标识',
	model_route_version VARCHAR(64) COMMENT '模型路由版本',
	rendered_input_ref VARCHAR(64) COMMENT '渲染内容引用',
	run_id VARCHAR(64) COMMENT '调试运行标识',
	frozen_version JSON COMMENT '调试时固定的提示词版本',
	sample_snapshot JSON COMMENT '调试时固定的样例与预期断言',
	rendered_input JSON COMMENT '保持来源分区的完整渲染快照',
	descriptor_digest VARCHAR(64) COMMENT '调试执行描述摘要',
	sample_id VARCHAR(64) COMMENT '调试样例标识',
	model_route_name VARCHAR(128) COMMENT '调试时的模型路由名称',
	status VARCHAR(32) COMMENT '调试受理状态',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_prompt_tests_0 (channel_id, id),
	INDEX ix_prompt_tests_1 (channel_id, version_id),
	INDEX ix_prompt_tests_2 (channel_id, environment, version_id, descriptor_digest)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='提示词调试记录' COLLATE utf8mb4_0900_bin;

-- prompts：提示词资源。
CREATE TABLE prompts (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	prompt_code VARCHAR(64) COMMENT '提示词编码',
	name VARCHAR(128) COMMENT '提示词名称',
	purpose VARCHAR(512) COMMENT '使用用途',
	owner VARCHAR(128) COMMENT '负责人标识',
	status VARCHAR(32) COMMENT '资源状态',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_prompts_0 (channel_id, id),
	INDEX ix_prompts_1 (channel_id, prompt_code)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='提示词资源' COLLATE utf8mb4_0900_bin;

-- provider_catalog：供应商字典。
CREATE TABLE provider_catalog (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	code VARCHAR(64) COMMENT '供应商编码',
	name VARCHAR(128) COMMENT '供应商名称',
	protocols JSON COMMENT '支持协议族',
	template_content JSON COMMENT '无凭据连接模板',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_provider_catalog_0 (channel_id, id),
	INDEX ix_provider_catalog_1 (channel_id, code)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='供应商字典' COLLATE utf8mb4_0900_bin;

-- provider_statements：供应商账单核查批次。
CREATE TABLE provider_statements (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	name VARCHAR(128) COMMENT '账单来源名称',
	source_digest VARCHAR(64) COMMENT '导入来源摘要',
	version VARCHAR(64) COMMENT '来源账单版本',
	connection_id VARCHAR(64) COMMENT '模型连接标识',
	currency VARCHAR(3) COMMENT '账单币种',
	start_at DATETIME(6) COMMENT '核查开始时间',
	end_at DATETIME(6) COMMENT '核查结束时间',
	`lines` JSON COMMENT '规范化供应商记录',
	owner_key VARCHAR(64) COMMENT '创建身份摘要',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_provider_statements_0 (channel_id, id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='供应商账单核查批次' COLLATE utf8mb4_0900_bin;

-- recovery_barriers：内容恢复屏障。
CREATE TABLE recovery_barriers (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	state VARCHAR(32) COMMENT '恢复屏障状态',
	recovery_id VARCHAR(64) COMMENT '本次恢复标识',
	marker_digest VARCHAR(64) COMMENT '已校验删除账本摘要',
	verified_at DATETIME(6) COMMENT '删除账本核对时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_recovery_barriers_0 (channel_id, id),
	INDEX ix_recovery_barriers_1 (channel_id, environment, subject_type, subject_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='内容恢复屏障' COLLATE utf8mb4_0900_bin;

-- release_mappings：环境生效版本映射。
CREATE TABLE release_mappings (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	resource_type VARCHAR(64) COMMENT '资源类型',
	resource_id VARCHAR(64) COMMENT '稳定资源标识',
	version_id VARCHAR(64) COMMENT '生效版本标识',
	published_by VARCHAR(128) COMMENT '发布主体标识',
	release_note VARCHAR(1024) COMMENT '发布说明',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_release_mappings_0 (channel_id, id),
	INDEX ix_release_mappings_1 (channel_id, environment, resource_type, resource_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='环境生效版本映射' COLLATE utf8mb4_0900_bin;

-- release_snapshots：执行依赖冻结快照。
CREATE TABLE release_snapshots (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	run_id VARCHAR(64) COMMENT '所属运行标识',
	purpose VARCHAR(32) COMMENT '使用用途',
	versions JSON COMMENT '具体版本及草稿内容快照',
	dependencies_digest VARCHAR(64) COMMENT '全量依赖摘要',
	output_schema JSON COMMENT '固定输出结构',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_release_snapshots_0 (channel_id, id),
	INDEX ix_release_snapshots_1 (channel_id, environment, run_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='执行依赖冻结快照' COLLATE utf8mb4_0900_bin;

-- resource_grants：资源授权。
CREATE TABLE resource_grants (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	grantee_type VARCHAR(64) COMMENT '受权主体类型',
	grantee_id VARCHAR(128) COMMENT '受权主体标识',
	resource_type VARCHAR(64) COMMENT '资源类型',
	resource_id VARCHAR(64) COMMENT '资源标识',
	allowed_actions JSON COMMENT '允许动作',
	environments JSON COMMENT '授权环境',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_resource_grants_0 (channel_id, id),
	INDEX ix_resource_grants_1 (channel_id, grantee_type, grantee_id),
	INDEX ix_resource_grants_2 (channel_id, resource_type, resource_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='资源授权' COLLATE utf8mb4_0900_bin;

-- resource_references：版本引用关系。
CREATE TABLE resource_references (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	source_version_id VARCHAR(64) COMMENT '引用方版本标识',
	target_version_id VARCHAR(64) COMMENT '被引用版本标识',
	target_resource_type VARCHAR(64) COMMENT '被引用资源类型',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_resource_references_0 (channel_id, id),
	INDEX ix_resource_references_1 (channel_id, target_version_id),
	INDEX ix_resource_references_2 (channel_id, source_version_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='版本引用关系' COLLATE utf8mb4_0900_bin;

-- resource_uses：资源实际使用记录，每个资源在同一运行中计一次。
CREATE TABLE resource_uses (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '使用环境',
	resource_type VARCHAR(64) COMMENT '资源类型',
	resource_id VARCHAR(64) COMMENT '稳定资源标识',
	resource_name VARCHAR(128) COMMENT '使用时资源名称',
	run_id VARCHAR(64) COMMENT '关联运行标识',
	agent_name VARCHAR(128) COMMENT '使用时智能体名称',
	caller_name VARCHAR(128) COMMENT '使用时调用方名称',
	purpose VARCHAR(32) COMMENT '运行用途',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_resource_uses_0 (channel_id, id),
	INDEX ix_resource_uses_1 (channel_id, resource_type, resource_id),
	INDEX ix_resource_uses_2 (channel_id, run_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='资源实际使用记录，每个资源在同一运行中计一次' COLLATE utf8mb4_0900_bin;

-- resource_versions：资源版本与草稿。
CREATE TABLE resource_versions (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	resource_type VARCHAR(64) COMMENT '资源类型',
	resource_id VARCHAR(64) COMMENT '稳定资源标识',
	version_label VARCHAR(128) COMMENT '可读版本名称',
	state VARCHAR(32) COMMENT '版本状态',
	content JSON COMMENT '版本内容',
	content_digest VARCHAR(64) COMMENT '规范化内容摘要',
	dependencies JSON COMMENT '固定依赖清单',
	dependencies_digest VARCHAR(64) COMMENT '依赖摘要',
	output_schema JSON COMMENT '输出结构定义',
	created_by VARCHAR(128) COMMENT '创建主体标识',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_resource_versions_0 (channel_id, id),
	INDEX ix_resource_versions_1 (channel_id, resource_type, resource_id, state)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='资源版本与草稿' COLLATE utf8mb4_0900_bin;

-- run_contents：运行敏感内容引用。
CREATE TABLE run_contents (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	run_id VARCHAR(64) COMMENT '所属运行',
	kind VARCHAR(32) COMMENT '内容用途',
	payload JSON COMMENT '受控内容正文',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_run_contents_0 (channel_id, id),
	INDEX ix_run_contents_1 (channel_id, run_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='运行敏感内容引用' COLLATE utf8mb4_0900_bin;

-- run_events：可补发运行事件。
CREATE TABLE run_events (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	run_id VARCHAR(64) COMMENT '运行标识',
	sequence BIGINT COMMENT '运行内单调序号',
	event_type VARCHAR(64) COMMENT '事件类别',
	payload_ref VARCHAR(64) COMMENT '事件内容引用',
	expires_at DATETIME(6) COMMENT '事件失效时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_run_events_0 (channel_id, id),
	INDEX ix_run_events_1 (channel_id, run_id, sequence)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='可补发运行事件' COLLATE utf8mb4_0900_bin;

-- run_idempotency：接入请求幂等。
CREATE TABLE run_idempotency (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	client_id VARCHAR(64) COMMENT '接入服务稳定标识',
	agent_id VARCHAR(64) COMMENT '智能体标识',
	`key` VARCHAR(128) COMMENT '调用方幂等键',
	request_digest VARCHAR(64) COMMENT '语义请求摘要',
	run_id VARCHAR(64) COMMENT '首次受理运行',
	expires_at DATETIME(6) COMMENT '最早可清理时间',
	identity_type VARCHAR(32) COMMENT '稳定身份来源类型',
	identity_id VARCHAR(128) COMMENT '稳定调用服务或管理操作者',
	scope_digest VARCHAR(64) COMMENT '幂等范围摘要',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_run_idempotency_0 (channel_id, id),
	INDEX ix_run_idempotency_1 (channel_id, environment, subject_type, subject_id, client_id, agent_id, `key`),
	INDEX ix_run_idempotency_2 (channel_id, scope_digest, `key`)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='接入请求幂等' COLLATE utf8mb4_0900_bin;

-- run_leases：工作进程执行租约。
CREATE TABLE run_leases (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	run_id VARCHAR(64) COMMENT '运行标识',
	worker_id VARCHAR(128) COMMENT '进程标识',
	lease_version BIGINT COMMENT '租约代次',
	heartbeat_at DATETIME(6) COMMENT '最近续租时间',
	expires_at DATETIME(6) COMMENT '租约到期时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_run_leases_0 (channel_id, id),
	INDEX ix_run_leases_1 (channel_id, run_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='工作进程执行租约' COLLATE utf8mb4_0900_bin;

-- run_occupancies：运行会话执行占用。
CREATE TABLE run_occupancies (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	conversation_id VARCHAR(64) COMMENT '占用会话',
	run_id VARCHAR(64) COMMENT '占用运行',
	state VARCHAR(32) COMMENT '占用状态',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_run_occupancies_0 (channel_id, id),
	INDEX ix_run_occupancies_1 (channel_id, environment, conversation_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='运行会话执行占用' COLLATE utf8mb4_0900_bin;

-- run_recoveries：执行租约恢复判断记录。
CREATE TABLE run_recoveries (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	run_id VARCHAR(64) COMMENT '所属运行',
	lease_version BIGINT COMMENT '失效租约代次',
	decision VARCHAR(32) COMMENT '恢复判断结果',
	reason VARCHAR(64) COMMENT '脱敏原因类别',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_run_recoveries_0 (channel_id, id),
	INDEX ix_run_recoveries_1 (channel_id, run_id, lease_version)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='执行租约恢复判断记录' COLLATE utf8mb4_0900_bin;

-- run_steps：执行步骤。
CREATE TABLE run_steps (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	run_id VARCHAR(64) COMMENT '运行标识',
	node_key VARCHAR(128) COMMENT '流程节点',
	state VARCHAR(32) COMMENT '步骤状态',
	input_ref VARCHAR(64) COMMENT '输入引用',
	output_ref VARCHAR(64) COMMENT '输出引用',
	checkpoint_ref VARCHAR(64) COMMENT '恢复点',
	sequence BIGINT COMMENT '步骤顺序',
	attempt_count INTEGER COMMENT '实际尝试累计次数',
	lease_version BIGINT COMMENT '最近有效提交租约代次',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_run_steps_0 (channel_id, id),
	INDEX ix_run_steps_1 (channel_id, run_id, sequence)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='执行步骤' COLLATE utf8mb4_0900_bin;

-- runs：逻辑执行任务。
CREATE TABLE runs (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	source_type VARCHAR(32) COMMENT '发起来源',
	client_id VARCHAR(64) COMMENT '接入服务',
	key_id VARCHAR(64) COMMENT '渠道密钥',
	actor_id VARCHAR(128) COMMENT '管理发起人',
	agent_id VARCHAR(64) COMMENT '智能体',
	agent_version_id VARCHAR(64) COMMENT '智能体版本',
	release_snapshot_id VARCHAR(64) COMMENT '冻结依赖快照',
	purpose VARCHAR(32) COMMENT '调用用途',
	state VARCHAR(32) COMMENT '技术状态',
	deadline DATETIME(6) COMMENT '全程截止时间',
	conversation_id VARCHAR(64) COMMENT '会话标识',
	parent_run_id VARCHAR(64) COMMENT '来源运行',
	input_ref VARCHAR(64) COMMENT '输入内容引用',
	result_ref VARCHAR(64) COMMENT '正式结果引用',
	partial_output_ref VARCHAR(64) COMMENT '部分输出引用',
	error JSON COMMENT '脱敏错误',
	completed_at DATETIME(6) COMMENT '终结时间',
	agent_code VARCHAR(128) COMMENT '智能体调用编码',
	agent_name VARCHAR(128) COMMENT '受理时智能体名称',
	identity JSON COMMENT '不含访问令牌的原始身份',
	execution_policy JSON COMMENT '冻结执行限额与步骤策略',
	timeout_seconds INTEGER COMMENT '全程时限秒数',
	timeout_source VARCHAR(128) COMMENT '时限配置来源',
	event_sequence BIGINT COMMENT '最后事件序号',
	resources_released BOOL COMMENT '终态占用已释放',
	recovery_count INTEGER COMMENT '租约失效恢复次数',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_runs_0 (channel_id, id),
	INDEX ix_runs_1 (channel_id, environment, state, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='逻辑执行任务' COLLATE utf8mb4_0900_bin;

-- service_clients：业务接入服务。
CREATE TABLE service_clients (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	name VARCHAR(128) COMMENT '服务名称',
	scopes JSON COMMENT '权限上限',
	status VARCHAR(32) COMMENT '服务状态',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_service_clients_0 (channel_id, id),
	INDEX ix_service_clients_1 (channel_id, environment, status)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='业务接入服务' COLLATE utf8mb4_0900_bin;

-- skill_files：技能版本文件清单。
CREATE TABLE skill_files (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	version_id VARCHAR(64) COMMENT '技能版本',
	relative_path VARCHAR(1024) COMMENT '包内路径',
	content_type VARCHAR(128) COMMENT '媒体类型',
	size_bytes BIGINT COMMENT '文件字节数',
	sha256 VARCHAR(64) COMMENT '文件摘要',
	artifact_id VARCHAR(64) COMMENT '受控内容引用',
	loadable BOOL COMMENT '当前是否可加载',
	unavailable_reason LONGTEXT COMMENT '不可加载原因',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_skill_files_0 (channel_id, id),
	INDEX ix_skill_files_1 (channel_id, version_id, relative_path(512))
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='技能版本文件清单' COLLATE utf8mb4_0900_bin;

-- skill_tests：技能加载测试。
CREATE TABLE skill_tests (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	version_id VARCHAR(64) COMMENT '技能版本',
	release_snapshot_id VARCHAR(64) COMMENT '上下文冻结快照',
	selected_files JSON COMMENT '实际加载文件',
	run_id VARCHAR(64) COMMENT '运行标识',
	result JSON COMMENT '测试结果',
	context_snapshot JSON COMMENT '加载输入与版本冻结快照',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_skill_tests_0 (channel_id, id),
	INDEX ix_skill_tests_1 (channel_id, version_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='技能加载测试' COLLATE utf8mb4_0900_bin;

-- skills：技能资源。
CREATE TABLE skills (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	skill_code VARCHAR(64) COMMENT '技能编码',
	name VARCHAR(128) COMMENT '技能名称',
	description LONGTEXT COMMENT '用途说明',
	owner VARCHAR(128) COMMENT '负责人',
	tags JSON COMMENT '发现标签',
	status VARCHAR(32) COMMENT '启用状态',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_skills_0 (channel_id, id),
	INDEX ix_skills_1 (channel_id, skill_code)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='技能资源' COLLATE utf8mb4_0900_bin;

-- source_links：内容来源与派生关系。
CREATE TABLE source_links (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	source_type VARCHAR(64) COMMENT '来源类型',
	source_id VARCHAR(128) COMMENT '来源标识',
	derived_type VARCHAR(64) COMMENT '派生类型',
	derived_id VARCHAR(128) COMMENT '派生标识',
	source_version VARCHAR(128) COMMENT '来源版本',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_source_links_0 (channel_id, id),
	INDEX ix_source_links_1 (channel_id, environment, source_type, source_id),
	INDEX ix_source_links_2 (channel_id, environment, derived_type, derived_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='内容来源与派生关系' COLLATE utf8mb4_0900_bin;

-- subject_review_bindings：当前主体复核的固定 MCP 绑定。
CREATE TABLE subject_review_bindings (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	client_id VARCHAR(64) COMMENT '受限接入服务标识',
	connection_id VARCHAR(64) COMMENT '固定身份复核连接',
	discovery_id VARCHAR(64) COMMENT '授权时发现快照',
	remote_tool_name VARCHAR(256) COMMENT '专用身份复核工具名',
	tool_name VARCHAR(128) COMMENT '身份工具显示名称',
	connection_revision BIGINT COMMENT '授权时连接配置修订',
	schema_hash VARCHAR(128) COMMENT '授权时身份工具契约摘要',
	timeout_seconds INTEGER COMMENT '身份复核超时秒数',
	enabled BOOL COMMENT '是否允许身份复核',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_subject_review_bindings_0 (channel_id, id),
	INDEX ix_subject_review_bindings_1 (channel_id, environment, client_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='当前主体复核的固定 MCP 绑定' COLLATE utf8mb4_0900_bin;

-- tool_calls：工具实际调用。
CREATE TABLE tool_calls (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	run_id VARCHAR(64) COMMENT '运行标识',
	step_id VARCHAR(64) COMMENT '步骤标识',
	attempt_id VARCHAR(64) COMMENT '尝试标识',
	tool_version_id VARCHAR(64) COMMENT '工具版本',
	args_digest VARCHAR(64) COMMENT '参数摘要',
	state VARCHAR(32) COMMENT '调用状态',
	source_request_id VARCHAR(256) COMMENT '源请求标识',
	result_ref VARCHAR(64) COMMENT '结果内容引用',
	latency_ms INTEGER COMMENT '耗时毫秒',
	error JSON COMMENT '脱敏错误',
	tool_id VARCHAR(64) COMMENT '工具资源标识',
	redacted_arguments JSON COMMENT '脱敏输入参数',
	result_summary JSON COMMENT '结果结构摘要',
	evidence_ids JSON COMMENT '有效证据标识集合',
	attempt JSON COMMENT '独立尝试状态',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_tool_calls_0 (channel_id, id),
	INDEX ix_tool_calls_1 (channel_id, run_id),
	INDEX ix_tool_calls_2 (channel_id, attempt_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='工具实际调用' COLLATE utf8mb4_0900_bin;

-- tools：工具资源。
CREATE TABLE tools (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	tool_code VARCHAR(64) COMMENT '调用编码',
	name VARCHAR(128) COMMENT '工具名称',
	description LONGTEXT COMMENT '用途说明',
	source_type VARCHAR(32) COMMENT '来源类型',
	owner VARCHAR(128) COMMENT '负责人',
	status VARCHAR(32) COMMENT '启用状态',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_tools_0 (channel_id, id),
	INDEX ix_tools_1 (channel_id, tool_code)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='工具资源' COLLATE utf8mb4_0900_bin;

-- transaction_lock_slots：服务层事务互斥固定锁槽。
CREATE TABLE transaction_lock_slots (
	channel_id VARCHAR(64) COMMENT '锁目录所属系统渠道',
	slot INTEGER COMMENT '包含锁顺序分组的固定槽位',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_transaction_lock_slots_0 (channel_id, slot)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='服务层事务互斥固定锁槽' COLLATE utf8mb4_0900_bin;

-- usage_adjustments：用量计价修正轨迹。
CREATE TABLE usage_adjustments (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	usage_id VARCHAR(64) COMMENT '账本标识',
	event_id VARCHAR(64) COMMENT '来源事件标识',
	previous_revision BIGINT COMMENT '前次修订',
	amount_delta NUMERIC(24, 8) COMMENT '金额变动',
	currency VARCHAR(3) COMMENT '币种',
	reason VARCHAR(512) COMMENT '修正原因',
	calculation JSON COMMENT '核算依据',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_usage_adjustments_0 (channel_id, id),
	INDEX ix_usage_adjustments_1 (channel_id, usage_id, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='用量计价修正轨迹' COLLATE utf8mb4_0900_bin;

-- usage_aggregates：可重算用量聚合。
CREATE TABLE usage_aggregates (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	dimensions JSON COMMENT '统计维度',
	dimensions_digest VARCHAR(64) COMMENT '统计范围摘要',
	period_start DATETIME(6) COMMENT '周期开始',
	currency VARCHAR(3) COMMENT '币种',
	totals JSON COMMENT '数量及完整性分组',
	ledger_watermark DATETIME(6) COMMENT '账本处理水位',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_usage_aggregates_0 (channel_id, id),
	INDEX ix_usage_aggregates_1 (channel_id, dimensions_digest, period_start, currency)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='可重算用量聚合' COLLATE utf8mb4_0900_bin;

-- usage_events：供应商用量来源事件。
CREATE TABLE usage_events (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	attempt_id VARCHAR(64) COMMENT '尝试标识',
	connection_id VARCHAR(64) COMMENT '供应商连接标识',
	source_request_id VARCHAR(256) COMMENT '供应商请求标识',
	event_version BIGINT COMMENT '事件版本',
	raw_usage JSON COMMENT '原始计量值与子集口径',
	usage_status VARCHAR(32) COMMENT '事件完整性',
	observed_at DATETIME(6) COMMENT '观测时间',
	event_payload JSON COMMENT '经契约验证的完整计量事件',
	payload_digest VARCHAR(64) COMMENT '事件内容摘要',
	applied BOOL COMMENT '是否成为当前有效计量',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_usage_events_0 (channel_id, id),
	INDEX ix_usage_events_1 (channel_id, connection_id, source_request_id, event_version)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='供应商用量来源事件' COLLATE utf8mb4_0900_bin;

-- usage_exchange_rates：核算展示汇率版本。
CREATE TABLE usage_exchange_rates (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	base_currency VARCHAR(3) COMMENT '原始币种',
	quote_currency VARCHAR(3) COMMENT '折算币种',
	rate NUMERIC(24, 8) COMMENT '折算汇率',
	effective_at DATETIME(6) COMMENT '汇率日期',
	source VARCHAR(1024) COMMENT '汇率来源',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_usage_exchange_rates_0 (channel_id, id),
	INDEX ix_usage_exchange_rates_1 (channel_id, base_currency, quote_currency, effective_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='核算展示汇率版本' COLLATE utf8mb4_0900_bin;

-- usage_exports：用量导出请求。
CREATE TABLE usage_exports (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	requested_by VARCHAR(128) COMMENT '导出人',
	filters JSON COMMENT '授权筛选条件',
	timezone VARCHAR(64) COMMENT '业务时区',
	channel_range JSON COMMENT '明确授权的渠道集合',
	state VARCHAR(32) COMMENT '导出状态',
	artifact_id VARCHAR(64) COMMENT '产物标识',
	scope_snapshot JSON COMMENT '创建时受信查询范围',
	object_key VARCHAR(1024) COMMENT '渠道隔离的私有产物路径',
	metadata JSON COMMENT '计量口径及价格完整性',
	error_message VARCHAR(512) COMMENT '导出失败原因',
	expires_at DATETIME(6) COMMENT '导出失效时间',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_usage_exports_0 (channel_id, id),
	INDEX ix_usage_exports_1 (channel_id, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='用量导出请求' COLLATE utf8mb4_0900_bin;

-- usage_records：实际尝试用量账本。
CREATE TABLE usage_records (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	run_id VARCHAR(64) COMMENT '运行标识',
	attempt_id VARCHAR(64) COMMENT '实际尝试标识',
	source_type VARCHAR(32) COMMENT '来源类型',
	client_id VARCHAR(64) COMMENT '接入服务标识',
	key_id VARCHAR(64) COMMENT '渠道密钥标识',
	actor_id VARCHAR(128) COMMENT '管理主体标识',
	agent_id VARCHAR(64) COMMENT '智能体标识',
	model_id VARCHAR(64) COMMENT '模型标识',
	connection_id VARCHAR(64) COMMENT '供应商连接标识',
	purpose VARCHAR(32) COMMENT '调用用途',
	input_tokens BIGINT COMMENT '输入数量',
	output_tokens BIGINT COMMENT '输出数量',
	cached_tokens BIGINT COMMENT '缓存子集数量',
	reasoning_tokens BIGINT COMMENT '推理子集数量',
	raw_usage_ref VARCHAR(64) COMMENT '原始用量引用',
	usage_status VARCHAR(32) COMMENT '用量完整性',
	pricing_status VARCHAR(32) COMMENT '计价完整性',
	price_version_id VARCHAR(64) COMMENT '价格版本标识',
	amount NUMERIC(24, 8) COMMENT '核算金额',
	currency VARCHAR(3) COMMENT '币种',
	snapshot JSON COMMENT '受信运行来源及可读名称快照',
	normalized_tokens JSON COMMENT '按维度归一化用量',
	subset_relations JSON COMMENT '计量子集关系',
	calculation JSON COMMENT '当前核算公式及依据',
	upper_tokens JSON COMMENT '调用前核准的计量上限',
	upper_amount NUMERIC(24, 8) COMMENT '调用前预占金额',
	state VARCHAR(32) COMMENT '实际调用结算状态',
	sent_at DATETIME(6) COMMENT '供应商调用发送前登记时间',
	outcome VARCHAR(32) COMMENT '实际尝试结果',
	latest_event_version BIGINT COMMENT '当前有效来源事件版本',
	latest_event_id VARCHAR(64) COMMENT '当前有效来源事件',
	final_reported BOOL COMMENT '是否收到供应商最终用量',
	source_request_id VARCHAR(256) COMMENT '固定供应商请求标识',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_usage_records_0 (channel_id, id),
	INDEX ix_usage_records_1 (channel_id, attempt_id),
	INDEX ix_usage_records_2 (channel_id, created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='实际尝试用量账本' COLLATE utf8mb4_0900_bin;

-- webhook_deliveries：持久化事件投递。
CREATE TABLE webhook_deliveries (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	endpoint_id VARCHAR(64) COMMENT '投递端点标识',
	event_id VARCHAR(64) COMMENT '稳定事件编号',
	kind VARCHAR(64) COMMENT '事件类型',
	payload JSON COMMENT '最小事件正文',
	owner_key VARCHAR(64) COMMENT '执行身份摘要',
	state VARCHAR(32) COMMENT '当前处理状态',
	attempts BIGINT COMMENT '累计投递次数',
	next_at DATETIME(6) COMMENT '下次尝试时间',
	lease_until DATETIME(6) COMMENT '投递租约到期',
	lease_nonce VARCHAR(64) COMMENT '投递租约凭据',
	error JSON COMMENT '最近投递错误',
	http_status BIGINT COMMENT '最近响应状态',
	cycle_attempts BIGINT COMMENT '本轮自动投递次数，人工重投重新计数',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	INDEX ix_webhook_deliveries_0 (channel_id, id),
	INDEX ix_webhook_deliveries_1 (channel_id, environment, subject_type, subject_id),
	INDEX ix_webhook_deliveries_2 (channel_id, state, next_at)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='持久化事件投递' COLLATE utf8mb4_0900_bin;

-- webhook_endpoints：事件投递端点。
CREATE TABLE webhook_endpoints (
	id VARCHAR(64) COMMENT '记录标识',
	channel_id VARCHAR(64) COMMENT '所属渠道标识',
	created_at DATETIME(6) COMMENT '创建时间',
	updated_at DATETIME(6) COMMENT '更新时间',
	revision BIGINT COMMENT '并发修订号',
	environment VARCHAR(16) COMMENT '所属环境',
	subject_type VARCHAR(64) COMMENT '业务主体类型',
	subject_id VARCHAR(128) COMMENT '业务主体编号',
	name VARCHAR(128) COMMENT '端点名称',
	url LONGTEXT COMMENT '固定接收地址',
	secret_ref VARCHAR(64) COMMENT '签名密钥密文引用',
	events JSON COMMENT '订阅事件类型',
	owner_key VARCHAR(64) COMMENT '执行身份摘要',
	identity JSON COMMENT '原执行身份快照',
	state VARCHAR(32) COMMENT '当前处理状态',
	is_deleted BOOL COMMENT '是否已逻辑删除',
	client_ids JSON COMMENT '订阅的调用服务列表；空列表仅包含配置者运行',
	INDEX ix_webhook_endpoints_0 (channel_id, id),
	INDEX ix_webhook_endpoints_1 (channel_id, environment, subject_type, subject_id)
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='事件投递端点' COLLATE utf8mb4_0900_bin;

COMMIT;
SELECT RELEASE_LOCK(SHA2(CONCAT('creativity:migrate:', DATABASE()), 256));
