# 智能匹配模型

模型版本 1.2.0；负责方案 21；需求 [15-智能匹配.md](../../../../需求文档/15-智能匹配.md)。总索引见 [README](../README.md)。

## matching_configs

匹配场景配置。状态：设计基线；归属：渠道；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `agent_id` | `varchar(64)` | 智能体标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `dictionary_mapping` | `jsonb` | 条件字典映射 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `candidate_tool` | `varchar(64)` | 候选工具版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `quote_tool` | `varchar(64)` | 报价工具版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `verification_tool` | `varchar(64)` | 复核工具版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `ranking_signals` | `jsonb` | 排序依据 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `top_k_limit` | `integer` | 候选数量上限 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, agent_id)`。

## match_feedback

推荐反馈。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `run_id` | `varchar(64)` | 来源运行 | 是 | 服务层校验后的业务输入 | 内部 |
| `entity_ref` | `jsonb` | 被反馈实体引用 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `feedback_type` | `varchar(64)` | 反馈类别 | 是 | 服务层校验后的业务输入 | 内部 |
| `comment` | `text` | 反馈说明 | 否 | 服务层校验后的业务输入 | 内部 |
| `review_status` | `varchar(32)` | 人工审阅状态 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`。
