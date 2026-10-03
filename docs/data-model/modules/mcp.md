# 远程工具连接模型

模型版本 1.6.0；负责方案 14；需求 [09-MCP配置.md](../../../../需求文档/09-MCP配置.md)。总索引见 [README](../README.md)。

## mcp_connections

远程工具连接。状态：已实现；归属：环境；迁移：0014_mcp。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `name` | `varchar(128)` | 连接名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `transport` | `varchar(64)` | 传输类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `endpoint` | `varchar(2048)` | 服务地址 | 是 | 服务层校验后的业务输入 | 内部 |
| `credential_ref` | `varchar(64)` | 凭据引用 | 否 | 服务层校验后的业务输入 | 内部 |
| `timeouts` | `jsonb` | 超时策略 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `status` | `varchar(32)` | 启用状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `health_status` | `varchar(32)` | 健康状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `configuration_revision` | `bigint` | 连接配置修订 | 是 | 服务层校验后的业务输入 | 内部 |
| `credential_revision` | `bigint` | 凭据版本 | 否 | 服务层校验后的业务输入 | 内部 |
| `tested_revision` | `bigint` | 握手通过的配置修订 | 否 | 服务层校验后的业务输入 | 内部 |
| `discovered_revision` | `bigint` | 发现通过的配置修订 | 否 | 服务层校验后的业务输入 | 内部 |
| `failure_count` | `integer` | 连续失败次数 | 是 | 服务层校验后的业务输入 | 内部 |
| `health_policy` | `jsonb` | 检查频率与失败阈值 | 是 | 服务层校验后的业务输入 | 内部 |
| `last_check_at` | `timestamptz` | 最近检查时间 | 否 | 服务层校验后的业务输入 | 内部 |
| `auth_failed` | `boolean` | 凭据失效阻断状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `health_actor_id` | `varchar(64)` | 健康检查授权成员 | 是 | 受信服务上下文 | 内部 |
| `health_data_scope_id` | `varchar(64)` | 健康检查授权数据域 | 是 | 受信服务上下文 | 内部 |
| `next_check_at` | `timestamptz` | 下次健康检查时间 | 是 | 受信服务上下文 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, status)`；`(channel_id, environment, next_check_at)`。

## mcp_checks

握手与健康检查。状态：已实现；归属：环境；迁移：0014_mcp。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `connection_id` | `varchar(64)` | 连接标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `negotiated_version` | `varchar(64)` | 协商协议版本 | 否 | 服务层校验后的业务输入 | 内部 |
| `server_info` | `jsonb` | 远端信息 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `capabilities` | `jsonb` | 协商能力 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `health_status` | `varchar(32)` | 健康状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `latency_ms` | `integer` | 耗时毫秒 | 否 | 服务层校验后的业务输入 | 内部 |
| `error_category` | `varchar(64)` | 错误类别 | 否 | 服务层校验后的业务输入 | 内部 |
| `connection_revision` | `bigint` | 检查时连接配置修订 | 是 | 服务层校验后的业务输入 | 内部 |
| `operation` | `varchar(32)` | 检查操作类型 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, connection_id, created_at)`。

## mcp_discoveries

远端工具发现快照。状态：已实现；归属：环境；迁移：0014_mcp。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `connection_id` | `varchar(64)` | 连接标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `connection_revision` | `bigint` | 连接修订 | 是 | 服务层校验后的业务输入 | 内部 |
| `tool_definitions` | `jsonb` | 远端工具定义 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `schema_hashes` | `jsonb` | 定义摘要 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `negotiated_version` | `varchar(64)` | 发现协商协议版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `credential_revision` | `bigint` | 发现时凭据版本 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, connection_id, created_at)`。

## mcp_imports

远端工具导入映射。状态：已实现；归属：环境；迁移：0014_mcp。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `connection_id` | `varchar(64)` | 连接标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `discovery_id` | `varchar(64)` | 发现快照 | 是 | 服务层校验后的业务输入 | 内部 |
| `remote_tool_name` | `varchar(256)` | 远端名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `local_tool_id` | `varchar(64)` | 本地工具标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `schema_hash` | `varchar(64)` | 远端结构摘要 | 是 | 服务层校验后的业务输入 | 内部 |
| `imported_version` | `varchar(64)` | 本地导入版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `effect_type` | `varchar(32)` | 管理员核定影响类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 本地工具显示名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `input_schema` | `jsonb` | 固定本地输入契约 | 是 | 服务层校验后的业务输入 | 内部 |
| `contract_status` | `varchar(32)` | 固定契约可用状态 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, connection_id, remote_tool_name)`；`(channel_id, environment, connection_id, discovery_id, remote_tool_name)`。
