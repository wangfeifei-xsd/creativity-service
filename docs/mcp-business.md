# 20 MCP 业务工具配置接入

本单元提供通用连接、发现、固定版本、主体复核和结果包裹。业务 MCP Server 维护数据与领域权限，平台不注册新的业务 Python 适配器。工具目录以 MCP 发现和本地工具版本为准，旧 `/admin/v1/integrations` 仅保留兼容。开发规则见 [rule.md](../../rule.md)。

## 配置顺序

1. 按 [渠道配置](channels.md) 创建渠道、环境、显式数据域映射及接入服务。接入 Key 与 HMAC 委托密钥独立。创建运行需要 `run:create`，查询结果和 SSE 还需要 `run:read`、`run:content`、`data:read_sensitive`；这些权限必须同时落在服务、Key、主体委托和源端当前权限内。
2. 为 API 和 Worker 配置相同的 `CREATIVITY_MCP_DESTINATIONS`、`CREATIVITY_MCP_KEY_VERSION`、`CREATIVITY_MCP_ENCRYPTION_KEYS`。地址策略显式指定渠道、环境、主机、协议、端口及允许网段。生产使用 HTTPS；本机测试 TCP 端点需显式允许 `127.0.0.1/32`。HMAC 委托主密钥沿用 `CREATIVITY_BUSINESS_*`，不要与 MCP 服务令牌混用。
3. 管理工作区选择本渠道、环境及数据域，创建 MCP 连接，使用“更新凭据”存储服务令牌，再执行发现并启用连接。凭据只持久化加密引用。
4. 源端提供专用身份工具，其 `Tool._meta["creativity/purpose"]` 为 `subject_review`，声明 `readOnlyHint: true`，输入/输出遵守下述固定协议。该工具在发现页可见，但不能导入模型工具目录。
5. 在“业务接入 → 当前主体复核”绑定接入服务、MCP 连接、发现快照及身份工具。每个渠道/环境/数据域/接入服务至多一条配置；保存固定连接修订与 schema 摘要。管理接口为 `GET/POST /admin/v1/subject-review-bindings`，客户端选项为 `/subject-review-bindings/options`。管理权限需要 `integration:manage` 和 `mcp:manage`。
6. 在 MCP 发现页选择业务工具，设置显示名称、权限、真实影响、输出 schema、超时及体积限制，导入草稿，测试、冻结、发布。Agent 固定绑定发布版本，不能以发现替代授权。需要业务主体的 MCP 工具必须配置服务凭据并返回结构化结果元数据。
7. 业务后端沿用 [18 委托协议](integrations.md)，通过 `/api/v1/runs` 调用 Agent。sync、stream 和异步 Worker 经过同一运行与工具执行器。新业务只替换 MCP 地址、工具 schema、Skills/Agent 配置与身份配置。

保存复核绑定的正文示例；渠道、环境与数据域均由管理 Token 决定，不在此正文中传入：

```json
{
  "client_id": "client_from_channel",
  "connection_id": "mcp_from_discovery",
  "discovery_id": "discovery_reviewed",
  "remote_tool_name": "access.review-current",
  "timeout_seconds": 5,
  "enabled": true
}
```

编辑必须携带当前 `revision`。停用配置后，已有任务在下一次边界检查时拒绝继续；无配置不会退回旧八类 HTTP operation。

## 服务认证与受信身份

服务间认证使用连接专属 `Authorization: Bearer <服务凭据>`。调用端的请求 Token、HMAC 密钥和原始委托字符串均不发送给 MCP。远端只能在服务认证通过后采信 `_meta["creativity.identity"]`，随后按自己的当前主体权限再次授权实体及动作。

身份契约为 [McpIdentity](../contracts/mcp/McpIdentity.schema.json)，版本 `creativity.mcp-identity.v1`。包含已验证的 `channel_id/environment/data_scope_id/subject_type/subject_id`、平台服务标识、收窄后的 actions/resources、`request_id/run_id/attempt_id`、观测时间与有效期。工具资源限定为当前调用，业务资源范围保留源端复核后的子集。管理调试没有业务主体时不会编造用户身份；要求主体的工具必须通过实际业务委托调用。

保留身份字段、认证头、目的 URL、SQL 与命令均不进入模型参数。嵌套参数也会拒绝这些覆盖。远端名称保持原值，由连接内映射生成本地工具编码，工具调用不受旧八类 operation 限制。

## 当前主体复核

实现选择专用 MCP 身份工具方案；该工具本身可以在源端调用其固定身份协议。平台不提供自由 URL 的身份查询入口。

输入是 [SubjectReviewRequest](../contracts/mcp/SubjectReviewRequest.schema.json)：`creativity.subject-review.v1`、完整已验证 scope、经签名且映射成功的 source_scope、client/key/request 标识、原委托 actions/resources 上限。输入由服务端产生，直接使用固定 MCP 连接，不调用业务工具执行器，也不递归依赖待复核主体。

响应是 [SubjectReviewResponse](../contracts/mcp/SubjectReviewResponse.schema.json)：同协议和 scope、`active`、当前 actions、agent_actions、resources、observed_at、expires_at。源端按自己的用户状态查询，不能仅回显请求上限。平台要求观测时间不早于 30 秒前、不晚于允许的 5 秒时钟偏差，复核有效期至多 60 秒。动作和资源只能收窄，扩大、换主体、换数据域、停用、过期、格式错误和超时全部拒绝。平台不跨调用缓存复核结果。

API 验签后立即复核；Worker 使用持久化身份来源，每次后续边界重新检查当前权限。请求 Token 或短期声明自然到期不会取消已合法受理的任务；渠道、环境、数据域、服务 Key、主体、委托密钥、身份连接或服务凭据失效仍会阻断。网络等待前后重新检查配置、密钥及范围，事务中不等待远端。

首次成功复核的新主体在数据域恢复屏障就绪、且没有任何历史主体内容时建立自己的内容屏障。已有历史内容而缺少屏障，或处于恢复状态时仍拒绝，不能通过重新委托重置删除/恢复状态。

## 结构化结果与证据

业务数据原样置于 MCP `structuredContent`，并满足远端 outputSchema 与本地固定 output_schema。元数据放在 `CallToolResult._meta["creativity.result"]`，schema 为 [McpResultMetadata](../contracts/mcp/McpResultMetadata.schema.json)。

```json
{
  "protocol": "creativity.tool-result.v1",
  "scope": {
    "channel_id": "channel_example", "environment": "test", "data_scope_id": "scope_example",
    "subject_type": "member", "subject_id": "reader-a"
  },
  "source_request_id": "source-request-example",
  "source_version": "source-data-v1",
  "observed_at": "2026-10-02T12:00:00Z",
  "actual_effect": "READ_ONLY",
  "result_status": "partial",
  "has_more": true,
  "cursor": "next-page",
  "coverage": {"returned_count": 1},
  "warnings": ["仅返回本页"],
  "evidence": []
}
```

上述时间仅为契约示意；实际响应应填写真实观测时间。结果包裹不猜测领域字段，单位、分页含义、缺失值与时区语义由业务 MCP/Skill 契约定义。

| 远端结果 | ToolResult / 失败行为 |
| --- | --- |
| complete | `data` 保持结构，`coverage.result_status=complete` |
| empty | 明确无记录，保留合法的空结构与 `empty` 标记 |
| missing | 结构允许的缺失值和源端说明原样保留，标记 `missing` |
| partial | 必须声明覆盖范围；有下一页须带 cursor，截断须带原始/返回数量 |
| isError | 按 error_category 保留 forbidden、timeout、unavailable、invalid、remote 的不同错误，绝不转换成空结果 |
| 无结构的旧公开工具 | 仅非主体工具允许原始文本包裹；来源时间明确标记 `platform_receipt` |

source_request_id、source_version、observed_at 被提升至 ToolResult。源证据必须含相同 scope、来源编号、版本、观测时间、可读标题及字段位置；平台将其保存在 `coverage.source_evidence` 并生成关联此次 tool_call 的平台证据，不允许源端伪造已存证据 ID。平台文件引用继续经过已有文件授权。媒体/资源链接、超大结果、无效 schema、范围错配、冲突完整性与真实副作用不符均拒绝。

只读声明同时检查远端注解、本地授权和结果的 actual_effect。平台无法从协议证明远端未声明的内部行为；源服务必须如实提供契约。P0 不执行业务写工具。

## 固定版本和故障处理

连接地址/凭据/配置修订变化后，重新发现、导入新工具版本、重新授权发布并更新 Agent 依赖。旧工具绑定始终保留导入时的连接修订，不会因为新连接测试通过就恢复为新绑定。Agent 发布和实际调用使用同一绑定检查。每次业务提交前重新发现所需 schema；参数、输出或影响注解变化均会阻断，已发现的快照不覆盖。

本地 JSON Schema 支持对象、数组、组合、`$defs` 与本地 JSON Pointer `$ref`；不联网解析外部引用，不允许动态引用或重新定义 schema 地址。保留 64 KiB 契约及嵌套限制。工具测试页支持字段表单及 JSON 参数。

| 故障 | 排查位置 |
| --- | --- |
| SUBJECT_REVIEW_REQUIRED | 当前工作区、接入服务和复核绑定是否匹配且启用 |
| SUBJECT_REVIEW_CHANGED / MCP_TOOL_CHANGED | 比较发现快照、连接修订与已发布固定版本，重新授权 |
| SUBJECT_REVIEW_DENIED / DELEGATION_FORBIDDEN | 源端主体状态、范围和权限是否仍为原委托子集 |
| SUBJECT_REVIEW_UNAVAILABLE / MCP_AUTH_FAILED | 连接检查、令牌轮换、地址许可和超时；不回显完整凭据 |
| MCP_REMOTE_FORBIDDEN / MCP_REMOTE_TIMEOUT | 按 request/run/attempt 与源请求编号关联远端记录 |
| TOOL_RESULT_INVALID / TOOL_EFFECT_MISMATCH | 核对 schema、结果范围、证据、完整性及实际副作用 |

## 后续交接

21 绑定本渠道固定 tool_version 与 Skills；22 使用当前主体协议调用统一 API；23 可直接复用 [两套受控 MCP 服务](../examples/mcp/README.md) 验证新工具名称与数据结构。测试服务与模型替身证据见 [验证记录](mcp-business-validation.md)。正式业务 MCP 开发、部署和真实模型供应商验收不属于本单元的受控测试证明。
