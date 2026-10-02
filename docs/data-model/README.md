# P0 数据模型索引

模型版本 **1.2.0**；需求基线 **v0.6**；技术基线 **v1.5**。

本档案覆盖全部 P0 持久化对象。机器清单为 [catalog.json](catalog.json)，字段文档由 `scripts/render_data_model.py` 生成。共享基础的代码定义位于 `core/database/baseline_v0001.json`；公共表对应 `0001_core`，账号模块对应 `0002_iam`，渠道模块对应 `0003_channels`；其他业务表按所属方案建库。开发规范引用 [rule.md](../../../rule.md)。

对象逻辑标识（如 run_id、conversation_id、version_id）在所属表统一物理存为 `id`；关联字段保留业务名称。渠道主档的 `id` 与 `channel_id` 相等。业务必填由服务入口验证，所有普通列均显式赋值。JSONB 中的类型化内容由所属模块 schema 校验；敏感级别按来源可向上提升。

[关系与生命周期](relations.md) · [服务不变量](service-invariants.md) · [存储职责](storage-map.md) · [变更记录](changes.md)

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
| `channels` | [渠道管理](modules/channels.md) | 01-渠道管理.md | 已实现 | 05 |
| `channel_environments` | [渠道管理](modules/channels.md) | 01-渠道管理.md | 已实现 | 05 |
| `data_scopes` | [渠道管理](modules/channels.md) | 01-渠道管理.md | 已实现 | 05 |
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
| `prompts` | [提示词](modules/prompts.md) | 05-提示词管理.md | 已实现 | 09 |
| `prompt_samples` | [提示词](modules/prompts.md) | 05-提示词管理.md | 已实现 | 09 |
| `prompt_tests` | [提示词](modules/prompts.md) | 05-提示词管理.md | 已实现 | 09 |
| `agents` | [智能体定义](modules/agents.md) | 06-Agent与流程管理.md | 设计基线 | 16 |
| `platform_templates` | [智能体定义](modules/agents.md) | 06-Agent与流程管理.md | 设计基线 | 16 |
| `conversations` | [会话](modules/conversations.md) | 07-会话管理.md | 已实现 | 12 |
| `messages` | [会话](modules/conversations.md) | 07-会话管理.md | 已实现 | 12 |
| `conversation_turns` | [会话](modules/conversations.md) | 07-会话管理.md | 已实现 | 12 |
| `conversation_summaries` | [会话](modules/conversations.md) | 07-会话管理.md | 已实现 | 12 |
| `context_snapshots` | [会话](modules/conversations.md) | 07-会话管理.md | 已实现 | 12 |
| `memories` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 设计基线 | 13 |
| `memory_sources` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 设计基线 | 13 |
| `memory_versions` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 设计基线 | 13 |
| `memory_preferences` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 设计基线 | 13 |
| `memory_policies` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 设计基线 | 13 |
| `memory_retrievals` | [结构化记忆](modules/memory.md) | 08-记忆管理.md | 设计基线 | 13 |
| `mcp_connections` | [远程工具连接](modules/mcp.md) | 09-MCP配置.md | 已实现 | 14 |
| `mcp_checks` | [远程工具连接](modules/mcp.md) | 09-MCP配置.md | 已实现 | 14 |
| `mcp_discoveries` | [远程工具连接](modules/mcp.md) | 09-MCP配置.md | 已实现 | 14 |
| `mcp_imports` | [远程工具连接](modules/mcp.md) | 09-MCP配置.md | 已实现 | 14 |
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
| `evaluation_datasets` | [效果评测](modules/evaluation.md) | 13-效果评测.md | 设计基线 | 24 |
| `evaluation_dataset_versions` | [效果评测](modules/evaluation.md) | 13-效果评测.md | 设计基线 | 24 |
| `evaluation_cases` | [效果评测](modules/evaluation.md) | 13-效果评测.md | 设计基线 | 24 |
| `evaluation_fixtures` | [效果评测](modules/evaluation.md) | 13-效果评测.md | 设计基线 | 24 |
| `evaluations` | [效果评测](modules/evaluation.md) | 13-效果评测.md | 设计基线 | 24 |
| `evaluation_results` | [效果评测](modules/evaluation.md) | 13-效果评测.md | 设计基线 | 24 |
| `evaluation_reports` | [效果评测](modules/evaluation.md) | 13-效果评测.md | 设计基线 | 24 |
| `integrations` | [业务接入与身份委托](modules/integrations.md) | 14-业务接入与适配.md | 已实现 | 18/19/20 |
| `delegation_keys` | [业务接入与身份委托](modules/integrations.md) | 14-业务接入与适配.md | 已实现 | 18/19/20 |
| `integration_tests` | [业务接入与身份委托](modules/integrations.md) | 14-业务接入与适配.md | 已实现 | 18/19/20 |
| `delegation_nonces` | [业务接入与身份委托](modules/integrations.md) | 14-业务接入与适配.md | 已实现 | 18/19/20 |
| `matching_configs` | [智能匹配](modules/matching.md) | 15-智能匹配.md | 设计基线 | 21 |
| `match_feedback` | [智能匹配](modules/matching.md) | 15-智能匹配.md | 设计基线 | 21 |
| `risk_policies` | [风险评估](modules/risk.md) | 16-风险评估.md | 设计基线 | 22 |
| `risk_assessments` | [风险评估](modules/risk.md) | 16-风险评估.md | 设计基线 | 22 |
| `risk_batches` | [风险评估](modules/risk.md) | 16-风险评估.md | 设计基线 | 22 |
| `risk_feedback` | [风险评估](modules/risk.md) | 16-风险评估.md | 设计基线 | 22 |
| `metrics` | [指标分析](modules/analysis.md) | 17-数据分析.md | 设计基线 | 23 |
| `analysis_plans` | [指标分析](modules/analysis.md) | 17-数据分析.md | 设计基线 | 23 |
| `analysis_datasets` | [指标分析](modules/analysis.md) | 17-数据分析.md | 设计基线 | 23 |
| `calculation_steps` | [指标分析](modules/analysis.md) | 17-数据分析.md | 设计基线 | 23 |
| `analysis_reports` | [指标分析](modules/analysis.md) | 17-数据分析.md | 设计基线 | 23 |
| `analysis_feedback` | [指标分析](modules/analysis.md) | 17-数据分析.md | 设计基线 | 23 |
| `deletion_jobs` | [删除传播与保留](modules/deletion.md) | 00-需求总纲.md | 已实现 | 25 |
| `deletion_work_items` | [删除传播与保留](modules/deletion.md) | 00-需求总纲.md | 设计基线 | 25 |

复用映射：渠道审计与账号审计共用 `audit_events`；模型价格归用量模块 `price_versions`；资源内容、依赖、发布映射和运行快照共用公共表；会话删除任务复用方案 25；匹配结果使用运行结果，不再复制任务表；风险及分析详情为运行的受控派生对象。前端工作区没有独立权限或导航真值表。

后续方案开始编码前检查对应对象已在本索引中。新增字段、关系、索引或状态，先修订机器清单与关系/不变量，再在同批提交实现与迁移。验收 26 核验实际 schema，发布 27 将本目录复制成不可变模型快照。
