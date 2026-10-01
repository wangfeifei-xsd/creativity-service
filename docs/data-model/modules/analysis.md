# 指标分析模型

模型版本 1.2.0；负责方案 23；需求 [17-数据分析.md](../../../../需求文档/17-数据分析.md)。总索引见 [README](../README.md)。

## metrics

指标目录资源。状态：设计基线；归属：渠道；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `metric_code` | `varchar(64)` | 指标编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 指标名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 当前可用状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `owner` | `varchar(128)` | 业务负责人 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, metric_code)`。

## analysis_plans

经授权指标查询计划。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `metric_refs` | `jsonb` | 指标具体版本 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `periods` | `jsonb` | 实际查询周期 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `timezone` | `varchar(64)` | 业务时区 | 是 | 服务层校验后的业务输入 | 内部 |
| `filters` | `jsonb` | 已授权筛选 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `comparisons` | `jsonb` | 比较口径 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`。

## analysis_datasets

指标数据快照。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `source_version` | `varchar(128)` | 来源版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `observed_at` | `timestamptz` | 采集时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `period` | `jsonb` | 覆盖周期 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `completeness` | `jsonb` | 完整性与缺失范围 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `values` | `jsonb` | 含单位和精度的数值 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `source_refs` | `jsonb` | 来源证据 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`。

## calculation_steps

确定性指标计算。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `input_refs` | `jsonb` | 输入快照引用 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `formula` | `jsonb` | 白名单公式 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `precision` | `integer` | 结果精度 | 是 | 服务层校验后的业务输入 | 内部 |
| `output_values` | `jsonb` | 计算结果 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `code_version` | `varchar(64)` | 计算实现版本 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`。

## analysis_reports

指标分析报告。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `question` | `text` | 业务问题 | 是 | 服务层校验后的业务输入 | 内部 |
| `plan_id` | `varchar(64)` | 查询计划 | 是 | 服务层校验后的业务输入 | 内部 |
| `tables` | `jsonb` | 已校验表格 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `chart_specs` | `jsonb` | 白名单图表 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `findings` | `jsonb` | 来源明确的结论 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `limitations` | `jsonb` | 缺失与限制 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `artifact_ids` | `jsonb` | 导出文件引用 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`。

## analysis_feedback

分析反馈。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `metric_ref` | `varchar(64)` | 指标版本引用 | 否 | 服务层校验后的业务输入 | 内部 |
| `finding_ref` | `varchar(128)` | 结论引用 | 否 | 服务层校验后的业务输入 | 内部 |
| `feedback_type` | `varchar(64)` | 反馈类别 | 是 | 服务层校验后的业务输入 | 内部 |
| `comment` | `text` | 反馈说明 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`。

## 版本内容结构：metric

存于公共 `resource_versions.content`；数组项结构按名称单独列出。父版本、依赖与发布映射只保存一份。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `description` | `text` | 业务定义 | 是 | 服务层校验后的业务输入 | 内部 |
| `unit` | `varchar(64)` | 计量单位 | 是 | 服务层校验后的业务输入 | 内部 |
| `currency` | `varchar(3)` | 币种 | 否 | 服务层校验后的业务输入 | 内部 |
| `grain` | `varchar(64)` | 数据粒度 | 是 | 服务层校验后的业务输入 | 内部 |
| `formula` | `jsonb` | 确定性公式定义 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `precision` | `integer` | 精度 | 是 | 服务层校验后的业务输入 | 内部 |
| `allowed_dimensions` | `jsonb` | 可用维度 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `supported_periods` | `jsonb` | 支持周期 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `filters` | `jsonb` | 允许筛选 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `source_tool` | `varchar(64)` | 来源工具版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `permission` | `jsonb` | 必要授权 | 是 | 服务层校验后的业务输入 | 敏感内容 |
