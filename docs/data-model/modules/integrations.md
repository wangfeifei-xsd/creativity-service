# 业务接入与身份委托模型

模型版本 1.9.3；负责方案 18/19/20；需求 [14-业务接入与适配.md](../../../../需求文档/14-业务接入与适配.md)。总索引见 [README](../README.md)。

## integrations

业务系统适配连接。状态：已实现；归属：数据域；归档修订：0018_integrations。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `data_scope_id` | `varchar(64)` | 所属业务数据域 | 是 | 服务层校验与受信上下文 | 内部 |
| `name` | `varchar(128)` | 接入名称 | 是 | 服务层校验与受信上下文 | 内部 |
| `adapter_code` | `varchar(64)` | 适配器编码 | 是 | 服务层校验与受信上下文 | 内部 |
| `adapter_version` | `varchar(64)` | 适配器版本 | 是 | 服务层校验与受信上下文 | 内部 |
| `business_endpoint` | `varchar(2048)` | 源服务地址 | 是 | 服务层校验与受信上下文 | 内部 |
| `credential_ref` | `varchar(64)` | 服务凭据引用 | 是 | 服务层校验与受信上下文 | 内部 |
| `allowed_operations` | `jsonb` | 已授权能力 | 是 | 服务层校验与受信上下文 | 内部 |
| `operation_paths` | `jsonb` | 固定能力接口路径 | 是 | 服务层校验与受信上下文 | 内部 |
| `field_mapping` | `jsonb` | 源字段转换配置 | 是 | 服务层校验与受信上下文 | 内部 |
| `scope_mapping_ref` | `varchar(64)` | 渠道数据域映射引用 | 是 | 服务层校验与受信上下文 | 内部 |
| `health` | `varchar(32)` | 连接健康状态 | 是 | 服务层校验与受信上下文 | 内部 |
| `contract_version` | `varchar(64)` | 标准契约版本 | 是 | 服务层校验与受信上下文 | 内部 |
| `status` | `varchar(32)` | 启用状态 | 是 | 服务层校验与受信上下文 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, data_scope_id, status)`。

## delegation_keys

委托签名验证密钥。状态：已实现；归属：环境；归档修订：0018_integrations。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `client_id` | `varchar(64)` | 接入服务标识 | 是 | 服务层校验与受信上下文 | 内部 |
| `key_reference` | `varchar(64)` | 独立签名密文引用 | 是 | 服务层校验与受信上下文 | 内部 |
| `algorithm` | `varchar(32)` | 固定签名算法 | 是 | 服务层校验与受信上下文 | 内部 |
| `issuer` | `varchar(128)` | 可信签发者 | 是 | 服务层校验与受信上下文 | 内部 |
| `audience` | `varchar(128)` | 委托受众 | 是 | 服务层校验与受信上下文 | 内部 |
| `max_ttl_seconds` | `integer` | 最长委托有效秒数 | 是 | 服务层校验与受信上下文 | 内部 |
| `clock_skew_seconds` | `integer` | 允许时钟偏差秒数 | 是 | 服务层校验与受信上下文 | 内部 |
| `status` | `varchar(32)` | 密钥状态 | 是 | 服务层校验与受信上下文 | 内部 |
| `not_before` | `timestamptz` | 密钥生效时间 | 是 | 服务层校验与受信上下文 | 内部 |
| `expires_at` | `timestamptz` | 密钥到期时间 | 是 | 服务层校验与受信上下文 | 内部 |
| `rotated_from` | `varchar(64)` | 轮换前密钥编号 | 否 | 服务层校验与受信上下文 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, client_id)`。

## integration_tests

接入契约验证。状态：已实现；归属：数据域；归档修订：0018_integrations。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `data_scope_id` | `varchar(64)` | 所属业务数据域 | 是 | 服务层校验与受信上下文 | 内部 |
| `integration_id` | `varchar(64)` | 连接标识 | 是 | 服务层校验与受信上下文 | 内部 |
| `config_revision` | `bigint` | 测试对应配置修订 | 是 | 服务层校验与受信上下文 | 内部 |
| `cases` | `jsonb` | 验证能力清单 | 是 | 服务层校验与受信上下文 | 内部 |
| `results` | `jsonb` | 脱敏验证结论 | 是 | 服务层校验与受信上下文 | 内部 |
| `capabilities` | `jsonb` | 通过验证的能力 | 是 | 服务层校验与受信上下文 | 内部 |
| `state` | `varchar(32)` | 验证状态 | 是 | 服务层校验与受信上下文 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, data_scope_id, integration_id)`。

## delegation_nonces

已验证业务委托与请求防重放。状态：已实现；归属：环境；归档修订：0018_integrations。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `client_id` | `varchar(64)` | 稳定接入服务标识 | 是 | 服务层校验与受信上下文 | 内部 |
| `kid` | `varchar(64)` | 验签密钥编号 | 是 | 服务层校验与受信上下文 | 内部 |
| `nonce_digest` | `varchar(64)` | 随机数摘要 | 是 | 服务层校验与受信上下文 | 内部 |
| `request_digest` | `varchar(64)` | 实际请求绑定摘要 | 是 | 服务层校验与受信上下文 | 内部 |
| `claims_digest` | `varchar(64)` | 完整委托声明摘要 | 是 | 服务层校验与受信上下文 | 内部 |
| `claims` | `jsonb` | 验签后权限声明 | 是 | 服务层校验与受信上下文 | 敏感内容 |
| `resolved_scope` | `jsonb` | 验签后渠道数据域主体 | 是 | 服务层校验与受信上下文 | 内部 |
| `expires_at` | `timestamptz` | 防重放声明有效时间 | 是 | 服务层校验与受信上下文 | 内部 |
| `retain_until` | `timestamptz` | 防重放记录最早清理时间 | 是 | 服务层校验与受信上下文 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, client_id, nonce_digest)`；`(channel_id, environment, retain_until)`。

## subject_review_bindings

当前主体复核的固定 MCP 绑定。状态：已实现；归属：数据域；归档修订：0021_mcp_subject_review。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 渠道、环境、数据域与接入服务的确定性摘要 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `data_scope_id` | `varchar(64)` | 所属业务数据域 | 是 | 服务层校验与受信上下文 | 内部 |
| `client_id` | `varchar(64)` | 受限接入服务标识 | 是 | 服务层校验后的受信配置 | 内部 |
| `connection_id` | `varchar(64)` | 固定身份复核连接 | 是 | 服务层校验后的受信配置 | 内部 |
| `discovery_id` | `varchar(64)` | 授权时发现快照 | 是 | 服务层校验后的受信配置 | 内部 |
| `remote_tool_name` | `varchar(256)` | 专用身份复核工具名 | 是 | 服务层校验后的受信配置 | 内部 |
| `tool_name` | `varchar(128)` | 身份工具显示名称 | 是 | 服务层校验后的受信配置 | 内部 |
| `connection_revision` | `bigint` | 授权时连接配置修订 | 是 | 服务层校验后的受信配置 | 内部 |
| `schema_hash` | `varchar(128)` | 授权时身份工具契约摘要 | 是 | 服务层校验后的受信配置 | 内部 |
| `timeout_seconds` | `integer` | 身份复核超时秒数 | 是 | 服务层校验后的受信配置 | 内部 |
| `enabled` | `boolean` | 是否允许身份复核 | 是 | 服务层校验后的受信配置 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, data_scope_id, client_id)`。

## automation_schedules

通用运行定时计划。状态：已实现；归属：主体；归档修订：0030_automation。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `data_scope_id` | `varchar(64)` | 业务数据域标识 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `name` | `varchar(128)` | 计划名称 | 是 | 受信上下文与服务层校验 | 内部 |
| `spec` | `jsonb` | 周期与运行输入 | 是 | 受信上下文与服务层校验 | 内部 |
| `owner_key` | `varchar(64)` | 执行身份摘要 | 是 | 受信上下文与服务层校验 | 内部 |
| `identity` | `jsonb` | 原执行身份快照 | 是 | 受信上下文与服务层校验 | 内部 |
| `state` | `varchar(32)` | 当前处理状态 | 是 | 受信上下文与服务层校验 | 内部 |
| `next_at` | `timestamptz` | 下次触发时间 | 是 | 受信上下文与服务层校验 | 内部 |
| `last_error` | `jsonb` | 最近派发错误 | 否 | 受信上下文与服务层校验 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, data_scope_id, subject_type, subject_id)`；`(channel_id, state, next_at)`。

## automation_batches

批量运行受理批次。状态：已实现；归属：主体；归档修订：0030_automation。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `data_scope_id` | `varchar(64)` | 业务数据域标识 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `name` | `varchar(128)` | 批次名称 | 是 | 受信上下文与服务层校验 | 内部 |
| `request_digest` | `varchar(64)` | 受理请求摘要 | 是 | 受信上下文与服务层校验 | 内部 |
| `item_ids` | `jsonb` | 批次条目关联 | 是 | 受信上下文与服务层校验 | 内部 |
| `owner_key` | `varchar(64)` | 执行身份摘要 | 是 | 受信上下文与服务层校验 | 内部 |
| `identity` | `jsonb` | 原执行身份快照 | 是 | 受信上下文与服务层校验 | 内部 |
| `state` | `varchar(32)` | 当前处理状态 | 是 | 受信上下文与服务层校验 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, data_scope_id, subject_type, subject_id)`。

## automation_items

逐项运行派发记录。状态：已实现；归属：主体；归档修订：0030_automation。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `data_scope_id` | `varchar(64)` | 业务数据域标识 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `batch_id` | `varchar(64)` | 来源批次标识 | 否 | 受信上下文与服务层校验 | 内部 |
| `schedule_id` | `varchar(64)` | 来源计划标识 | 否 | 受信上下文与服务层校验 | 内部 |
| `event_id` | `varchar(128)` | 外部事件或窗口编号 | 是 | 受信上下文与服务层校验 | 内部 |
| `request_digest` | `varchar(64)` | 条目语义摘要 | 是 | 受信上下文与服务层校验 | 内部 |
| `request` | `jsonb` | 受理运行输入 | 是 | 受信上下文与服务层校验 | 内部 |
| `owner_key` | `varchar(64)` | 执行身份摘要 | 是 | 受信上下文与服务层校验 | 内部 |
| `identity` | `jsonb` | 原执行身份快照 | 是 | 受信上下文与服务层校验 | 内部 |
| `state` | `varchar(32)` | 当前处理状态 | 是 | 受信上下文与服务层校验 | 内部 |
| `lease_until` | `timestamptz` | 派发租约到期 | 否 | 受信上下文与服务层校验 | 内部 |
| `lease_nonce` | `varchar(64)` | 派发租约凭据 | 否 | 受信上下文与服务层校验 | 内部 |
| `attempts` | `bigint` | 受理尝试次数 | 是 | 受信上下文与服务层校验 | 内部 |
| `run_id` | `varchar(64)` | 关联运行标识 | 否 | 受信上下文与服务层校验 | 内部 |
| `error` | `jsonb` | 最近受理错误 | 否 | 受信上下文与服务层校验 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, data_scope_id, subject_type, subject_id)`；`(channel_id, state, lease_until)`。

## webhook_endpoints

事件投递端点。状态：已实现；归属：主体；归档修订：0030_automation。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `data_scope_id` | `varchar(64)` | 业务数据域标识 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `name` | `varchar(128)` | 端点名称 | 是 | 受信上下文与服务层校验 | 内部 |
| `url` | `text` | 固定接收地址 | 是 | 受信上下文与服务层校验 | 内部 |
| `secret_ref` | `varchar(64)` | 签名密钥密文引用 | 是 | 受信上下文与服务层校验 | 内部 |
| `events` | `jsonb` | 订阅事件类型 | 是 | 受信上下文与服务层校验 | 内部 |
| `owner_key` | `varchar(64)` | 执行身份摘要 | 是 | 受信上下文与服务层校验 | 内部 |
| `identity` | `jsonb` | 原执行身份快照 | 是 | 受信上下文与服务层校验 | 内部 |
| `state` | `varchar(32)` | 当前处理状态 | 是 | 受信上下文与服务层校验 | 内部 |
| `client_ids` | `jsonb` | 订阅的调用服务列表；空列表仅包含配置者运行 | 是 | 管理配置 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, data_scope_id, subject_type, subject_id)`。

## webhook_deliveries

持久化事件投递。状态：已实现；归属：主体；归档修订：0030_automation。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `data_scope_id` | `varchar(64)` | 业务数据域标识 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `endpoint_id` | `varchar(64)` | 投递端点标识 | 是 | 受信上下文与服务层校验 | 内部 |
| `event_id` | `varchar(64)` | 稳定事件编号 | 是 | 受信上下文与服务层校验 | 内部 |
| `kind` | `varchar(64)` | 事件类型 | 是 | 受信上下文与服务层校验 | 内部 |
| `payload` | `jsonb` | 最小事件正文 | 是 | 受信上下文与服务层校验 | 内部 |
| `owner_key` | `varchar(64)` | 执行身份摘要 | 是 | 受信上下文与服务层校验 | 内部 |
| `state` | `varchar(32)` | 当前处理状态 | 是 | 受信上下文与服务层校验 | 内部 |
| `attempts` | `bigint` | 累计投递次数 | 是 | 受信上下文与服务层校验 | 内部 |
| `next_at` | `timestamptz` | 下次尝试时间 | 是 | 受信上下文与服务层校验 | 内部 |
| `lease_until` | `timestamptz` | 投递租约到期 | 否 | 受信上下文与服务层校验 | 内部 |
| `lease_nonce` | `varchar(64)` | 投递租约凭据 | 否 | 受信上下文与服务层校验 | 内部 |
| `error` | `jsonb` | 最近投递错误 | 否 | 受信上下文与服务层校验 | 内部 |
| `http_status` | `bigint` | 最近响应状态 | 否 | 受信上下文与服务层校验 | 内部 |
| `cycle_attempts` | `bigint` | 本轮自动投递次数，人工重投重新计数 | 是 | 受信上下文与服务层校验 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, data_scope_id, subject_type, subject_id)`；`(channel_id, state, next_at)`。

## alert_rules

外部告警规则。状态：已实现；归属：主体；归档修订：0031_identity_operations。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `data_scope_id` | `varchar(64)` | 业务数据域标识 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `name` | `varchar(128)` | 规则名称 | 是 | 受信上下文与服务层校验 | 内部 |
| `kind` | `varchar(32)` | 监测类型 | 是 | 受信上下文与服务层校验 | 内部 |
| `threshold` | `bigint` | 触发次数阈值 | 是 | 受信上下文与服务层校验 | 内部 |
| `window_seconds` | `bigint` | 监测窗口秒数 | 是 | 受信上下文与服务层校验 | 内部 |
| `endpoint_id` | `varchar(64)` | 告警投递端点 | 是 | 受信上下文与服务层校验 | 内部 |
| `owner_key` | `varchar(64)` | 创建身份摘要 | 是 | 受信上下文与服务层校验 | 内部 |
| `identity` | `jsonb` | 原执行身份快照 | 是 | 受信上下文与服务层校验 | 内部 |
| `state` | `varchar(32)` | 启停状态 | 是 | 受信上下文与服务层校验 | 内部 |
| `active` | `boolean` | 当前是否告警 | 是 | 受信上下文与服务层校验 | 内部 |
| `generation` | `bigint` | 触发周期序号 | 是 | 受信上下文与服务层校验 | 内部 |
| `last_value` | `bigint` | 最近观察次数 | 是 | 受信上下文与服务层校验 | 内部 |
| `pending_events` | `jsonb` | 待生成投递事件 | 是 | 受信上下文与服务层校验 | 内部 |
| `client_ids` | `jsonb` | 订阅的调用服务列表；空列表仅包含配置者运行 | 是 | 管理配置 | 内部 |

普通索引：`(channel_id, id)`。
