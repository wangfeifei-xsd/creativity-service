# 运行受理与编排模型

模型版本 1.8.0；负责方案 11/17；需求 [12-执行记录与任务运行.md](../../../../需求文档/12-执行记录与任务运行.md)。总索引见 [README](../README.md)。

## runs

逻辑执行任务。状态：已实现；归属：主体；迁移：0011_runs。

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
| `source_type` | `varchar(32)` | 发起来源 | 是 | 服务层校验后的业务输入 | 内部 |
| `client_id` | `varchar(64)` | 接入服务 | 否 | 服务层校验后的业务输入 | 内部 |
| `key_id` | `varchar(64)` | 渠道密钥 | 否 | 服务层校验后的业务输入 | 内部 |
| `actor_id` | `varchar(128)` | 管理发起人 | 否 | 服务层校验后的业务输入 | 内部 |
| `agent_id` | `varchar(64)` | 智能体 | 是 | 服务层校验后的业务输入 | 内部 |
| `agent_version_id` | `varchar(64)` | 智能体版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `release_snapshot_id` | `varchar(64)` | 冻结依赖快照 | 是 | 服务层校验后的业务输入 | 内部 |
| `purpose` | `varchar(32)` | 调用用途 | 是 | 服务层校验后的业务输入 | 内部 |
| `state` | `varchar(32)` | 技术状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `deadline` | `timestamptz` | 全程截止时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `conversation_id` | `varchar(64)` | 会话标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `parent_run_id` | `varchar(64)` | 来源运行 | 否 | 服务层校验后的业务输入 | 内部 |
| `input_ref` | `varchar(64)` | 输入内容引用 | 是 | 服务层校验后的业务输入 | 内部 |
| `result_ref` | `varchar(64)` | 正式结果引用 | 否 | 服务层校验后的业务输入 | 内部 |
| `partial_output_ref` | `varchar(64)` | 部分输出引用 | 否 | 服务层校验后的业务输入 | 内部 |
| `error` | `jsonb` | 脱敏错误 | 否 | 服务层校验后的业务输入 | 敏感内容 |
| `completed_at` | `timestamptz` | 终结时间 | 否 | 服务层校验后的业务输入 | 内部 |
| `agent_code` | `varchar(128)` | 智能体调用编码 | 是 | 受信服务校验与事务写入 | 内部 |
| `agent_name` | `varchar(128)` | 受理时智能体名称 | 是 | 受信服务校验与事务写入 | 内部 |
| `identity` | `jsonb` | 不含访问令牌的原始身份 | 是 | 受信服务校验与事务写入 | 内部 |
| `execution_policy` | `jsonb` | 冻结执行限额与步骤策略 | 是 | 受信服务校验与事务写入 | 内部 |
| `timeout_seconds` | `integer` | 全程时限秒数 | 是 | 受信服务校验与事务写入 | 内部 |
| `timeout_source` | `varchar(128)` | 时限配置来源 | 是 | 受信服务校验与事务写入 | 内部 |
| `event_sequence` | `bigint` | 最后事件序号 | 是 | 受信服务校验与事务写入 | 内部 |
| `resources_released` | `boolean` | 终态占用已释放 | 是 | 受信服务校验与事务写入 | 内部 |
| `recovery_count` | `integer` | 租约失效恢复次数 | 是 | 受信服务校验与事务写入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, state, created_at)`。

## run_idempotency

接入请求幂等。状态：已实现；归属：主体；迁移：0011_runs。

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
| `client_id` | `varchar(64)` | 接入服务稳定标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `agent_id` | `varchar(64)` | 智能体标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `key` | `varchar(128)` | 调用方幂等键 | 是 | 服务层校验后的业务输入 | 内部 |
| `request_digest` | `varchar(64)` | 语义请求摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `run_id` | `varchar(64)` | 首次受理运行 | 是 | 服务层校验后的业务输入 | 内部 |
| `expires_at` | `timestamptz` | 最早可清理时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `identity_type` | `varchar(32)` | 稳定身份来源类型 | 是 | 受信服务校验与事务写入 | 内部 |
| `identity_id` | `varchar(128)` | 稳定调用服务或管理操作者 | 是 | 受信服务校验与事务写入 | 内部 |
| `scope_digest` | `varchar(64)` | 幂等范围摘要 | 是 | 受信服务校验与事务写入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, data_scope_id, subject_type, subject_id, client_id, agent_id, key)`；`(channel_id, scope_digest, key)`。

## dispatch_outbox

可靠调度投递意图。状态：已实现；归属：环境；迁移：0011_runs。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `run_id` | `varchar(64)` | 运行标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `state` | `varchar(32)` | 投递状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `dispatch_attempts` | `integer` | 投递次数 | 是 | 服务层校验后的业务输入 | 内部 |
| `next_attempt_at` | `timestamptz` | 下次补偿时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `last_error` | `varchar(64)` | 脱敏错误类别 | 否 | 服务层校验后的业务输入 | 内部 |
| `delivery_version` | `bigint` | 本次投递声明代次 | 是 | 受信服务校验与事务写入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, state, next_attempt_at)`。

## run_steps

执行步骤。状态：已实现；归属：主体；迁移：0011_runs。

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
| `node_key` | `varchar(128)` | 流程节点 | 是 | 服务层校验后的业务输入 | 内部 |
| `state` | `varchar(32)` | 步骤状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `input_ref` | `varchar(64)` | 输入引用 | 否 | 服务层校验后的业务输入 | 内部 |
| `output_ref` | `varchar(64)` | 输出引用 | 否 | 服务层校验后的业务输入 | 内部 |
| `checkpoint_ref` | `varchar(64)` | 恢复点 | 否 | 服务层校验后的业务输入 | 内部 |
| `sequence` | `bigint` | 步骤顺序 | 是 | 服务层校验后的业务输入 | 内部 |
| `attempt_count` | `integer` | 实际尝试累计次数 | 是 | 受信服务校验与事务写入 | 内部 |
| `lease_version` | `bigint` | 最近有效提交租约代次 | 是 | 受信服务校验与事务写入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, run_id, sequence)`。

## attempts

外部实际尝试。状态：已实现；归属：主体；迁移：0011_runs。

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
| `step_id` | `varchar(64)` | 步骤标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `kind` | `varchar(32)` | 模型或工具类别 | 是 | 服务层校验后的业务输入 | 内部 |
| `target_version_id` | `varchar(64)` | 实际依赖版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `provider_credential_id` | `varchar(64)` | 实际供应商凭据 | 否 | 服务层校验后的业务输入 | 内部 |
| `source_request_id` | `varchar(256)` | 源请求标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `state` | `varchar(32)` | 尝试状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `started_at` | `timestamptz` | 开始时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `finished_at` | `timestamptz` | 结束时间 | 否 | 服务层校验后的业务输入 | 内部 |
| `error` | `jsonb` | 脱敏错误 | 否 | 服务层校验后的业务输入 | 敏感内容 |
| `usage_id` | `varchar(64)` | 用量账本引用 | 否 | 服务层校验后的业务输入 | 内部 |
| `lease_version` | `bigint` | 调用所属租约代次 | 是 | 受信服务校验与事务写入 | 内部 |
| `sent_at` | `timestamptz` | 外部发送意图登记时间 | 否 | 受信服务校验与事务写入 | 内部 |
| `retryable` | `boolean` | 明确失败是否允许有限重试 | 是 | 受信服务校验与事务写入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`；`(channel_id, step_id, created_at)`。

## run_events

可补发运行事件。状态：已实现；归属：主体；迁移：0011_runs。

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
| `sequence` | `bigint` | 运行内单调序号 | 是 | 服务层校验后的业务输入 | 内部 |
| `event_type` | `varchar(64)` | 事件类别 | 是 | 服务层校验后的业务输入 | 内部 |
| `payload_ref` | `varchar(64)` | 事件内容引用 | 否 | 服务层校验后的业务输入 | 内部 |
| `expires_at` | `timestamptz` | 事件失效时间 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, run_id, sequence)`。

## run_leases

工作进程执行租约。状态：已实现；归属：环境；迁移：0011_runs。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `run_id` | `varchar(64)` | 运行标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `worker_id` | `varchar(128)` | 进程标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `lease_version` | `bigint` | 租约代次 | 是 | 服务层校验后的业务输入 | 内部 |
| `heartbeat_at` | `timestamptz` | 最近续租时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `expires_at` | `timestamptz` | 租约到期时间 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`。

## checkpoints

自有流程恢复点。状态：已实现；归属：主体；迁移：0011_runs。

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
| `namespace` | `varchar(128)` | 流程命名空间 | 是 | 服务层校验后的业务输入 | 内部 |
| `checkpoint_key` | `varchar(128)` | 恢复点逻辑标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `parent_key` | `varchar(128)` | 父恢复点 | 否 | 服务层校验后的业务输入 | 内部 |
| `lease_version` | `bigint` | 提交租约代次 | 是 | 服务层校验后的业务输入 | 内部 |
| `release_snapshot_id` | `varchar(64)` | 固定依赖快照 | 是 | 服务层校验后的业务输入 | 内部 |
| `state_ref` | `varchar(64)` | 状态内容引用 | 是 | 服务层校验后的业务输入 | 内部 |
| `metadata` | `jsonb` | 无原文恢复元数据 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, run_id, namespace, checkpoint_key)`。

## checkpoint_writes

流程恢复点待提交写入。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `checkpoint_id` | `varchar(64)` | 恢复点标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `task_id` | `varchar(128)` | 节点任务标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `sequence` | `bigint` | 节点写入序号 | 是 | 服务层校验后的业务输入 | 内部 |
| `payload_ref` | `varchar(64)` | 写入内容引用 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, checkpoint_id, task_id, sequence)`。

## run_contents

运行敏感内容引用。状态：已实现；归属：主体；迁移：0011_runs。

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
| `run_id` | `varchar(64)` | 所属运行 | 是 | 受信服务校验与事务写入 | 内部 |
| `kind` | `varchar(32)` | 内容用途 | 是 | 受信服务校验与事务写入 | 内部 |
| `payload` | `jsonb` | 受控内容正文 | 否 | 受信服务校验与事务写入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`。

## run_recoveries

执行租约恢复判断记录。状态：已实现；归属：主体；迁移：0011_runs。

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
| `run_id` | `varchar(64)` | 所属运行 | 是 | 受信服务校验与事务写入 | 内部 |
| `lease_version` | `bigint` | 失效租约代次 | 是 | 受信服务校验与事务写入 | 内部 |
| `decision` | `varchar(32)` | 恢复判断结果 | 是 | 受信服务校验与事务写入 | 内部 |
| `reason` | `varchar(64)` | 脱敏原因类别 | 是 | 受信服务校验与事务写入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, run_id, lease_version)`。

## run_occupancies

运行会话执行占用。状态：已实现；归属：主体；迁移：0011_runs。

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
| `conversation_id` | `varchar(64)` | 占用会话 | 是 | 受信服务校验与事务写入 | 内部 |
| `run_id` | `varchar(64)` | 占用运行 | 是 | 受信服务校验与事务写入 | 内部 |
| `state` | `varchar(32)` | 占用状态 | 是 | 受信服务校验与事务写入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, conversation_id)`。
