# 工具定义与证据模型

模型版本 1.2.0；负责方案 10；需求 [10-工具管理.md](../../../../需求文档/10-工具管理.md)。总索引见 [README](../README.md)。

## tools

工具资源。状态：设计基线；归属：渠道；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `tool_code` | `varchar(64)` | 调用编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 工具名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `description` | `text` | 用途说明 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_type` | `varchar(32)` | 来源类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `owner` | `varchar(128)` | 负责人 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 启用状态 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, tool_code)`。

## tool_calls

工具实际调用。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `attempt_id` | `varchar(64)` | 尝试标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `tool_version_id` | `varchar(64)` | 工具版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `args_digest` | `varchar(64)` | 参数摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `state` | `varchar(32)` | 调用状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_request_id` | `varchar(256)` | 源请求标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `result_ref` | `varchar(64)` | 结果内容引用 | 否 | 服务层校验后的业务输入 | 内部 |
| `latency_ms` | `integer` | 耗时毫秒 | 否 | 服务层校验后的业务输入 | 内部 |
| `error` | `jsonb` | 脱敏错误 | 否 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, run_id)`；`(channel_id, attempt_id)`。

## evidence_refs

证据定位与授权范围。状态：设计基线；归属：主体；迁移：由所属方案新增。

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
| `source_type` | `varchar(64)` | 来源类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_id` | `varchar(128)` | 来源标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_version` | `varchar(128)` | 来源版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `observed_at` | `timestamptz` | 观测时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `location` | `jsonb` | 字段路径或文本位置 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `title` | `varchar(255)` | 授权范围内的来源名称 | 否 | 服务层校验后的业务输入 | 内部 |
| `artifact_id` | `varchar(64)` | 内容产物标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `authorization_scope` | `jsonb` | 授权范围摘要 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, source_type, source_id)`。

## 版本内容结构：tool

存于公共 `resource_versions.content`；数组项结构按名称单独列出。父版本、依赖与发布映射只保存一份。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `input_schema` | `jsonb` | 输入结构 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `output_schema` | `jsonb` | 输出结构 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `binding` | `jsonb` | 固定来源绑定 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `effect_type` | `varchar(32)` | 真实影响类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `required_scopes` | `jsonb` | 必要授权 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `allowed_data_domains` | `jsonb` | 允许数据域 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `subject_requirements` | `jsonb` | 主体限制 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `timeout_seconds` | `integer` | 超时秒数 | 是 | 服务层校验后的业务输入 | 内部 |
| `max_result_size` | `bigint` | 结果字节上限 | 是 | 服务层校验后的业务输入 | 内部 |
| `retry_policy` | `jsonb` | 重试策略 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `idempotency_policy` | `jsonb` | 源幂等策略 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `cache_policy` | `jsonb` | 完整范围缓存策略 | 是 | 服务层校验后的业务输入 | 敏感内容 |
