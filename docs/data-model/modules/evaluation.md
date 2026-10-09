# 效果评测模型

模型版本 2.0.0；负责方案 24；需求 [13-效果评测.md](../../../../需求文档/13-效果评测.md)。总索引见 [README](../README.md)。

## evaluation_datasets

评测样本集。状态：已实现；归属：主体；归档修订：0024_evaluations。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `name` | `varchar(128)` | 样本集名称 | 是 | 服务层校验及受信上下文 | 内部 |
| `scenario` | `varchar(64)` | 适用能力类别 | 是 | 服务层校验及受信上下文 | 内部 |
| `owner` | `varchar(128)` | 负责人 | 是 | 服务层校验及受信上下文 | 内部 |
| `applicability` | `longtext` | 适用范围 | 是 | 服务层校验及受信上下文 | 内部 |
| `current_version_id` | `varchar(64)` | 当前样本版本 | 否 | 服务层校验及受信上下文 | 内部 |

普通索引：`(channel_id, id)`。

## evaluation_dataset_versions

不可变评测样本及标签版本。状态：已实现；归属：主体；归档修订：0024_evaluations。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `dataset_id` | `varchar(64)` | 所属样本集 | 是 | 服务层校验及受信上下文 | 内部 |
| `version_label` | `varchar(128)` | 版本名称 | 是 | 服务层校验及受信上下文 | 内部 |
| `content_digest` | `varchar(64)` | 数据和标签摘要 | 是 | 服务层校验及受信上下文 | 内部 |
| `case_ids` | `json` | 固定样本清单 | 是 | 服务层校验及受信上下文 | 内部 |
| `reference_versions` | `json` | 参考资料版本 | 是 | 服务层校验及受信上下文 | 内部 |
| `captured_at` | `datetime(6) UTC` | 固定数据时间 | 是 | 服务层校验及受信上下文 | 内部 |
| `reference_digests` | `json` | 参考资料版本内容摘要 | 是 | 受信已发布版本 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, dataset_id)`。

## evaluation_cases

不可变评测样本。状态：已实现；归属：主体；归档修订：0024_evaluations。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `dataset_id` | `varchar(64)` | 所属样本集 | 是 | 服务层校验及受信上下文 | 内部 |
| `case_key` | `varchar(128)` | 跨版本样本定位键 | 是 | 服务层校验及受信上下文 | 内部 |
| `title` | `varchar(255)` | 样本标题 | 是 | 服务层校验及受信上下文 | 内部 |
| `payload` | `json` | 输入、断言、标签、人工结论和来源 | 否 | 服务层校验及受信上下文 | 敏感 |
| `fixture_id` | `varchar(64)` | 固定工具数据引用 | 否 | 服务层校验及受信上下文 | 内部 |
| `previous_case_id` | `varchar(64)` | 修改前样本引用 | 否 | 服务层校验及受信上下文 | 内部 |
| `invalidated` | `boolean` | 来源已失效 | 是 | 服务层校验及受信上下文 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, dataset_id)`。

## evaluation_fixtures

评测工具夹具。状态：已实现；归属：主体；归档修订：0024_evaluations。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `payload` | `json` | 按工具版本及参数匹配的固定结果 | 否 | 服务层校验及受信上下文 | 敏感 |
| `captured_at` | `datetime(6) UTC` | 夹具采集时间 | 是 | 服务层校验及受信上下文 | 内部 |
| `invalidated` | `boolean` | 夹具来源已失效 | 是 | 服务层校验及受信上下文 | 内部 |

普通索引：`(channel_id, id)`。

## evaluations

批量评测调度任务。状态：已实现；归属：主体；归档修订：0024_evaluations。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `name` | `varchar(128)` | 任务名称 | 是 | 服务层校验及受信上下文 | 内部 |
| `dataset_version_id` | `varchar(64)` | 固定样本版本 | 是 | 服务层校验及受信上下文 | 内部 |
| `dataset_digest` | `varchar(64)` | 固定数据和标签摘要 | 是 | 服务层校验及受信上下文 | 内部 |
| `candidate_snapshots` | `json` | 冻结候选及完整依赖清单 | 是 | 服务层校验及受信上下文 | 内部 |
| `baseline_evaluation_id` | `varchar(64)` | 历史基线评测 | 否 | 服务层校验及受信上下文 | 内部 |
| `baseline_candidate_id` | `varchar(64)` | 基线候选 | 否 | 服务层校验及受信上下文 | 内部 |
| `execution_mode` | `varchar(32)` | 工具数据执行模式 | 是 | 服务层校验及受信上下文 | 内部 |
| `config` | `json` | 并发、预算、阈值和发布评测配置 | 是 | 服务层校验及受信上下文 | 内部 |
| `identity` | `json` | 原始调用身份 | 是 | 服务层校验及受信上下文 | 内部 |
| `state` | `varchar(32)` | 调度状态 | 是 | 服务层校验及受信上下文 | 内部 |
| `human_review` | `json` | 独立报告人工审阅 | 否 | 服务层校验及受信上下文 | 内部 |
| `expires_at` | `datetime(6) UTC` | 发布证据有效期 | 是 | 服务层校验及受信上下文 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, state)`。

## evaluation_results

评测单例及重跑记录。状态：已实现；归属：主体；归档修订：0024_evaluations。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `evaluation_id` | `varchar(64)` | 所属评测 | 是 | 服务层校验及受信上下文 | 内部 |
| `case_id` | `varchar(64)` | 固定样本 | 是 | 服务层校验及受信上下文 | 内部 |
| `candidate_id` | `varchar(64)` | 冻结候选 | 是 | 服务层校验及受信上下文 | 内部 |
| `attempt_number` | `integer` | 样本重跑序号 | 是 | 服务层校验及受信上下文 | 内部 |
| `run_id` | `varchar(64)` | 统一运行 | 否 | 服务层校验及受信上下文 | 内部 |
| `state` | `varchar(32)` | 单例状态 | 是 | 服务层校验及受信上下文 | 内部 |
| `judgment` | `json` | 确定性与语义判定 | 否 | 服务层校验及受信上下文 | 敏感 |
| `human_label` | `json` | 独立人工结论 | 否 | 服务层校验及受信上下文 | 敏感 |
| `claimed_at` | `datetime(6) UTC` | 派发占位时间 | 否 | 服务层校验及受信上下文 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, evaluation_id)`；`(channel_id, run_id)`。

## evaluation_reports

可追溯评测报告。状态：已实现；归属：主体；归档修订：0024_evaluations。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `evaluation_id` | `varchar(64)` | 所属评测 | 是 | 服务层校验及受信上下文 | 内部 |
| `report_digest` | `varchar(64)` | 报告证据摘要 | 是 | 服务层校验及受信上下文 | 内部 |
| `payload` | `json` | 覆盖、差异、阻断、用量与耗时 | 是 | 服务层校验及受信上下文 | 内部 |
| `reproducible` | `boolean` | 来源和固定数据可复现 | 是 | 服务层校验及受信上下文 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, evaluation_id)`。
