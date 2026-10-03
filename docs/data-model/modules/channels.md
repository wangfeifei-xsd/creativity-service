# 渠道管理模型

模型版本 1.8.0；负责方案 05；需求 [01-渠道管理.md](../../../../需求文档/01-渠道管理.md)。总索引见 [README](../README.md)。

## channels

渠道主档。状态：已实现；归属：渠道；迁移：0020_access_decoupling。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `channel_code` | `varchar(64)` | 渠道稳定编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 渠道名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 渠道状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `owner` | `varchar(128)` | 负责人名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `archived_at` | `timestamptz` | 归档时间 | 否 | 服务层校验后的业务输入 | 内部 |
| `retention_policy` | `jsonb` | 保存策略 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `budget_policy_refs` | `jsonb` | 预算策略引用 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `rate_limit_policy_refs` | `jsonb` | 限流策略引用 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `business_type` | `varchar(32)` | 可选业务分类展示文本，历史分类原值保留 | 否 | 可选展示元数据，不参与身份、路由或授权 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, channel_code)`。

## channel_environments

渠道环境。状态：已实现；归属：环境；迁移：0003_channels。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `name` | `varchar(128)` | 环境名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 环境状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `release_policy` | `jsonb` | 发布策略 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `retention_policy` | `jsonb` | 保存策略 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, environment)`。

## data_scopes

业务数据域映射。状态：已实现；归属：环境；迁移：0020_access_decoupling。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `name` | `varchar(128)` | 数据域名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `external_scope_type` | `varchar(64)` | 显式配置的外部数据域类型 | 是 | 管理员配置，验签后按原值精确匹配 | 内部 |
| `external_scope_id` | `varchar(128)` | 显式配置的外部数据域编号 | 是 | 管理员配置，验签后按原值精确匹配 | 内部 |
| `status` | `varchar(32)` | 数据域状态 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, external_scope_type, external_scope_id)`。

## service_clients

业务接入服务。状态：已实现；归属：环境；迁移：0003_channels。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `name` | `varchar(128)` | 服务名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `scopes` | `jsonb` | 权限上限 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `status` | `varchar(32)` | 服务状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `data_scopes` | `jsonb` | 授权业务数据域清单 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, status)`。

## channel_keys

渠道接入密钥。状态：已实现；归属：环境；迁移：0003_channels。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `name` | `varchar(128)` | 密钥名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `client_id` | `varchar(64)` | 接入服务标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `secret_digest` | `varchar(64)` | 不可逆密钥摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `prefix` | `varchar(16)` | 辨认前缀 | 是 | 服务层校验后的业务输入 | 内部 |
| `suffix` | `varchar(8)` | 辨认末尾 | 是 | 服务层校验后的业务输入 | 内部 |
| `scopes` | `jsonb` | 权限上限 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `status` | `varchar(32)` | 密钥状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `expires_at` | `timestamptz` | 失效时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `last_used_at` | `timestamptz` | 最近使用时间 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, client_id, status)`。

## key_identity_index

系统渠道密钥身份索引。状态：已实现；归属：系统；迁移：0003_channels。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `key_lookup_digest` | `varchar(64)` | 完整密钥不可逆摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `key_id` | `varchar(64)` | 目标密钥标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `target_channel_id` | `varchar(64)` | 目标渠道标识 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, key_lookup_digest)`。

控制面用途：`identity_lookup`；账号/角色身份引用不赋予其他渠道数据访问权。

## key_rotations

密钥轮换记录。状态：已实现；归属：渠道；迁移：0003_channels。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `old_key_id` | `varchar(64)` | 原密钥标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `new_key_id` | `varchar(64)` | 新密钥标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `overlap_until` | `timestamptz` | 重叠截止时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `operator_id` | `varchar(128)` | 操作人标识 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, old_key_id)`。

## channel_code_index

系统渠道的渠道编码定位索引。状态：已实现；归属：系统；迁移：0003_channels。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `channel_code` | `varchar(64)` | 规范化渠道编码 | 是 | 受限控制面服务校验后的输入 | 内部 |
| `target_channel_id` | `varchar(64)` | 实际业务渠道标识 | 是 | 受限控制面服务校验后的输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, channel_code)`。

控制面用途：`channel_directory`；账号/角色身份引用不赋予其他渠道数据访问权。

## channel_lifecycle_events

渠道生命周期交接事件。状态：已实现；归属：渠道；迁移：0003_channels。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `event_type` | `varchar(64)` | 生命周期事件类型 | 是 | 受信服务上下文与服务层校验 | 内部 |
| `target_type` | `varchar(64)` | 变更对象类型 | 是 | 受信服务上下文与服务层校验 | 内部 |
| `target_id` | `varchar(64)` | 变更对象标识 | 是 | 受信服务上下文与服务层校验 | 内部 |
| `environment` | `varchar(16)` | 受影响环境 | 否 | 受信服务上下文与服务层校验 | 内部 |
| `payload` | `jsonb` | 变更事实与原始归属 | 是 | 受信服务上下文与服务层校验 | 内部 |
| `acknowledgements` | `jsonb` | 已处理模块及时间 | 是 | 受信服务上下文与服务层校验 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, created_at)`。
