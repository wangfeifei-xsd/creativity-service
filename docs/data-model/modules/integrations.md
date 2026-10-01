# 业务接入与身份委托模型

模型版本 1.2.0；负责方案 18/19/20；需求 [14-业务接入与适配.md](../../../../需求文档/14-业务接入与适配.md)。总索引见 [README](../README.md)。

## integrations

业务系统适配连接。状态：设计基线；归属：环境；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `name` | `varchar(128)` | 接入名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `adapter_version` | `varchar(64)` | 适配器版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `business_endpoint` | `varchar(2048)` | 源服务地址 | 是 | 服务层校验后的业务输入 | 内部 |
| `credential_ref` | `varchar(64)` | 服务凭据 | 是 | 服务层校验后的业务输入 | 内部 |
| `allowed_operations` | `jsonb` | 已授权操作 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `scope_mapping_ref` | `varchar(64)` | 域映射引用 | 是 | 服务层校验后的业务输入 | 内部 |
| `health` | `varchar(32)` | 健康状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `contract_version` | `varchar(64)` | 标准契约版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 启用状态 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, status)`。

## delegation_keys

委托签名验证密钥。状态：设计基线；归属：环境；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `client_id` | `varchar(64)` | 调用服务 | 是 | 服务层校验后的业务输入 | 内部 |
| `key_reference` | `varchar(64)` | 密钥或公钥引用 | 是 | 服务层校验后的业务输入 | 内部 |
| `algorithm` | `varchar(32)` | 固定算法 | 是 | 服务层校验后的业务输入 | 内部 |
| `audience` | `varchar(128)` | 受众 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 密钥状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `expires_at` | `timestamptz` | 到期时间 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, client_id)`。

## integration_tests

接入契约验证。状态：设计基线；归属：环境；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `integration_id` | `varchar(64)` | 连接标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `config_revision` | `bigint` | 配置修订 | 是 | 服务层校验后的业务输入 | 内部 |
| `cases` | `jsonb` | 验证用例 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `results` | `jsonb` | 脱敏验证结果 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `capabilities` | `jsonb` | 已验证能力 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, integration_id)`。
