# 智能体定义模型

模型版本 1.2.0；负责方案 16；需求 [06-Agent与流程管理.md](../../../../需求文档/06-Agent与流程管理.md)。总索引见 [README](../README.md)。

## agents

智能体资源。状态：已实现；归属：渠道；迁移：0016_agents。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `agent_code` | `varchar(64)` | 调用编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 智能体名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `description` | `text` | 用途说明 | 是 | 服务层校验后的业务输入 | 内部 |
| `owner` | `varchar(128)` | 负责人标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 启用状态 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, agent_code)`。

## platform_templates

平台能力模板。状态：设计基线；归属：渠道；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `code` | `varchar(64)` | 模板编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 模板名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `scenario` | `varchar(64)` | 场景类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `content` | `jsonb` | 无凭据可复制模板内容 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `version` | `varchar(64)` | 模板版本 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, code)`。

控制面用途：`templates`；账号/角色身份引用不赋予其他渠道数据访问权。

## agent_candidates

冻结智能体候选快照。状态：已实现；归属：主体；迁移：0016_agents。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 服务层校验后的业务输入 | 内部 |
| `data_scope_id` | `varchar(64)` | 业务数据域标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 服务层校验后的业务输入 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 服务层校验后的业务输入 | 内部 |
| `agent_id` | `varchar(64)` | 智能体标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_version_id` | `varchar(64)` | 来源版本标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_revision` | `bigint` | 来源草稿修订号 | 是 | 服务层校验后的业务输入 | 内部 |
| `purpose` | `varchar(32)` | 候选用途 | 是 | 服务层校验后的业务输入 | 内部 |
| `content_digest` | `varchar(64)` | 内容摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `dependencies_digest` | `varchar(64)` | 完整依赖摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `candidate_digest` | `varchar(64)` | 候选组合摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `spec` | `jsonb` | 不可变执行定义 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, agent_id, created_at)`。

## agent_release_records

智能体发布检查与操作记录。状态：已实现；归属：环境；迁移：0016_agents。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 目标环境 | 是 | 服务层校验后的业务输入 | 内部 |
| `agent_id` | `varchar(64)` | 智能体标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `version_id` | `varchar(64)` | 生效版本标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `version_label` | `varchar(128)` | 版本名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `previous_version_id` | `varchar(64)` | 原生效版本标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `source_revision` | `bigint` | 发布来源修订号 | 是 | 服务层校验后的业务输入 | 内部 |
| `operation` | `varchar(32)` | 操作类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `note` | `varchar(1024)` | 操作说明 | 是 | 服务层校验后的业务输入 | 内部 |
| `actor_id` | `varchar(128)` | 操作人标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `actor_name` | `varchar(128)` | 操作人名称 | 否 | 服务层校验后的业务输入 | 内部 |
| `content_digest` | `varchar(64)` | 内容摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `dependencies_digest` | `varchar(64)` | 完整依赖摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `evidence_refs` | `jsonb` | 评测报告引用 | 是 | 服务层校验后的业务输入 | 内部 |
| `checks` | `jsonb` | 发布检查证据 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, environment, agent_id, created_at)`。

## agent_environment_states

智能体各环境停用状态。状态：已实现；归属：环境；迁移：0016_agents。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 目标环境 | 是 | 服务层校验后的业务输入 | 内部 |
| `agent_id` | `varchar(64)` | 智能体标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 当前环境启用状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `reason` | `varchar(1024)` | 状态变更原因 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, agent_id)`。

## 版本内容结构：agent

存于公共 `resource_versions.content`；数组项结构按名称单独列出。父版本、依赖与发布映射只保存一份。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `workflow_type` | `varchar(64)` | 流程类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `entrypoint` | `varchar(128)` | 固定执行入口 | 是 | 服务层校验后的业务输入 | 内部 |
| `input_schema` | `jsonb` | 输入结构 | 是 | 服务层校验后的业务输入 | 内部 |
| `output_schema` | `jsonb` | 输出结构 | 是 | 服务层校验后的业务输入 | 内部 |
| `start_step` | `varchar(128)` | 起始步骤 | 是 | 服务层校验后的业务输入 | 内部 |
| `steps` | `jsonb` | 步骤与输入来源 | 是 | 服务层校验后的业务输入 | 内部 |
| `edges` | `jsonb` | 条件流转与终止出口 | 是 | 服务层校验后的业务输入 | 内部 |
| `bindings` | `jsonb` | 直接依赖版本与工具白名单 | 是 | 服务层校验后的业务输入 | 内部 |
| `limits` | `jsonb` | 运行预算、重试及循环硬上限 | 是 | 服务层校验后的业务输入 | 内部 |
| `context` | `jsonb` | 显式会话、记忆与上下文策略 | 是 | 服务层校验后的业务输入 | 内部 |
