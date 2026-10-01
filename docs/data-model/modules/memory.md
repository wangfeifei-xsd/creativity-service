# 结构化记忆模型

模型版本 1.2.0；负责方案 13；需求 [08-记忆管理.md](../../../../需求文档/08-记忆管理.md)。总索引见 [README](../README.md)。

## memories

主体结构化记忆。状态：设计基线；归属：主体；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `data_scope_id` | `varchar(64)` | 业务数据域标识 | 否 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `memory_type` | `varchar(64)` | 记忆类别 | 是 | 服务层校验后的业务输入 | 内部 |
| `key` | `varchar(128)` | 记忆属性名 | 是 | 服务层校验后的业务输入 | 内部 |
| `display_name` | `varchar(128)` | 属性中文名 | 是 | 服务层校验后的业务输入 | 内部 |
| `value` | `jsonb` | 记忆值 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `status` | `varchar(32)` | 确认及有效状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `expires_at` | `timestamptz` | 有效截止时间 | 否 | 服务层校验后的业务输入 | 内部 |
| `current_version_id` | `varchar(64)` | 当前版本 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, data_scope_id, subject_type, subject_id, key, status)`。

## memory_sources

记忆有效来源。状态：设计基线；归属：主体；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `data_scope_id` | `varchar(64)` | 业务数据域标识 | 否 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `memory_id` | `varchar(64)` | 记忆标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_type` | `varchar(64)` | 来源类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_id` | `varchar(128)` | 来源标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_version` | `varchar(128)` | 来源版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `observed_at` | `timestamptz` | 观测时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `evidence_id` | `varchar(64)` | 证据标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 来源状态 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, memory_id)`。

## memory_versions

记忆变更版本。状态：设计基线；归属：主体；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `data_scope_id` | `varchar(64)` | 业务数据域标识 | 否 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `memory_id` | `varchar(64)` | 记忆标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `previous_version_id` | `varchar(64)` | 前版本 | 否 | 服务层校验后的业务输入 | 内部 |
| `value` | `jsonb` | 该版本记忆值 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `changed_by` | `varchar(128)` | 变更主体 | 是 | 服务层校验后的业务输入 | 内部 |
| `reason` | `varchar(512)` | 变更原因 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, memory_id, created_at)`。

## memory_preferences

主体长期记忆开关。状态：设计基线；归属：主体；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `data_scope_id` | `varchar(64)` | 业务数据域标识 | 否 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `enabled` | `boolean` | 是否启用 | 是 | 服务层校验后的业务输入 | 内部 |
| `changed_by` | `varchar(128)` | 变更主体 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, data_scope_id, subject_type, subject_id)`。

## memory_policies

渠道与智能体记忆策略。状态：设计基线；归属：渠道；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `agent_id` | `varchar(64)` | 智能体标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `allowed_types` | `jsonb` | 允许类别 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `write_mode` | `varchar(32)` | 写入方式 | 是 | 服务层校验后的业务输入 | 内部 |
| `ttl_seconds` | `integer` | 保留秒数 | 是 | 服务层校验后的业务输入 | 内部 |
| `max_items` | `integer` | 存储条数上限 | 是 | 服务层校验后的业务输入 | 内部 |
| `retrieval_limit` | `integer` | 召回条数上限 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, agent_id)`。

## memory_retrievals

运行记忆召回记录。状态：设计基线；归属：主体；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `data_scope_id` | `varchar(64)` | 业务数据域标识 | 否 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `run_id` | `varchar(64)` | 运行标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `memory_refs` | `jsonb` | 使用的记忆版本 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `selection_reason` | `jsonb` | 选择依据 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`。
