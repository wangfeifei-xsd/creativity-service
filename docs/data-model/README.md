# Creativity P0 数据模型索引

模型版本 **2.1.0**；需求基线 **v0.7**；技术基线 **v1.6**。

本档案覆盖全部 P0 持久化对象。机器清单为 [catalog.json](catalog.json)，字段文档由 `scripts/render_data_model.py` 生成。共享基础的代码定义位于 `core/database/baseline_v0001.json`；各模块归档中的 `revision` 保留原始修订来源，供模型归属与一致性检查使用。[MySQL 8 初始基线](../../alembic/mysql_versions/0048_mysql_milvus.py) 冻结至 `0048_mysql_milvus`；原 PostgreSQL 修订仅保留作历史来源。开发规范引用 [rule.md](../../../rule.md)。

空库初始化按顺序执行表结构 [sql/init.sql](../../sql/init.sql) 和数据 [sql/init_data.sql](../../sql/init_data.sql)；执行、维护与验证方法见 [初始化说明](../../sql/README.md)。

需求 15–17 为可选配置示例，原 12 张领域表设计已撤销，不属于待建库清单。平台上下文统一为渠道、环境与主体；业务工具统一由 MCP 接入，固定业务 HTTP 协议已移除。

对象逻辑标识（如 run_id、conversation_id、version_id）在所属表统一物理存为 `id`；关联字段保留业务名称。渠道主档的 `id` 与 `channel_id` 相等。业务必填由服务入口验证，所有普通列均显式赋值。JSON 中的类型化内容由所属模块 schema 校验；敏感级别按来源可向上提升。

[关系与生命周期](relations.md) · [服务不变量](service-invariants.md) · [存储职责](storage-map.md) · [迁移兼容](changes.md)

| 表/对象 | 所属模块 | 需求 | 状态 | 负责方案 |
| --- | --- | --- | --- | --- |
| `resource_versions` | [公共设施](modules/core.md) | 00-需求总纲.md | 已实现 | 03 |
| `release_mappings` | [公共设施](modules/core.md) | 00-需求总纲.md | 已实现 | 03 |
| `release_snapshots` | [公共设施](modules/core.md) | 00-需求总纲.md | 已实现 | 03 |
| `resource_references` | [公共设施](modules/core.md) | 00-需求总纲.md | 已实现 | 03 |
| `audit_events` | [公共设施](modules/core.md) | 00-需求总纲.md | 已实现 | 03 |
| `credentials` | [公共设施](modules/core.md) | 00-需求总纲.md | 已实现 | 03 |
| `artifacts` | [公共设施](modules/core.md) | 00-需求总纲.md | 已实现 | 03 |
| `source_links` | [公共设施](modules/core.md) | 00-需求总纲.md | 已实现 | 03 |
| `deletion_markers` | [公共设施](modules/core.md) | 00-需求总纲.md | 已实现 | 03 |
| `recovery_barriers` | [公共设施](modules/core.md) | 00-需求总纲.md | 已实现 | 03 |
| `creativity_alembic_version` | [公共设施](modules/core.md) | 00-需求总纲.md | 已实现 | 03 |
| `resource_uses` | [公共设施](modules/core.md) | 00-需求总纲.md | 已实现 | 03 |
| `transaction_lock_slots` | [公共设施](modules/core.md) | 00-需求总纲.md | 已实现 | 03 |
| `channels` | [渠道管理](modules/channels.md) | 01-渠道管理.md | 已实现 | 05 |
| `channel_environments` | [渠道管理](modules/channels.md) | 01-渠道管理.md | 已实现 | 05 |
| `service_clients` | [渠道管理](modules/channels.md) | 01-渠道管理.md | 已实现 | 05 |
| `channel_keys` | [渠道管理](modules/channels.md) | 01-渠道管理.md | 已实现 | 05 |
| `key_identity_index` | [渠道管理](modules/channels.md) | 01-渠道管理.md | 已实现 | 05 |
| `key_rotations` | [渠道管理](modules/channels.md) | 01-渠道管理.md | 已实现 | 05 |
| `channel_code_index` | [渠道管理](modules/channels.md) | 01-渠道管理.md | 已实现 | 05 |
| `channel_lifecycle_events` | [渠道管理](modules/channels.md) | 01-渠道管理.md | 已实现 | 05 |
| `platform_accounts` | [账号与授权](modules/iam.md) | 02-账号与权限管理.md | 已实现 | 04 |
| `builtin_roles` | [账号与授权](modules/iam.md) | 02-账号与权限管理.md | 已实现 | 04 |
| `channel_memberships` | [账号与授权](modules/iam.md) | 02-账号与权限管理.md | 已实现 | 04 |
| `resource_grants` | [账号与授权](modules/iam.md) | 02-账号与权限管理.md | 已实现 | 04 |
| `iam_revocations` | [账号与授权](modules/iam.md) | 02-账号与权限管理.md | 已实现 | 04 |
| `custom_roles` | [账号与授权](modules/iam.md) | 02-账号与权限管理.md | 已实现 | 04 |
| `iam_menus` | [账号与授权](modules/iam.md) | 02-账号与权限管理.md | 已实现 | 04 |
| `auth_tokens`（Redis 认证库） | [账号与授权](modules/iam.md) | 02-账号与权限管理.md | 已实现 | 04 |
| `provider_catalog` | [模型配置](modules/models.md) | 03-模型配置.md | 已实现 | 07 |
| `model_connections` | [模型配置](modules/models.md) | 03-模型配置.md | 已实现 | 07 |
| `models` | [模型配置](modules/models.md) | 03-模型配置.md | 已实现 | 07 |
| `model_routes` | [模型配置](modules/models.md) | 03-模型配置.md | 已实现 | 07 |
| `model_tests` | [模型配置](modules/models.md) | 03-模型配置.md | 已实现 | 07 |
| `price_versions` | [用量与预算](modules/usage.md) | 04-用量监控与预算.md | 已实现 | 08 |
| `usage_records` | [用量与预算](modules/usage.md) | 04-用量监控与预算.md | 已实现 | 08 |
| `usage_events` | [用量与预算](modules/usage.md) | 04-用量监控与预算.md | 已实现 | 08 |
| `usage_adjustments` | [用量与预算](modules/usage.md) | 04-用量监控与预算.md | 已实现 | 08 |
| `budget_policies` | [用量与预算](modules/usage.md) | 04-用量监控与预算.md | 已实现 | 08 |
| `budget_reservations` | [用量与预算](modules/usage.md) | 04-用量监控与预算.md | 已实现 | 08 |
| `admissions` | [用量与预算](modules/usage.md) | 04-用量监控与预算.md | 已实现 | 08 |
| `budget_alerts` | [用量与预算](modules/usage.md) | 04-用量监控与预算.md | 已实现 | 08 |
| `usage_aggregates` | [用量与预算](modules/usage.md) | 04-用量监控与预算.md | 已实现 | 08 |
| `usage_exports` | [用量与预算](modules/usage.md) | 04-用量监控与预算.md | 已实现 | 08 |
| `platform_limits` | [用量与预算](modules/usage.md) | 04-用量监控与预算.md | 已实现 | 08 |
| `platform_quota_occupancies` | [用量与预算](modules/usage.md) | 04-用量监控与预算.md | 已实现 | 08 |
| `usage_exchange_rates` | [用量与预算](modules/usage.md) | 04-用量监控与预算.md | 已实现 | 08 |
| `provider_statements` | [用量与预算](modules/usage.md) | 04-用量监控与预算.md | 已实现 | 08 |
| `prompts` | [提示词](modules/prompts.md) | 05-提示词管理.md | 已实现 | 09 |
| `prompt_samples` | [提示词](modules/prompts.md) | 05-提示词管理.md | 已实现 | 09 |
| `prompt_tests` | [提示词](modules/prompts.md) | 05-提示词管理.md | 已实现 | 09 |
| `agents` | [智能体定义](modules/agents.md) | 06-Agent与流程管理.md | 已实现 | 16 |
| `platform_templates` | [智能体定义](modules/agents.md) | 06-Agent与流程管理.md | 设计基线 | 16 |
| `agent_candidates` | [智能体定义](modules/agents.md) | 06-Agent与流程管理.md | 已实现 | 16 |
| `agent_release_records` | [智能体定义](modules/agents.md) | 06-Agent与流程管理.md | 已实现 | 16 |
| `agent_environment_states` | [智能体定义](modules/agents.md) | 06-Agent与流程管理.md | 已实现 | 16 |
| `conversations` | [会话](modules/conversations.md) | 07-会话管理.md | 已实现 | 12 |
| `messages` | [会话](modules/conversations.md) | 07-会话管理.md | 已实现 | 12 |
| `conversation_turns` | [会话](modules/conversations.md) | 07-会话管理.md | 已实现 | 12 |
| `conversation_summaries` | [会话](modules/conversations.md) | 07-会话管理.md | 已实现 | 12 |
| `context_snapshots` | [会话](modules/conversations.md) | 07-会话管理.md | 已实现 | 12 |
| `memories` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 已实现 | 13 |
| `memory_sources` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 已实现 | 13 |
| `memory_versions` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 已实现 | 13 |
| `memory_preferences` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 已实现 | 13 |
| `memory_policies` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 已实现 | 13 |
| `memory_retrievals` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 已实现 | 13 |
| `memory_deletion_jobs` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 已实现 | 13 |
| `memory_embeddings` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 已实现 | 13 |
| `memory_consolidations` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 已实现 | 13 |
| `memory_index_tasks` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 已实现 | 13 |
| `mcp_connections` | [远程工具连接](modules/mcp.md) | 09-MCP配置.md | 已实现 | 14 |
| `mcp_checks` | [远程工具连接](modules/mcp.md) | 09-MCP配置.md | 已实现 | 14 |
| `mcp_discoveries` | [远程工具连接](modules/mcp.md) | 09-MCP配置.md | 已实现 | 14 |
| `mcp_imports` | [远程工具连接](modules/mcp.md) | 09-MCP配置.md | 已实现 | 14 |
| `mcp_oauth_flows` | [远程工具连接](modules/mcp.md) | 09-MCP配置.md | 已实现 | 14 |
| `mcp_oauth_tokens` | [远程工具连接](modules/mcp.md) | 09-MCP配置.md | 已实现 | 14 |
| `tools` | [工具定义与证据](modules/tools.md) | 10-工具管理.md | 已实现 | 10 |
| `tool_calls` | [工具定义与证据](modules/tools.md) | 10-工具管理.md | 已实现 | 10 |
| `evidence_refs` | [工具定义与证据](modules/tools.md) | 10-工具管理.md | 已实现 | 10 |
| `skills` | [技能包](modules/skills.md) | 11-Skills管理.md | 已实现 | 15 |
| `skill_files` | [技能包](modules/skills.md) | 11-Skills管理.md | 已实现 | 15 |
| `skill_tests` | [技能包](modules/skills.md) | 11-Skills管理.md | 已实现 | 15 |
| `runs` | [运行受理与编排](modules/runs.md) | 12-执行记录与任务运行.md | 已实现 | 11/17 |
| `run_idempotency` | [运行受理与编排](modules/runs.md) | 12-执行记录与任务运行.md | 已实现 | 11/17 |
| `dispatch_outbox` | [运行受理与编排](modules/runs.md) | 12-执行记录与任务运行.md | 已实现 | 11/17 |
| `run_steps` | [运行受理与编排](modules/runs.md) | 12-执行记录与任务运行.md | 已实现 | 11/17 |
| `attempts` | [运行受理与编排](modules/runs.md) | 12-执行记录与任务运行.md | 已实现 | 11/17 |
| `run_events` | [运行受理与编排](modules/runs.md) | 12-执行记录与任务运行.md | 已实现 | 11/17 |
| `run_leases` | [运行受理与编排](modules/runs.md) | 12-执行记录与任务运行.md | 已实现 | 11/17 |
| `checkpoints` | [运行受理与编排](modules/runs.md) | 12-执行记录与任务运行.md | 已实现 | 11/17 |
| `checkpoint_writes` | [运行受理与编排](modules/runs.md) | 12-执行记录与任务运行.md | 设计基线 | 11/17 |
| `run_contents` | [运行受理与编排](modules/runs.md) | 12-执行记录与任务运行.md | 已实现 | 11/17 |
| `run_recoveries` | [运行受理与编排](modules/runs.md) | 12-执行记录与任务运行.md | 已实现 | 11/17 |
| `run_occupancies` | [运行受理与编排](modules/runs.md) | 12-执行记录与任务运行.md | 已实现 | 11/17 |
| `evaluation_datasets` | [效果评测](modules/evaluation.md) | 13-效果评测.md | 已实现 | 24 |
| `evaluation_dataset_versions` | [效果评测](modules/evaluation.md) | 13-效果评测.md | 已实现 | 24 |
| `evaluation_cases` | [效果评测](modules/evaluation.md) | 13-效果评测.md | 已实现 | 24 |
| `evaluation_fixtures` | [效果评测](modules/evaluation.md) | 13-效果评测.md | 已实现 | 24 |
| `evaluations` | [效果评测](modules/evaluation.md) | 13-效果评测.md | 已实现 | 24 |
| `evaluation_results` | [效果评测](modules/evaluation.md) | 13-效果评测.md | 已实现 | 24 |
| `evaluation_reports` | [效果评测](modules/evaluation.md) | 13-效果评测.md | 已实现 | 24 |
| `delegation_keys` | [业务接入与身份委托](modules/integrations.md) | 14-业务接入与适配.md | 已实现 | 18/19/20 |
| `delegation_nonces` | [业务接入与身份委托](modules/integrations.md) | 14-业务接入与适配.md | 已实现 | 18/19/20 |
| `subject_review_bindings` | [业务接入与身份委托](modules/integrations.md) | 14-业务接入与适配.md | 已实现 | 18/19/20 |
| `automation_schedules` | [业务接入与身份委托](modules/integrations.md) | 14-业务接入与适配.md | 已实现 | 18/19/20 |
| `automation_batches` | [业务接入与身份委托](modules/integrations.md) | 14-业务接入与适配.md | 已实现 | 18/19/20 |
| `automation_items` | [业务接入与身份委托](modules/integrations.md) | 14-业务接入与适配.md | 已实现 | 18/19/20 |
| `webhook_endpoints` | [业务接入与身份委托](modules/integrations.md) | 14-业务接入与适配.md | 已实现 | 18/19/20 |
| `webhook_deliveries` | [业务接入与身份委托](modules/integrations.md) | 14-业务接入与适配.md | 已实现 | 18/19/20 |
| `alert_rules` | [业务接入与身份委托](modules/integrations.md) | 14-业务接入与适配.md | 已实现 | 18/19/20 |
| `deletion_jobs` | [删除传播与保留](modules/deletion.md) | 00-需求总纲.md | 已实现 | 25 |
| `deletion_work_items` | [删除传播与保留](modules/deletion.md) | 00-需求总纲.md | 已实现 | 25 |
| `deletion_receipts` | [删除传播与保留](modules/deletion.md) | 00-需求总纲.md | 已实现 | 25 |

复用映射：渠道审计与账号审计共用 `audit_events`；模型价格归用量模块 `price_versions`；资源内容、依赖、发布映射和运行快照共用公共表；会话删除任务复用方案 25；任意 Agent 的结果使用通用运行与产物模型，业务领域对象由源系统维护。前端工作区没有独立权限或导航真值表。

后续方案开始编码前检查对应对象已在本索引中。新增字段、关系、索引或状态，先修订机器清单与关系/不变量，再在同批提交实现与迁移。验收 26 核验实际 schema，发布 27 将本目录复制成不可变模型快照。
