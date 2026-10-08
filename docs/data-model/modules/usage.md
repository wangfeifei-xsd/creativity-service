# 用量与预算模型

模型版本 2.1.0；负责方案 08；需求 [04-用量监控与预算.md](../../../../需求文档/04-用量监控与预算.md)。总索引见 [README](../README.md)。

## price_versions

模型价格版本。状态：已实现；归属：渠道；归档修订：0004_usage。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `model_id` | `varchar(64)` | 模型标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `currency` | `varchar(3)` | 币种 | 是 | 服务层校验后的业务输入 | 内部 |
| `price_items` | `jsonb` | 各计价维度和子集关系 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `unit` | `varchar(64)` | 计价单位 | 是 | 服务层校验后的业务输入 | 内部 |
| `effective_at` | `timestamptz` | 价格生效时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `source` | `varchar(1024)` | 价格来源 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 价格版本名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `subset_relations` | `jsonb` | 适配器计量子集关系 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, model_id, effective_at)`。

## usage_records

实际尝试用量账本。状态：已实现；归属：主体；归档修订：0004_usage。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `run_id` | `varchar(64)` | 运行标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `attempt_id` | `varchar(64)` | 实际尝试标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_type` | `varchar(32)` | 来源类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `client_id` | `varchar(64)` | 接入服务标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `key_id` | `varchar(64)` | 渠道密钥标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `actor_id` | `varchar(128)` | 管理主体标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `agent_id` | `varchar(64)` | 智能体标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `model_id` | `varchar(64)` | 模型标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `connection_id` | `varchar(64)` | 供应商连接标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `purpose` | `varchar(32)` | 调用用途 | 是 | 服务层校验后的业务输入 | 内部 |
| `input_tokens` | `bigint` | 输入数量 | 否 | 服务层校验后的业务输入 | 内部 |
| `output_tokens` | `bigint` | 输出数量 | 否 | 服务层校验后的业务输入 | 内部 |
| `cached_tokens` | `bigint` | 缓存子集数量 | 否 | 服务层校验后的业务输入 | 内部 |
| `reasoning_tokens` | `bigint` | 推理子集数量 | 否 | 服务层校验后的业务输入 | 内部 |
| `raw_usage_ref` | `varchar(64)` | 原始用量引用 | 否 | 服务层校验后的业务输入 | 内部 |
| `usage_status` | `varchar(32)` | 用量完整性 | 是 | 服务层校验后的业务输入 | 内部 |
| `pricing_status` | `varchar(32)` | 计价完整性 | 是 | 服务层校验后的业务输入 | 内部 |
| `price_version_id` | `varchar(64)` | 价格版本标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `amount` | `numeric(24,8)` | 核算金额 | 否 | 服务层校验后的业务输入 | 内部 |
| `currency` | `varchar(3)` | 币种 | 否 | 服务层校验后的业务输入 | 内部 |
| `snapshot` | `jsonb` | 受信运行来源及可读名称快照 | 是 | 服务层校验后的业务输入 | 内部 |
| `normalized_tokens` | `jsonb` | 按维度归一化用量 | 是 | 服务层校验后的业务输入 | 内部 |
| `subset_relations` | `jsonb` | 计量子集关系 | 是 | 服务层校验后的业务输入 | 内部 |
| `calculation` | `jsonb` | 当前核算公式及依据 | 是 | 服务层校验后的业务输入 | 内部 |
| `upper_tokens` | `jsonb` | 调用前核准的计量上限 | 是 | 服务层校验后的业务输入 | 内部 |
| `upper_amount` | `numeric(24,8)` | 调用前预占金额 | 否 | 服务层校验后的业务输入 | 内部 |
| `state` | `varchar(32)` | 实际调用结算状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `sent_at` | `timestamptz` | 供应商调用发送前登记时间 | 否 | 服务层校验后的业务输入 | 内部 |
| `outcome` | `varchar(32)` | 实际尝试结果 | 是 | 服务层校验后的业务输入 | 内部 |
| `latest_event_version` | `bigint` | 当前有效来源事件版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `latest_event_id` | `varchar(64)` | 当前有效来源事件 | 否 | 服务层校验后的业务输入 | 内部 |
| `final_reported` | `boolean` | 是否收到供应商最终用量 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_request_id` | `varchar(256)` | 固定供应商请求标识 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, attempt_id)`；`(channel_id, created_at)`。

## usage_events

供应商用量来源事件。状态：已实现；归属：渠道；归档修订：0004_usage。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `attempt_id` | `varchar(64)` | 尝试标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `connection_id` | `varchar(64)` | 供应商连接标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_request_id` | `varchar(256)` | 供应商请求标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `event_version` | `bigint` | 事件版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `raw_usage` | `jsonb` | 原始计量值与子集口径 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `usage_status` | `varchar(32)` | 事件完整性 | 是 | 服务层校验后的业务输入 | 内部 |
| `observed_at` | `timestamptz` | 观测时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `event_payload` | `jsonb` | 经契约验证的完整计量事件 | 是 | 服务层校验后的业务输入 | 内部 |
| `payload_digest` | `varchar(64)` | 事件内容摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `applied` | `boolean` | 是否成为当前有效计量 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, connection_id, source_request_id, event_version)`。

## usage_adjustments

用量计价修正轨迹。状态：已实现；归属：渠道；归档修订：0004_usage。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `usage_id` | `varchar(64)` | 账本标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `event_id` | `varchar(64)` | 来源事件标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `previous_revision` | `bigint` | 前次修订 | 是 | 服务层校验后的业务输入 | 内部 |
| `amount_delta` | `numeric(24,8)` | 金额变动 | 否 | 服务层校验后的业务输入 | 内部 |
| `currency` | `varchar(3)` | 币种 | 否 | 服务层校验后的业务输入 | 内部 |
| `reason` | `varchar(512)` | 修正原因 | 是 | 服务层校验后的业务输入 | 内部 |
| `calculation` | `jsonb` | 核算依据 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, usage_id, created_at)`。

## budget_policies

预算策略。状态：已实现；归属：渠道；归档修订：0004_usage。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `scope_type` | `varchar(64)` | 预算对象类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `scope_id` | `varchar(128)` | 预算对象标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `period` | `varchar(32)` | 预算周期 | 是 | 服务层校验后的业务输入 | 内部 |
| `timezone` | `varchar(64)` | 业务时区 | 是 | 服务层校验后的业务输入 | 内部 |
| `currency` | `varchar(3)` | 币种 | 否 | 服务层校验后的业务输入 | 内部 |
| `limit_value` | `numeric(24,8)` | 限额 | 是 | 服务层校验后的业务输入 | 内部 |
| `unit` | `varchar(32)` | 金额或数量单位 | 是 | 服务层校验后的业务输入 | 内部 |
| `mode` | `varchar(32)` | 控制模式 | 是 | 服务层校验后的业务输入 | 内部 |
| `thresholds` | `jsonb` | 提醒阈值 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `status` | `varchar(32)` | 策略状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 预算名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `version_id` | `varchar(64)` | 当前不可变策略版本 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, scope_type, scope_id)`。

## budget_reservations

预算尝试预占。状态：已实现；归属：渠道；归档修订：0004_usage。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `policy_id` | `varchar(64)` | 预算策略标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `policy_revision` | `bigint` | 策略修订 | 是 | 服务层校验后的业务输入 | 内部 |
| `period_start` | `timestamptz` | 周期开始 | 是 | 服务层校验后的业务输入 | 内部 |
| `run_id` | `varchar(64)` | 运行标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `attempt_id` | `varchar(64)` | 实际尝试标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `reserved_amount` | `numeric(24,8)` | 预占数量或金额 | 是 | 服务层校验后的业务输入 | 内部 |
| `settled_amount` | `numeric(24,8)` | 结算数量或金额 | 否 | 服务层校验后的业务输入 | 内部 |
| `currency` | `varchar(3)` | 币种 | 否 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 预占状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `expires_at` | `timestamptz` | 待核查时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `policy_version_id` | `varchar(64)` | 预占时固定的预算策略版本 | 是 | 受限控制面服务校验后的输入 | 内部 |
| `unit` | `varchar(32)` | 占用计量单位 | 是 | 服务层校验后的业务输入 | 内部 |
| `scope_snapshot` | `jsonb` | 预算命中对象与周期快照 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, policy_id, period_start)`；`(channel_id, attempt_id)`。

## admissions

运行准入配额占用。状态：已实现；归属：主体；归档修订：0004_usage。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `run_id` | `varchar(64)` | 运行标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `policy_refs` | `jsonb` | 命中配额策略 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `status` | `varchar(32)` | 占用状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `expires_at` | `timestamptz` | 核查时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `snapshot` | `jsonb` | 运行来源及候选模型快照 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`。

## budget_alerts

预算阈值提醒。状态：已实现；归属：渠道；归档修订：0004_usage。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `rule_id` | `varchar(64)` | 规则标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `period_start` | `timestamptz` | 周期开始 | 是 | 服务层校验后的业务输入 | 内部 |
| `threshold` | `numeric(12,6)` | 触发阈值 | 是 | 服务层校验后的业务输入 | 内部 |
| `scope_key` | `varchar(64)` | 预算范围摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 提醒状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `first_triggered_at` | `timestamptz` | 首次触发时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `resolved_at` | `timestamptz` | 解除时间 | 否 | 服务层校验后的业务输入 | 内部 |
| `transitions` | `jsonb` | 解除及再次触发轨迹 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, rule_id, period_start, threshold, scope_key)`。

## usage_aggregates

可重算用量聚合。状态：已实现；归属：渠道；归档修订：0004_usage。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `dimensions` | `jsonb` | 统计维度 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `dimensions_digest` | `varchar(64)` | 统计范围摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `period_start` | `timestamptz` | 周期开始 | 是 | 服务层校验后的业务输入 | 内部 |
| `currency` | `varchar(3)` | 币种 | 否 | 服务层校验后的业务输入 | 内部 |
| `totals` | `jsonb` | 数量及完整性分组 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `ledger_watermark` | `timestamptz` | 账本处理水位 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, dimensions_digest, period_start, currency)`。

## usage_exports

用量导出请求。状态：已实现；归属：主体；归档修订：0004_usage。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `requested_by` | `varchar(128)` | 导出人 | 是 | 服务层校验后的业务输入 | 内部 |
| `filters` | `jsonb` | 授权筛选条件 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `timezone` | `varchar(64)` | 业务时区 | 是 | 服务层校验后的业务输入 | 内部 |
| `channel_range` | `jsonb` | 明确授权的渠道集合 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `state` | `varchar(32)` | 导出状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `artifact_id` | `varchar(64)` | 产物标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `scope_snapshot` | `jsonb` | 创建时受信查询范围 | 是 | 服务层校验后的业务输入 | 内部 |
| `object_key` | `varchar(1024)` | 渠道隔离的私有产物路径 | 否 | 服务层校验后的业务输入 | 内部 |
| `metadata` | `jsonb` | 计量口径及价格完整性 | 是 | 服务层校验后的业务输入 | 内部 |
| `error_message` | `varchar(512)` | 导出失败原因 | 否 | 服务层校验后的业务输入 | 内部 |
| `expires_at` | `timestamptz` | 导出失效时间 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, created_at)`。

## platform_limits

系统渠道平台总准入限额。状态：已实现；归属：系统；归档修订：0004_usage。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `limit_code` | `varchar(64)` | 平台限额编码 | 是 | 受限控制面服务校验后的输入 | 内部 |
| `name` | `varchar(128)` | 限额名称 | 是 | 受限控制面服务校验后的输入 | 内部 |
| `kind` | `varchar(32)` | 并发或请求数量类别 | 是 | 受限控制面服务校验后的输入 | 内部 |
| `period` | `varchar(32)` | 限额周期 | 是 | 受限控制面服务校验后的输入 | 内部 |
| `timezone` | `varchar(64)` | 周期时区 | 是 | 受限控制面服务校验后的输入 | 内部 |
| `limit_value` | `numeric(24,8)` | 限额数量 | 是 | 受限控制面服务校验后的输入 | 内部 |
| `unit` | `varchar(32)` | 计量单位 | 是 | 受限控制面服务校验后的输入 | 内部 |
| `status` | `varchar(32)` | 限额启用状态 | 是 | 受限控制面服务校验后的输入 | 内部 |
| `replaces_id` | `varchar(64)` | 前一个限额版本标识 | 否 | 受限控制面服务校验后的输入 | 内部 |
| `effective_at` | `timestamptz` | 该限额版本生效时间 | 是 | 受限控制面服务校验后的输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, limit_code)`。

控制面用途：`platform_limits`；账号/角色身份引用不赋予其他渠道数据访问权。

## platform_quota_occupancies

平台配额运行占用。状态：已实现；归属：系统；归档修订：0004_usage。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `target_channel_id` | `varchar(64)` | 实际业务渠道标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `run_id` | `varchar(64)` | 实际运行标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `limit_id` | `varchar(64)` | 平台限额版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `limit_code` | `varchar(64)` | 稳定平台限额编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `period_start` | `timestamptz` | 计数周期起点 | 是 | 服务层校验后的业务输入 | 内部 |
| `unit` | `varchar(32)` | 请求量或并发单位 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 占用状态 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, limit_code, period_start)`；`(channel_id, target_channel_id, run_id)`。

控制面用途：`platform_limits`；账号/角色身份引用不赋予其他渠道数据访问权。

## usage_exchange_rates

核算展示汇率版本。状态：已实现；归属：渠道；归档修订：0004_usage。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `base_currency` | `varchar(3)` | 原始币种 | 是 | 服务层校验后的业务输入 | 内部 |
| `quote_currency` | `varchar(3)` | 折算币种 | 是 | 服务层校验后的业务输入 | 内部 |
| `rate` | `numeric(24,8)` | 折算汇率 | 是 | 服务层校验后的业务输入 | 内部 |
| `effective_at` | `timestamptz` | 汇率日期 | 是 | 服务层校验后的业务输入 | 内部 |
| `source` | `varchar(1024)` | 汇率来源 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, base_currency, quote_currency, effective_at)`。

## provider_statements

供应商账单核查批次。状态：已实现；归属：主体；归档修订：0031_identity_operations。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `name` | `varchar(128)` | 账单来源名称 | 是 | 受信上下文与服务层校验 | 内部 |
| `source_digest` | `varchar(64)` | 导入来源摘要 | 是 | 受信上下文与服务层校验 | 内部 |
| `version` | `varchar(64)` | 来源账单版本 | 是 | 受信上下文与服务层校验 | 内部 |
| `connection_id` | `varchar(64)` | 模型连接标识 | 是 | 受信上下文与服务层校验 | 内部 |
| `currency` | `varchar(3)` | 账单币种 | 是 | 受信上下文与服务层校验 | 内部 |
| `start_at` | `timestamptz` | 核查开始时间 | 是 | 受信上下文与服务层校验 | 内部 |
| `end_at` | `timestamptz` | 核查结束时间 | 是 | 受信上下文与服务层校验 | 内部 |
| `lines` | `jsonb` | 规范化供应商记录 | 是 | 受信上下文与服务层校验 | 内部 |
| `owner_key` | `varchar(64)` | 创建身份摘要 | 是 | 受信上下文与服务层校验 | 内部 |

普通索引：`(channel_id, id)`。

## 版本内容结构：budget_policy

存于公共 `resource_versions.content`；数组项结构按名称单独列出。父版本、依赖与发布映射只保存一份。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `scope_type` | `varchar(64)` | 预算对象类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `scope_id` | `varchar(128)` | 预算对象标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `period` | `varchar(32)` | 预算周期 | 是 | 服务层校验后的业务输入 | 内部 |
| `timezone` | `varchar(64)` | 业务时区 | 是 | 服务层校验后的业务输入 | 内部 |
| `currency` | `varchar(3)` | 币种 | 否 | 服务层校验后的业务输入 | 内部 |
| `limit_value` | `numeric(24,8)` | 限额 | 是 | 服务层校验后的业务输入 | 内部 |
| `unit` | `varchar(32)` | 金额或数量单位 | 是 | 服务层校验后的业务输入 | 内部 |
| `mode` | `varchar(32)` | 控制模式 | 是 | 服务层校验后的业务输入 | 内部 |
| `thresholds` | `jsonb` | 提醒阈值 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `status` | `varchar(32)` | 策略状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `effective_from` | `timestamptz` | 版本生效时间 | 是 | 受限控制面服务校验后的输入 | 内部 |
