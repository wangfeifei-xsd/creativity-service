# 业务接入与身份委托模型

模型版本 1.4.0；负责方案 18/19/20；需求 [14-业务接入与适配.md](../../../../需求文档/14-业务接入与适配.md)。总索引见 [README](../README.md)。

## integrations

业务系统适配连接。状态：已实现；归属：数据域；迁移：0018_integrations。

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

委托签名验证密钥。状态：已实现；归属：环境；迁移：0018_integrations。

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

接入契约验证。状态：已实现；归属：数据域；迁移：0018_integrations。

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

已验证业务委托与请求防重放。状态：已实现；归属：环境；迁移：0018_integrations。

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
