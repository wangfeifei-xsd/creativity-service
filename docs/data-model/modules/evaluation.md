# 效果评测模型

模型版本 1.5.0；负责方案 24；需求 [13-效果评测.md](../../../../需求文档/13-效果评测.md)。总索引见 [README](../README.md)。

## evaluation_datasets

评测样本集。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `name` | `varchar(128)` | 样本集名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `scenario` | `varchar(64)` | 场景类别 | 是 | 服务层校验后的业务输入 | 内部 |
| `owner` | `varchar(128)` | 负责人 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 状态 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, scenario)`。

## evaluation_dataset_versions

不可变样本集版本。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `dataset_id` | `varchar(64)` | 样本集标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `version_label` | `varchar(128)` | 版本名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `content_digest` | `varchar(64)` | 内容摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `case_ids` | `jsonb` | 具体样本修订集合 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `fixture_ids` | `jsonb` | 固定夹具集合 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, dataset_id, version_label)`。

## evaluation_cases

评测样本及人工标签。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `dataset_id` | `varchar(64)` | 样本集标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `title` | `varchar(255)` | 样本标题 | 是 | 服务层校验后的业务输入 | 内部 |
| `input` | `jsonb` | 样本输入 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `context` | `jsonb` | 授权上下文 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `expected_constraints` | `jsonb` | 确定性预期 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `labels` | `jsonb` | 人工标签及争议 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `source_refs` | `jsonb` | 真实来源引用 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `previous_case_id` | `varchar(64)` | 上次样本修订 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, dataset_id)`。

## evaluation_fixtures

固定评测数据夹具。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `tool_results` | `jsonb` | 工具结果 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `catalog_snapshot` | `jsonb` | 候选目录 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `policy_version` | `varchar(64)` | 风险政策版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `metric_snapshot` | `jsonb` | 指标口径及值 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `captured_at` | `timestamptz` | 捕获时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_refs` | `jsonb` | 来源引用 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, created_at)`。

## evaluations

批量评测任务。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `dataset_version_id` | `varchar(64)` | 样本集版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `candidate_snapshots` | `jsonb` | 候选依赖快照 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `baseline_snapshot_id` | `varchar(64)` | 基线快照 | 否 | 服务层校验后的业务输入 | 内部 |
| `execution_mode` | `varchar(32)` | 数据执行方式 | 是 | 服务层校验后的业务输入 | 内部 |
| `budget` | `jsonb` | 预算上限 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `state` | `varchar(32)` | 任务状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `dispatch_paused` | `boolean` | 停止派发标记 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, state, created_at)`。

## evaluation_results

逐样本评测结果。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `evaluation_id` | `varchar(64)` | 评测任务 | 是 | 服务层校验后的业务输入 | 内部 |
| `case_id` | `varchar(64)` | 样本标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `run_id` | `varchar(64)` | 实际运行 | 是 | 服务层校验后的业务输入 | 内部 |
| `state` | `varchar(32)` | 完成状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `scores` | `jsonb` | 指标得分 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `violations` | `jsonb` | 阻断项 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `judge_details` | `jsonb` | 裁判配置与结果 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `human_label` | `jsonb` | 人工复核 | 否 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, evaluation_id, case_id)`。

## evaluation_reports

评测对比与发布证据。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `evaluation_id` | `varchar(64)` | 评测任务 | 是 | 服务层校验后的业务输入 | 内部 |
| `dependencies_digest` | `varchar(64)` | 被评测依赖摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `metrics` | `jsonb` | 比较指标 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `coverage` | `jsonb` | 样本覆盖及无效原因 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `cost` | `jsonb` | 分币种成本 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `latency` | `jsonb` | 耗时统计 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `decision` | `varchar(32)` | 发布判定 | 是 | 服务层校验后的业务输入 | 内部 |
| `artifact_id` | `varchar(64)` | 报告文件 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, evaluation_id)`。
