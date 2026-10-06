# 公共设施模型

模型版本 2.0.0；负责方案 03；需求 [00-需求总纲.md](../../../../需求文档/00-需求总纲.md)。总索引见 [README](../README.md)。

## resource_versions

资源版本与草稿。状态：已实现；归属：渠道；归档修订：0001_core。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `resource_type` | `varchar(64)` | 资源类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `resource_id` | `varchar(64)` | 稳定资源标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `version_label` | `varchar(128)` | 可读版本名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `state` | `varchar(32)` | 版本状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `content` | `jsonb` | 版本内容 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `content_digest` | `varchar(64)` | 规范化内容摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `dependencies` | `jsonb` | 固定依赖清单 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `dependencies_digest` | `varchar(64)` | 依赖摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `output_schema` | `jsonb` | 输出结构定义 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `created_by` | `varchar(128)` | 创建主体标识 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, resource_type, resource_id, state)`。

## release_mappings

环境生效版本映射。状态：已实现；归属：环境；归档修订：0001_core。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `resource_type` | `varchar(64)` | 资源类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `resource_id` | `varchar(64)` | 稳定资源标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `version_id` | `varchar(64)` | 生效版本标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `published_by` | `varchar(128)` | 发布主体标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `release_note` | `varchar(1024)` | 发布说明 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, resource_type, resource_id)`。

## release_snapshots

执行依赖冻结快照。状态：已实现；归属：主体；归档修订：0001_core。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `run_id` | `varchar(64)` | 所属运行标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `purpose` | `varchar(32)` | 使用用途 | 是 | 服务层校验后的业务输入 | 内部 |
| `versions` | `jsonb` | 具体版本及草稿内容快照 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `dependencies_digest` | `varchar(64)` | 全量依赖摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `output_schema` | `jsonb` | 固定输出结构 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, environment, run_id)`。

## resource_references

版本引用关系。状态：已实现；归属：渠道；归档修订：0001_core。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `source_version_id` | `varchar(64)` | 引用方版本标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `target_version_id` | `varchar(64)` | 被引用版本标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `target_resource_type` | `varchar(64)` | 被引用资源类型 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, target_version_id)`；`(channel_id, source_version_id)`。

## audit_events

操作审计元数据。状态：已实现；归属：主体；归档修订：0001_core。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `actor_id` | `varchar(128)` | 操作主体标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `action` | `varchar(128)` | 操作名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `target_type` | `varchar(64)` | 对象类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `target_id` | `varchar(128)` | 对象标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `request_id` | `varchar(64)` | 请求标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `outcome` | `varchar(32)` | 操作结果 | 是 | 服务层校验后的业务输入 | 内部 |
| `summary` | `jsonb` | 脱敏变更摘要 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, created_at)`；`(channel_id, target_type, target_id)`；`(channel_id, created_at, id)`。

控制面用途：`audit`；账号/角色身份引用不赋予其他渠道数据访问权。

## credentials

加密服务凭据。状态：已实现；归属：环境；归档修订：0001_core。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `purpose` | `varchar(32)` | 凭据用途 | 是 | 服务层校验后的业务输入 | 内部 |
| `ciphertext` | `bytea` | 认证加密密文 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `key_version` | `varchar(64)` | 加密密钥版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `state` | `varchar(32)` | 凭据状态 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, purpose, state)`。

## artifacts

受控文件与产物元数据。状态：已实现；归属：主体；归档修订：0001_core。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `name` | `varchar(255)` | 文件显示名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `content_type` | `varchar(128)` | 内容媒体类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `object_key` | `varchar(1024)` | 私有对象路径 | 是 | 服务层校验后的业务输入 | 内部 |
| `size_bytes` | `bigint` | 文件字节数 | 是 | 服务层校验后的业务输入 | 内部 |
| `sha256` | `varchar(64)` | 内容摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `state` | `varchar(32)` | 暂存及可用状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `expires_at` | `timestamptz` | 保存到期时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `upload_expires_at` | `timestamptz` | 暂存到期时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `registered_at` | `timestamptz` | 登记完成时间 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, state, upload_expires_at)`。

## source_links

内容来源与派生关系。状态：已实现；归属：主体；归档修订：0001_core。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `source_type` | `varchar(64)` | 来源类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_id` | `varchar(128)` | 来源标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `derived_type` | `varchar(64)` | 派生类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `derived_id` | `varchar(128)` | 派生标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `source_version` | `varchar(128)` | 来源版本 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, source_type, source_id)`；`(channel_id, environment, derived_type, derived_id)`。

## deletion_markers

不可恢复使用的删除标记。状态：已实现；归属：主体；归档修订：0001_core。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `target_type` | `varchar(64)` | 删除对象类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `target_id` | `varchar(128)` | 删除对象标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `reason_code` | `varchar(64)` | 删除原因类别 | 是 | 服务层校验后的业务输入 | 内部 |
| `requested_by` | `varchar(128)` | 删除申请主体 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, target_type, target_id)`。

## recovery_barriers

内容恢复屏障。状态：已实现；归属：主体；归档修订：0001_core。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `state` | `varchar(32)` | 恢复屏障状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `recovery_id` | `varchar(64)` | 本次恢复标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `marker_digest` | `varchar(64)` | 已校验删除账本摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `verified_at` | `timestamptz` | 删除账本核对时间 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, subject_type, subject_id)`。

## creativity_alembic_version

平台数据库迁移版本记录。状态：已实现；归属：系统；归档修订：02 迁移适配。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `version_num` | `varchar(64)` | 当前数据库迁移修订编号 | 是 | Alembic 修订 | 内部 |
| `channel_id` | `varchar(64)` | 迁移记录所属系统渠道 | 是 | 系统渠道 | 内部 |

普通索引：无。

控制面用途：`migrations`；账号/角色身份引用不赋予其他渠道数据访问权。

## resource_uses

资源实际使用记录，每个资源在同一运行中计一次。状态：已实现；归属：环境；归档修订：0043_resource_management。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 渠道、运行和稳定资源标识的摘要 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 使用环境 | 是 | 受信执行上下文 | 内部 |
| `resource_type` | `varchar(64)` | 资源类型 | 是 | 受信执行上下文 | 内部 |
| `resource_id` | `varchar(64)` | 稳定资源标识 | 是 | 受信执行上下文 | 内部 |
| `resource_name` | `varchar(128)` | 使用时资源名称 | 是 | 受信执行上下文 | 内部 |
| `run_id` | `varchar(64)` | 关联运行标识 | 是 | 受信执行上下文 | 内部 |
| `agent_name` | `varchar(128)` | 使用时智能体名称 | 否 | 受信执行上下文 | 内部 |
| `caller_name` | `varchar(128)` | 使用时调用方名称 | 否 | 受信执行上下文 | 内部 |
| `purpose` | `varchar(32)` | 运行用途 | 是 | 受信执行上下文 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, resource_type, resource_id)`；`(channel_id, run_id)`。
