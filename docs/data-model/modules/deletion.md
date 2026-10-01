# 删除传播与保留模型

模型版本 1.2.0；负责方案 25；需求 [00-需求总纲.md](../../../../需求文档/00-需求总纲.md)。总索引见 [README](../README.md)。

## deletion_jobs

删除传播任务。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `marker_id` | `varchar(64)` | 删除标记 | 是 | 服务层校验后的业务输入 | 内部 |
| `scope_description` | `jsonb` | 待清理范围元数据 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `affected_resources` | `jsonb` | 影响引用清单 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `state` | `varchar(32)` | 清理状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `completed_at` | `timestamptz` | 完成时间 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, state, created_at)`。

## deletion_work_items

可重试模块清理步骤。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `job_id` | `varchar(64)` | 删除任务 | 是 | 服务层校验后的业务输入 | 内部 |
| `handler_key` | `varchar(128)` | 登记处理器 | 是 | 服务层校验后的业务输入 | 内部 |
| `target_type` | `varchar(64)` | 对象类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `target_id` | `varchar(128)` | 对象标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `state` | `varchar(32)` | 步骤状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `attempts` | `integer` | 尝试次数 | 是 | 服务层校验后的业务输入 | 内部 |
| `next_attempt_at` | `timestamptz` | 重试时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `last_error` | `varchar(64)` | 脱敏错误类别 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, job_id, handler_key, target_id)`。
