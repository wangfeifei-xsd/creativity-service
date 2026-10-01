# 模型配置模型

模型版本 1.2.0；负责方案 07；需求 [03-模型配置.md](../../../../需求文档/03-模型配置.md)。总索引见 [README](../README.md)。

## provider_catalog

供应商字典。状态：设计基线；归属：渠道；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `code` | `varchar(64)` | 供应商编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 供应商名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `protocols` | `jsonb` | 支持协议族 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `template_content` | `jsonb` | 无凭据连接模板 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, code)`。

控制面用途：`catalog`；账号/角色身份引用不赋予其他渠道数据访问权。

## model_connections

模型供应商连接。状态：设计基线；归属：环境；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `name` | `varchar(128)` | 连接名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `protocol` | `varchar(64)` | 协议类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `endpoint` | `varchar(2048)` | 服务地址 | 是 | 服务层校验后的业务输入 | 内部 |
| `credential_ref` | `varchar(64)` | 凭据标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `timeout_seconds` | `integer` | 调用超时秒数 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 连接启用状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `health_status` | `varchar(32)` | 最近健康状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `provider_id` | `varchar(64)` | 供应商字典引用 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, environment, status)`。

## models

模型映射。状态：设计基线；归属：渠道；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `model_code` | `varchar(64)` | 稳定模型编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 模型名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `connection_id` | `varchar(64)` | 连接标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `provider_model_name` | `varchar(256)` | 供应商模型名 | 是 | 服务层校验后的业务输入 | 内部 |
| `context_limit` | `integer` | 上下文上限 | 否 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 启用状态 | 是 | 服务层校验后的业务输入 | 内部 |
| `capabilities` | `jsonb` | 按能力记录支持及验证状态 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `verified_at` | `timestamptz` | 验证时间 | 否 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, model_code)`。

## model_routes

模型路由资源。状态：设计基线；归属：渠道；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `code` | `varchar(64)` | 路由编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 路由名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `status` | `varchar(32)` | 启用状态 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, code)`。

## model_tests

模型验证记录。状态：设计基线；归属：环境；迁移：由所属方案新增。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `timestamptz` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `timestamptz` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `model_id` | `varchar(64)` | 模型标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `config_revision` | `bigint` | 配置修订号 | 是 | 服务层校验后的业务输入 | 内部 |
| `cases` | `jsonb` | 验证用例 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `results` | `jsonb` | 验证结果 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `latency_ms` | `integer` | 耗时毫秒 | 否 | 服务层校验后的业务输入 | 内部 |
| `attempt_ids` | `jsonb` | 实际尝试引用 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, model_id, created_at)`。

## 版本内容结构：model_route

存于公共 `resource_versions.content`；数组项结构按名称单独列出。父版本、依赖与发布映射只保存一份。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `primary_model` | `varchar(64)` | 首选模型具体版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `fallback_models` | `jsonb` | 有序回退模型版本 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `retry_policy` | `jsonb` | 重试及总次数限制 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `required_capabilities` | `jsonb` | 必要能力 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `parameter_policy` | `jsonb` | 协议参数策略 | 是 | 服务层校验后的业务输入 | 敏感内容 |

## 版本内容结构：model

存于公共 `resource_versions.content`；数组项结构按名称单独列出。父版本、依赖与发布映射只保存一份。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `connection_revision` | `bigint` | 连接修订 | 是 | 服务层校验后的业务输入 | 内部 |
| `provider_model_name` | `varchar(256)` | 供应商名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `capabilities` | `jsonb` | 具体协议验证结果 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `parameters` | `jsonb` | 已校验参数 | 是 | 服务层校验后的业务输入 | 敏感内容 |
