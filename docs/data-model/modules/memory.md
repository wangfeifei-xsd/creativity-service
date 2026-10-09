# 结构化记忆模型

模型版本 2.0.0；负责方案 13；需求 [08-记忆管理.md](../../../../需求文档/08-记忆管理.md)。总索引见 [README](../README.md)。

## memories

主体结构化记忆。状态：已实现；归属：主体；归档修订：0013_memory。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 是 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 是 | 受信服务上下文 | 个人 |
| `memory_type` | `varchar(64)` | 记忆类别 | 是 | 服务层校验后的业务输入 | 内部 |
| `key` | `varchar(128)` | 记忆属性名 | 是 | 服务层校验后的业务输入 | 内部 |
| `display_name` | `varchar(128)` | 属性中文名 | 是 | 服务层校验后的业务输入 | 内部 |
| `value` | `json` | 记忆值 | 否 | 服务层校验后的业务输入 | 敏感内容 |
| `status` | `varchar(32)` | 确认及有效状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `expires_at` | `datetime(6) UTC` | 有效截止时间 | 否 | 服务层校验后的业务输入 | 内部 |
| `current_version_id` | `varchar(64)` | 当前版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `confirmed` | `boolean` | 是否经过明确确认 | 是 | 服务层校验后的业务输入 | 内部 |
| `observed_at` | `datetime(6) UTC` | 最新有效依据时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `trust_level` | `integer` | 有效来源可信等级 | 是 | 服务层校验后的业务输入 | 内部 |
| `usage_count` | `bigint` | 实际使用次数 | 是 | 服务层校验后的业务输入 | 内部 |
| `subject_name` | `varchar(255)` | 主体可读名称 | 否 | 服务层校验后的业务输入 | 个人 |
| `source_mode` | `varchar(16)` | 来源有效性模式：独立依据或全部依赖 | 是 | 记忆服务 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, subject_type, subject_id, key, status)`。

## memory_sources

记忆有效来源。状态：已实现；归属：主体；归档修订：0013_memory。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 是 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 是 | 受信服务上下文 | 个人 |
| `memory_id` | `varchar(64)` | 记忆标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_type` | `varchar(64)` | 来源类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_id` | `varchar(128)` | 来源标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_version` | `varchar(128)` | 来源版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `observed_at` | `datetime(6) UTC` | 观测时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `evidence_id` | `varchar(64)` | 证据标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 来源状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `authority` | `varchar(32)` | 来源权限类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `trust_level` | `integer` | 来源可信等级 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, memory_id)`；`(channel_id, environment, subject_type, subject_id, source_type, source_id)`。

## memory_versions

记忆变更版本。状态：已实现；归属：主体；归档修订：0013_memory。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 是 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 是 | 受信服务上下文 | 个人 |
| `memory_id` | `varchar(64)` | 记忆标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `previous_version_id` | `varchar(64)` | 前版本 | 否 | 服务层校验后的业务输入 | 内部 |
| `value` | `json` | 历史内容空槽位，不保存原文 | 否 | 服务层校验后的业务输入 | 敏感内容 |
| `changed_by` | `varchar(128)` | 变更主体 | 是 | 服务层校验后的业务输入 | 内部 |
| `reason` | `varchar(512)` | 变更原因 | 是 | 服务层校验后的业务输入 | 内部 |
| `version_number` | `integer` | 版本序号 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 变更后状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_ids` | `json` | 有效来源记录集合 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, memory_id, created_at)`。

## memory_preferences

主体长期记忆开关。状态：已实现；归属：主体；归档修订：0013_memory。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 是 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 是 | 受信服务上下文 | 个人 |
| `enabled` | `boolean` | 是否启用 | 是 | 服务层校验后的业务输入 | 内部 |
| `changed_by` | `varchar(128)` | 变更主体 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, subject_type, subject_id)`。

## memory_policies

渠道与智能体记忆策略。状态：已实现；归属：渠道；归档修订：0013_memory。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `agent_id` | `varchar(64)` | 智能体标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `allowed_types` | `json` | 允许类别 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `write_mode` | `varchar(32)` | 写入方式 | 是 | 服务层校验后的业务输入 | 内部 |
| `ttl_seconds` | `integer` | 保留秒数 | 是 | 服务层校验后的业务输入 | 内部 |
| `max_items` | `integer` | 存储条数上限 | 是 | 服务层校验后的业务输入 | 内部 |
| `retrieval_limit` | `integer` | 召回条数上限 | 是 | 服务层校验后的业务输入 | 内部 |
| `read_enabled` | `boolean` | 是否允许读取 | 是 | 服务层校验后的业务输入 | 内部 |
| `suggest_enabled` | `boolean` | 是否允许建议写入 | 是 | 服务层校验后的业务输入 | 内部 |
| `failure_mode` | `varchar(16)` | 读取故障处理方式 | 是 | 服务层校验后的业务输入 | 内部 |
| `attributes` | `json` | 渠道可配置的画像属性定义 | 是 | 渠道配置 | 内部 |
| `consolidation` | `json` | 后台归档与画像整理策略 | 是 | 渠道配置 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, agent_id)`。

## memory_retrievals

运行记忆召回记录。状态：已实现；归属：主体；归档修订：0013_memory。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 是 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 是 | 受信服务上下文 | 个人 |
| `run_id` | `varchar(64)` | 运行标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `memory_refs` | `json` | 使用的记忆版本 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `selection_reason` | `json` | 选择依据 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `warnings` | `json` | 脱敏降级提示 | 是 | 服务层校验后的业务输入 | 内部 |
| `agent_id` | `varchar(64)` | 使用记忆的智能体 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`。

## memory_deletion_jobs

主体记忆删除清理意图。状态：已实现；归属：主体；归档修订：0013_memory。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 是 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 是 | 受信服务上下文 | 个人 |
| `memory_ids` | `json` | 待清理记忆标识集合 | 是 | 服务层校验后的业务输入 | 内部 |
| `state` | `varchar(32)` | 清理进度 | 是 | 服务层校验后的业务输入 | 内部 |
| `completed_at` | `datetime(6) UTC` | 完成时间 | 否 | 服务层校验后的业务输入 | 内部 |
| `kind` | `varchar(16)` | 单项遗忘或主体清空 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, subject_type, subject_id, state)`。

## memory_embeddings

按主体和模型版本隔离的记忆向量。状态：已实现；归属：主体；归档修订：0028_memory_vectors。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 是 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 是 | 受信服务上下文 | 个人 |
| `memory_id` | `varchar(64)` | 来源记忆标识 | 是 | 服务端校验后的向量结果 | 内部 |
| `memory_version_id` | `varchar(64)` | 来源记忆版本 | 是 | 服务端校验后的向量结果 | 内部 |
| `model_version_id` | `varchar(64)` | 向量模型版本 | 是 | 服务端校验后的向量结果 | 内部 |
| `run_id` | `varchar(64)` | 生成向量的运行 | 是 | 服务端校验后的向量结果 | 内部 |
| `dimensions` | `integer` | 向量维度 | 是 | 服务端校验后的向量结果 | 内部 |
| `embedding` | `json` | 记忆向量 | 是 | 服务端校验后的向量结果 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, environment, subject_type, subject_id, model_version_id)`；`(channel_id, memory_id)`。

## memory_consolidations

会话归档与人物画像后台整理任务。状态：已实现；归属：主体；归档修订：0033_layered_memory。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 是 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 是 | 受信服务上下文 | 个人 |
| `conversation_id` | `varchar(128)` | 来源会话标识 | 是 | 后台整理 | 内部 |
| `source_run_id` | `varchar(128)` | 恢复授权与冻结策略的来源运行 | 是 | 后台整理 | 内部 |
| `source_message_ids` | `json` | 本批完整消息引用 | 是 | 后台整理 | 内部 |
| `memory_ids` | `json` | 已生成的归档及画像引用 | 是 | 后台整理 | 内部 |
| `generation_run_id` | `varchar(128)` | 后台生成运行标识 | 否 | 后台整理 | 内部 |
| `state` | `varchar(32)` | 后台整理状态 | 是 | 后台整理 | 内部 |
| `attempt` | `integer` | 生成轮次 | 是 | 后台整理 | 内部 |
| `next_attempt_at` | `datetime(6) UTC` | 下次允许整理时间 | 是 | 后台整理 | 内部 |
| `error_code` | `varchar(128)` | 最后失败原因编码 | 否 | 后台整理 | 内部 |
| `settings` | `json` | 冻结策略与属性，不包含会话原文 | 是 | 后台整理 | 内部 |
| `preference_revision` | `integer` | 受理时主体偏好修订 | 是 | 后台整理 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, state, next_attempt_at)`；`(channel_id, conversation_id)`。

## memory_index_tasks

Milvus 向量同步与删除持久化任务。状态：已实现；归属：主体；归档修订：0048_vector_sync。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 是 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 是 | 受信服务上下文 | 个人 |
| `dimensions` | `integer` | 向量维度 | 是 | 服务端校验后的向量结果 | 内部 |
| `operation` | `varchar(16)` | 待同步的写入或删除操作 | 是 | 服务层同步协议 | 内部 |
| `state` | `varchar(16)` | 同步任务状态 | 是 | 服务层同步协议 | 内部 |
| `lease_token` | `varchar(64)` | 当前执行租约标识 | 否 | 服务层同步协议 | 内部 |
| `lease_until` | `datetime(6) UTC` | 租约失效时间 | 否 | 服务层同步协议 | 内部 |
| `next_attempt_at` | `datetime(6) UTC` | 下次同步时间 | 是 | 服务层同步协议 | 内部 |
| `attempts` | `integer` | 连续失败次数 | 是 | 服务层同步协议 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, next_attempt_at, state)`；`(channel_id, environment, subject_type, subject_id)`。
