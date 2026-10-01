# 智能体定义模型

模型版本 1.2.0；负责方案 16；需求 [06-Agent与流程管理.md](../../../../需求文档/06-Agent与流程管理.md)。总索引见 [README](../README.md)。

## agents

智能体资源。状态：设计基线；归属：渠道；迁移：由所属方案新增。

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

## 版本内容结构：agent

存于公共 `resource_versions.content`；数组项结构按名称单独列出。父版本、依赖与发布映射只保存一份。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `input_schema` | `jsonb` | 输入结构 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `output_schema` | `jsonb` | 输出结构 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `entrypoint` | `varchar(128)` | 固定执行入口 | 是 | 服务层校验后的业务输入 | 内部 |
| `workflow_type` | `varchar(64)` | 流程类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `steps` | `jsonb` | 节点定义 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `edges` | `jsonb` | 流转边 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `conditions` | `jsonb` | 有限条件表达式 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `loop_limits` | `jsonb` | 循环与调用上限 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `failure_policy` | `jsonb` | 失败策略 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `deadline_seconds` | `integer` | 运行限时秒数 | 是 | 服务层校验后的业务输入 | 内部 |
| `token_limit` | `bigint` | 数量上限 | 是 | 服务层校验后的业务输入 | 内部 |
| `cost_limit` | `numeric(24,8)` | 费用上限 | 否 | 服务层校验后的业务输入 | 内部 |
| `max_model_rounds` | `integer` | 模型轮数上限 | 是 | 服务层校验后的业务输入 | 内部 |
| `max_tool_calls` | `integer` | 工具次数上限 | 是 | 服务层校验后的业务输入 | 内部 |
| `conversation_enabled` | `boolean` | 会话开关 | 是 | 服务层校验后的业务输入 | 内部 |
| `memory_policy` | `jsonb` | 记忆策略版本引用 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `context_limit` | `integer` | 上下文上限 | 是 | 服务层校验后的业务输入 | 内部 |
| `summary_policy` | `jsonb` | 摘要策略 | 是 | 服务层校验后的业务输入 | 敏感内容 |
