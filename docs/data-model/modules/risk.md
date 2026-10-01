# 风险评估模型

模型版本 1.2.0；负责方案 22；需求 [16-风险评估.md](../../../../需求文档/16-风险评估.md)。总索引见 [README](../README.md)。

## risk_policies

渠道风险政策资源。状态：设计基线；归属：渠道；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `code` | `varchar(64)` | 政策编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 政策名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `owner` | `varchar(128)` | 业务负责人 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 启用状态 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, code)`。

## risk_assessments

风险评估结果。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `target_ref` | `jsonb` | 评估对象 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `target_version` | `varchar(128)` | 对象版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `input_digest` | `varchar(64)` | 输入摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `policy_version_id` | `varchar(64)` | 政策版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `findings` | `jsonb` | 规则及证据命中 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `coverage` | `jsonb` | 实际检查范围 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `decision` | `varchar(32)` | 结论类别 | 是 | 服务层校验后的业务输入 | 内部 |
| `overall_severity` | `varchar(32)` | 有效总体严重度 | 否 | 服务层校验后的业务输入 | 内部 |
| `missing_facts` | `jsonb` | 缺失事实 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `batch_id` | `varchar(64)` | 所属批次 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`；`(channel_id, batch_id)`。

## risk_batches

有限批量风险任务。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `target_refs` | `jsonb` | 授权目标集合 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `child_run_ids` | `jsonb` | 子任务集合 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `state` | `varchar(32)` | 批次状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `summary` | `jsonb` | 分状态统计 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, state, created_at)`。

## risk_feedback

风险人工反馈修订。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `assessment_id` | `varchar(64)` | 评估标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `finding_id` | `varchar(64)` | 命中项标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `label` | `varchar(32)` | 人工标签 | 是 | 服务层校验后的业务输入 | 内部 |
| `reviewer_id` | `varchar(128)` | 复核主体 | 是 | 服务层校验后的业务输入 | 内部 |
| `reason` | `text` | 复核说明 | 是 | 服务层校验后的业务输入 | 内部 |
| `previous_feedback_id` | `varchar(64)` | 上次反馈修订 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, assessment_id, finding_id)`。

## 版本内容结构：risk_policy

存于公共 `resource_versions.content`；数组项结构按名称单独列出。父版本、依赖与发布映射只保存一份。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `effective_from` | `timestamptz` | 开始生效时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `effective_to` | `timestamptz` | 失效时间 | 否 | 服务层校验后的业务输入 | 内部 |
| `categories` | `jsonb` | 风险类别与中文名称 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `rules` | `jsonb` | 规则定义清单 | 是 | 服务层校验后的业务输入 | 敏感内容 |

## 版本内容结构：rule_item

存于公共 `resource_versions.content`；数组项结构按名称单独列出。父版本、依赖与发布映射只保存一份。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `rule_code` | `varchar(64)` | 规则编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 规则名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `category` | `varchar(64)` | 风险类别 | 是 | 服务层校验后的业务输入 | 内部 |
| `severity` | `varchar(32)` | 严重程度 | 是 | 服务层校验后的业务输入 | 内部 |
| `basis_type` | `varchar(32)` | 确定规则或语义判断 | 是 | 服务层校验后的业务输入 | 内部 |
| `required_facts` | `jsonb` | 必要事实 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `condition` | `jsonb` | 确定性条件 | 否 | 服务层校验后的业务输入 | 敏感内容 |
| `evaluation_guidance` | `text` | 语义判断指引 | 否 | 服务层校验后的业务输入 | 内部 |
| `action_suggestion` | `text` | 建议 | 是 | 服务层校验后的业务输入 | 内部 |
