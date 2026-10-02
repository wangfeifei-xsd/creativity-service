# 账号与授权模型

模型版本 1.4.0；负责方案 04；需求 [02-账号与权限管理.md](../../../../需求文档/02-账号与权限管理.md)。总索引见 [README](../README.md)。

## platform_accounts

平台账号。状态：已实现；归属：渠道；迁移：0002_iam。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `login_name` | `varchar(128)` | 规范化登录名 | 是 | 服务层校验后的业务输入 | 内部 |
| `display_name` | `varchar(128)` | 显示名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `password_hash` | `varchar(512)` | 密码安全摘要 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `platform_roles` | `jsonb` | 平台角色清单 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `status` | `varchar(32)` | 账号状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `must_change_password` | `boolean` | 首次修改密码标记 | 是 | 服务层校验后的业务输入 | 内部 |
| `credential_updated_at` | `timestamptz` | 凭据更新时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `credential_version` | `bigint` | 凭据撤销代次 | 是 | 受信服务上下文与服务层校验 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, login_name)`。

控制面用途：`accounts`；账号/角色身份引用不赋予其他渠道数据访问权。

## builtin_roles

内置角色。状态：已实现；归属：渠道；迁移：0002_iam。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `role_code` | `varchar(64)` | 角色编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 角色名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `allowed_actions` | `jsonb` | 允许动作 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `grant_scope` | `varchar(32)` | 授权类别 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, role_code)`。

控制面用途：`roles`；账号/角色身份引用不赋予其他渠道数据访问权。

## channel_memberships

渠道成员关系。状态：已实现；归属：渠道；迁移：0002_iam。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `user_id` | `varchar(64)` | 平台账号标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `roles` | `jsonb` | 角色清单 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `environments` | `jsonb` | 授权环境 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `data_scopes` | `jsonb` | 授权数据域 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `status` | `varchar(32)` | 成员状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `granted_by` | `varchar(128)` | 授权人标识 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, user_id)`。

## resource_grants

资源授权。状态：已实现；归属：渠道；迁移：0002_iam。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `grantee_type` | `varchar(64)` | 受权主体类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `grantee_id` | `varchar(128)` | 受权主体标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `resource_type` | `varchar(64)` | 资源类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `resource_id` | `varchar(64)` | 资源标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `allowed_actions` | `jsonb` | 允许动作 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `environments` | `jsonb` | 授权环境 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `data_scopes` | `jsonb` | 授权数据域 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, grantee_type, grantee_id)`；`(channel_id, resource_type, resource_id)`。

## iam_revocations

认证撤销补偿记录。状态：已实现；归属：渠道；迁移：0002_iam。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `kind` | `varchar(32)` | 撤销索引类别 | 是 | 受信服务上下文与服务层校验 | 内部 |
| `target_id` | `varchar(128)` | 撤销对象标识或令牌摘要 | 是 | 受信服务上下文与服务层校验 | 内部 |
| `cutoff_at` | `timestamptz` | 撤销签发时间上界 | 是 | 受信服务上下文与服务层校验 | 内部 |
| `completed_at` | `timestamptz` | 缓存补偿完成时间 | 否 | 受信服务上下文与服务层校验 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, kind, target_id)`；`(completed_at)`。

## auth_tokens

存储：Redis 认证库；无 PostgreSQL 副本。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `channel_id` | `varchar(64)` | 所属渠道 | 是 | 服务端已验证身份 | 内部 |
| `token_digest` | `varchar(64)` | 随机令牌摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `session_id` | `varchar(64)` | 会话标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `principal_type` | `varchar(32)` | 身份类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `principal_id` | `varchar(128)` | 身份标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `environment` | `varchar(16)` | 绑定环境 | 否 | 服务层校验后的业务输入 | 内部 |
| `client_id` | `varchar(64)` | 接入服务 | 否 | 服务层校验后的业务输入 | 内部 |
| `key_id` | `varchar(64)` | 渠道密钥 | 否 | 服务层校验后的业务输入 | 内部 |
| `issued_at` | `timestamptz` | 签发时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `expires_at` | `timestamptz` | 过期时间 | 是 | 服务层校验后的业务输入 | 内部 |
| `purpose` | `varchar(32)` | 令牌用途 | 是 | 受信服务上下文与服务层校验 | 内部 |
| `credential_version` | `bigint` | 签发时凭据代次 | 否 | 受信服务上下文与服务层校验 | 内部 |
| `membership_version` | `bigint` | 签发时成员修订 | 否 | 受信服务上下文与服务层校验 | 内部 |
| `data_scope_id` | `varchar(64)` | 绑定数据域 | 否 | 受信服务上下文与服务层校验 | 内部 |
| `index_keys` | `jsonb` | 服务端撤销索引键 | 是 | 受信服务上下文与服务层校验 | 内部 |
| `issued_at_ms` | `bigint` | 撤销比较使用的签发毫秒时间 | 是 | 服务端时钟 | 内部 |
