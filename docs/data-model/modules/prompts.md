# 提示词模型

模型版本 2.0.0；负责方案 09；需求 [05-提示词管理.md](../../../../需求文档/05-提示词管理.md)。总索引见 [README](../README.md)。

## prompts

提示词资源。状态：已实现；归属：渠道；归档修订：0009_prompts。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `prompt_code` | `varchar(64)` | 提示词编码 | 是 | 服务层校验后的业务输入 | 内部 |
| `name` | `varchar(128)` | 提示词名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `purpose` | `varchar(512)` | 使用用途 | 是 | 服务层校验后的业务输入 | 内部 |
| `owner` | `varchar(128)` | 负责人标识 | 是 | 服务端当前操作人 | 内部 |
| `status` | `varchar(32)` | 资源状态 | 是 | 服务层校验后的业务输入 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, prompt_code)`。

## prompt_samples

提示词调试样例。状态：已实现；归属：主体；归档修订：0009_prompts。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `prompt_id` | `varchar(64)` | 提示词资源标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `title` | `varchar(128)` | 样例名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `input` | `json` | 样例输入 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `expected_constraints` | `json` | 预期断言 | 是 | 服务层校验后的业务输入 | 敏感内容 |

普通索引：`(channel_id, id)`；`(channel_id, prompt_id)`。

## prompt_tests

提示词调试记录。状态：已实现；归属：主体；归档修订：0009_prompts。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `id` | `varchar(64)` | 记录标识 | 是 | 服务端随机标识 | 内部 |
| `channel_id` | `varchar(64)` | 所属渠道标识 | 是 | 受信服务上下文 | 内部 |
| `created_at` | `datetime(6) UTC` | 创建时间 | 是 | 服务端时钟 | 内部 |
| `updated_at` | `datetime(6) UTC` | 更新时间 | 是 | 服务端时钟 | 内部 |
| `revision` | `bigint` | 并发修订号 | 是 | 服务层递增 | 内部 |
| `environment` | `varchar(16)` | 所属环境 | 是 | 受信服务上下文 | 内部 |
| `subject_type` | `varchar(64)` | 业务主体类型 | 否 | 受信服务上下文 | 内部 |
| `subject_id` | `varchar(128)` | 业务主体编号 | 否 | 受信服务上下文 | 个人 |
| `version_id` | `varchar(64)` | 版本标识 | 是 | 服务层校验后的业务输入 | 内部 |
| `draft_revision` | `bigint` | 草稿修订 | 否 | 服务层校验后的业务输入 | 内部 |
| `release_snapshot_id` | `varchar(64)` | 冻结快照标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `model_route_version` | `varchar(64)` | 模型路由版本 | 是 | 服务层校验后的业务输入 | 内部 |
| `rendered_input_ref` | `varchar(64)` | 渲染内容引用 | 否 | 服务层校验后的业务输入 | 内部 |
| `run_id` | `varchar(64)` | 调试运行标识 | 否 | 服务层校验后的业务输入 | 内部 |
| `frozen_version` | `json` | 调试时固定的提示词版本 | 是 | 受信范围内读取的版本快照 | 敏感内容 |
| `sample_snapshot` | `json` | 调试时固定的样例与预期断言 | 是 | 本范围固定样例 | 敏感内容 |
| `rendered_input` | `json` | 保持来源分区的完整渲染快照 | 是 | 受控变量绑定与有限渲染 | 敏感内容 |
| `descriptor_digest` | `varchar(64)` | 调试执行描述摘要 | 是 | 服务端规范 JSON 摘要 | 内部 |
| `sample_id` | `varchar(64)` | 调试样例标识 | 是 | 服务层验证的样例引用 | 内部 |
| `model_route_name` | `varchar(128)` | 调试时的模型路由名称 | 否 | 受信模型路由资源 | 内部 |
| `status` | `varchar(32)` | 调试受理状态 | 是 | 服务层状态机 | 内部 |

普通索引：`(channel_id, id)`；`(channel_id, version_id)`；`(channel_id, environment, version_id, descriptor_digest)`。

## 版本内容结构：prompt

存于公共 `resource_versions.content`；数组项结构按名称单独列出。父版本、依赖与发布映射只保存一份。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `instruction_blocks` | `json` | 结构化指令分区 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `message_templates` | `json` | 消息模板 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `variables` | `json` | 变量定义清单 | 是 | 服务层校验后的业务输入 | 敏感内容 |
| `change_note` | `longtext` | 变更说明 | 是 | 服务层校验后的业务输入 | 内部 |

## 版本内容结构：variable_item

存于公共 `resource_versions.content`；数组项结构按名称单独列出。父版本、依赖与发布映射只保存一份。

| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `name` | `varchar(128)` | 模板引用名 | 是 | 服务层校验后的业务输入 | 内部 |
| `display_name` | `varchar(128)` | 中文字段名称 | 是 | 服务层校验后的业务输入 | 内部 |
| `type` | `varchar(32)` | 变量类型 | 是 | 服务层校验后的业务输入 | 内部 |
| `required` | `boolean` | 业务必填 | 是 | 服务层校验后的业务输入 | 内部 |
| `default` | `json` | 显式默认值 | 否 | 服务层校验后的业务输入 | 敏感内容 |
| `max_length` | `integer` | 长度上限 | 否 | 服务层校验后的业务输入 | 内部 |
| `source` | `varchar(32)` | 允许的注入来源 | 是 | 服务层校验后的业务输入 | 内部 |
| `sensitivity` | `varchar(32)` | 敏感级别 | 是 | 服务层校验后的业务输入 | 内部 |
