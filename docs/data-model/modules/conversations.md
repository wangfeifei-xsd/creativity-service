# 会话模型

模型版本 1.9.3；负责方案 12；需求 [07-会话管理.md](../../../../需求文档/07-会话管理.md)。总索引见 [README](../README.md)。

## conversations

业务会话。状态：已实现；归属：主体；归档修订：0012_conversations。

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
| `agent_id` | `varchar(64)` | 智能体标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `title` | `varchar(255)` | 会话标题 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 会话状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `active_run_id` | `varchar(64)` | 当前生成运行 | 否 | 服务层校验后的业务输入 | 内部 |
| `expires_at` | `timestamptz` | 保留到期时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `agent_code` | `varchar(128)` | 智能体调用编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `agent_name` | `varchar(128)` | 智能体名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `subject_name` | `varchar(128)` | 主体名称 | 否 | 服务层校验后的业务输入 | 内部 |
| `input_schema` | `jsonb` | 已接受的输入契约 | 是 | 服务层校验后的业务输入 | 内部 |
| `next_sequence` | `bigint` | 下一条消息顺序 | 是 | 服务层校验后的业务输入 | 内部 |
| `next_turn_sequence` | `bigint` | 下一轮顺序 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, data_scope_id, subject_type, subject_id, updated_at)`；`(channel_id, environment, data_scope_id, created_at, id)`。

## messages

会话消息。状态：已实现；归属：主体；归档修订：0012_conversations。

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
| `conversation_id` | `varchar(64)` | 会话标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `role` | `varchar(32)` | 消息角色 | 是 | 服务层校验后的业务输入 | 内部 |
| `content_parts` | `jsonb` | 文本及附件引用 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `run_id` | `varchar(64)` | 关联运行 | 否 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 消息完成状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `sequence` | `bigint` | 会话消息顺序 | 是 | 服务层校验后的业务输入 | 内部 |
| `turn_id` | `varchar(64)` | 关联轮次 | 否 | 服务层校验后的业务输入 | 内部 |
| `event_sequence` | `bigint` | 已投影运行事件顺序 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, conversation_id, created_at, id)`；`(channel_id, conversation_id, sequence)`。

## conversation_turns

会话轮次与消息幂等。状态：已实现；归属：主体；归档修订：0012_conversations。

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
| `conversation_id` | `varchar(64)` | 会话标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `client_message_id` | `varchar(128)` | 客户端消息标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `request_digest` | `varchar(64)` | 语义请求摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `user_message_id` | `varchar(64)` | 用户消息标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `run_id` | `varchar(64)` | 运行标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `sequence` | `bigint` | 会话内顺序 | 是 | 服务层校验后的业务输入 | 内部 |
| `assistant_message_id` | `varchar(64)` | 助手消息标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `agent_version_id` | `varchar(64)` | 本轮冻结智能体版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `version_label` | `varchar(128)` | 本轮版本名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `input` | `jsonb` | 不可变业务输入 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `input_schema` | `jsonb` | 本轮输入契约 | 是 | 服务层校验后的业务输入 | 内部 |
| `output_schema` | `jsonb` | 本轮输出契约 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_run_id` | `varchar(64)` | 普通追问来源运行 | 否 | 服务层校验后的业务输入 | 内部 |
| `confirmed_conditions` | `jsonb` | 上一轮已确认条件 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, conversation_id, client_message_id)`；`(channel_id, conversation_id, sequence)`。

## conversation_summaries

可溯源会话摘要。状态：已实现；归属：主体；归档修订：0012_conversations。

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
| `conversation_id` | `varchar(64)` | 会话标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_message_ids` | `jsonb` | 来源消息集合 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `version` | `bigint` | 摘要版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `content` | `text` | 摘要内容 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `status` | `varchar(32)` | 摘要有效状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `truncation` | `jsonb` | 摘要删减记录 | 是 | 服务层校验后的业务输入 | 内部 |
| `generation_run_id` | `varchar(64)` | 受控摘要生成运行 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, conversation_id, version)`。

## context_snapshots

实际模型上下文快照。状态：已实现；归属：主体；归档修订：0012_conversations。

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
| `included_message_ids` | `jsonb` | 实际包含消息 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `summary_version` | `varchar(64)` | 摘要版本 | 否 | 服务层校验后的业务输入 | 内部 |
| `memory_refs` | `jsonb` | 记忆具体版本 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `truncation` | `jsonb` | 删减原因及范围 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `conversation_id` | `varchar(64)` | 所属会话 | 是 | 服务层校验后的业务输入 | 内部 |
| `summary_id` | `varchar(64)` | 引用摘要标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `summary_source_ids` | `jsonb` | 摘要来源消息集合 | 是 | 服务层校验后的业务输入 | 内部 |
| `policy_version` | `varchar(32)` | 上下文选择策略版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `required_characters` | `bigint` | 必要指令和当前任务字符数 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`。
