# 技能包模型

模型版本 1.7.0；负责方案 15；需求 [11-Skills管理.md](../../../../需求文档/11-Skills管理.md)。总索引见 [README](../README.md)。

## skills

技能资源。状态：已实现；归属：渠道；迁移：0015_skills。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `skill_code` | `varchar(64)` | 技能编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 技能名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `description` | `text` | 用途说明 | 是 | 服务层校验后的业务输入 | 内部 |
| `owner` | `varchar(128)` | 负责人 | 是 | 服务层校验后的业务输入 | 内部 |
| `tags` | `jsonb` | 发现标签 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `status` | `varchar(32)` | 启用状态 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, skill_code)`。

## skill_files

技能版本文件清单。状态：已实现；归属：渠道；迁移：0015_skills。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `version_id` | `varchar(64)` | 技能版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `relative_path` | `varchar(1024)` | 包内路径 | 是 | 服务层校验后的业务输入 | 内部 |
| `content_type` | `varchar(128)` | 媒体类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `size_bytes` | `bigint` | 文件字节数 | 是 | 服务层校验后的业务输入 | 内部 |
| `sha256` | `varchar(64)` | 文件摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `artifact_id` | `varchar(64)` | 受控内容引用 | 是 | 服务层校验后的业务输入 | 内部 |
| `loadable` | `boolean` | 当前是否可加载 | 是 | 服务层校验后的业务输入 | 内部 |
| `unavailable_reason` | `text` | 不可加载原因 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, version_id, relative_path)`。

## skill_tests

技能加载测试。状态：已实现；归属：主体；迁移：0015_skills。

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
| `version_id` | `varchar(64)` | 技能版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `release_snapshot_id` | `varchar(64)` | 上下文冻结快照 | 否 | 服务层校验后的业务输入 | 内部 |
| `selected_files` | `jsonb` | 实际加载文件 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `run_id` | `varchar(64)` | 运行标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `result` | `jsonb` | 测试结果 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `context_snapshot` | `jsonb` | 加载输入与版本冻结快照 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, version_id)`。

## 版本内容结构：skill

存于公共 `resource_versions.content`；数组项结构按名称单独列出。父版本、依赖与发布映射只保存一份。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `package_hash` | `varchar(64)` | 包内容摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `entry_file` | `varchar(255)` | 入口文件 | 是 | 服务层校验后的业务输入 | 内部 |
| `change_note` | `text` | 变更说明 | 是 | 服务层校验后的业务输入 | 内部 |
| `required_tool_versions` | `jsonb` | 工具具体版本 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `required_model_capabilities` | `jsonb` | 必要模型能力 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `input_variables` | `jsonb` | 输入变量 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `loading_mode` | `varchar(32)` | 加载方式 | 是 | 服务层校验后的业务输入 | 内部 |
| `priority` | `integer` | 加载优先级 | 是 | 服务层校验后的业务输入 | 内部 |
| `context_budget` | `integer` | 上下文预算 | 是 | 服务层校验后的业务输入 | 内部 |
| `allowed_agents` | `jsonb` | 授权智能体范围 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `metadata` | `jsonb` | 入口元数据与兼容字段 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `files` | `jsonb` | 文件哈希清单 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `artifact_id` | `varchar(64)` | 包归档受控产物 | 是 | 服务层校验后的业务输入 | 内部 |
| `tool_requirements` | `jsonb` | 可移植工具名称及版本要求 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `conflict_groups` | `jsonb` | 互斥规则分组 | 是 | 服务层校验后的业务输入 | 敏感内容 |
