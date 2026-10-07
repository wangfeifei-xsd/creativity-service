# API 与 MCP 接入

业务后端通过统一 API 调用已发布 Agent；业务数据与工具由 MCP 服务提供。可直接复用 [后端客户端](../examples/backend/README.md) 或 [SDK](../sdks/README.md)，接口字段见 [业务 OpenAPI](../contracts/backend/openapi-v1.json)。

<a id="unified-api"></a>

## 接入流程

1. 配置渠道、环境和接入服务，签发 API Key 与独立委托密钥。
2. 配置 MCP 连接、专用当前主体复核工具及已发布业务工具。
3. 配置模型、Skills 和 Agent，调试并发布到目标环境。
4. 后端换取 Token，从自己的登录与权限服务读取当前主体，签署每次请求的委托。
5. 使用已保存的业务幂等键创建运行，保存 `run_id`，通过查询或 SSE 获取结果。

管理页面分步开通：填写渠道基本信息与首位管理员，配置环境，即可按角色授权进入渠道。POST /admin/v1/channels 可省略 environment，也可携带初始环境。创建环境同步整渠道管理员，初始化内容恢复屏障；随后按需配置接入服务与 Key。

创建运行需要 `run:create` 及相关资源授权；查询结果需要 `run:read`、`run:content`、`data:read_sensitive`。API Key、委托密钥和服务 Token 留在业务后端。

| 操作 | 接口与行为 |
| --- | --- |
| 换 Token | `POST /api/v1/auth/token`，正文为 `api_key` |
| 创建运行 | `POST /api/v1/runs`，正文为 `agent_code/input/delivery`，携带 `Idempotency-Key` |
| 同步等待 | `delivery=sync` 最多等待 15 秒；超时返回原运行的 202 回执 |
| 异步或流式 | `delivery=async/stream` 返回 202 及状态、事件地址 |
| 查询与取消 | `GET /api/v1/runs/{run_id}`；`POST /api/v1/runs/{run_id}/cancel` |
| 事件 | `GET /api/v1/runs/{run_id}/events`，重连与帧格式见 [SSE](runtime.md#runtime-sse) |
| 会话与文件 | `/api/v1/conversations`；下载使用返回的受控产物地址 |

HTTP 200 表示成功取得快照，仍需检查 `state` 和 `result.business_status`。最终 `result.data` 遵循 Agent 输出 schema，中间文本不作为正式结果。

<a id="integrations"></a>

## 身份委托与重试

委托采用 `business-delegation-v2`，载荷只包含主体、动作、资源和请求绑定；渠道与环境来自服务 Token，可选声明仅用于一致性校验。旧 v1 或携带已撤销范围字段的声明拒绝。

除换 Token 外，业务请求使用 `Authorization: Bearer ...` 和 `X-Business-Delegation`。委托绑定实际方法、路径、原始查询串、正文 SHA-256 及适用幂等键；每个新请求生成新 nonce。签名实现见 [后端客户端](../examples/backend/client.py)，跨语言向量见 [签名样例](../contracts/integrations/delegation-vectors.json)。

Token 到期后重新交换并签名查询原运行。创建响应丢失时保留原幂等键和语义输入，同键异内容返回 409。Key 轮换不产生新的逻辑运行；原 Key 失效会影响在途任务后续授权。会话消息用 `client_message_id` 去重；创建会话、上传等操作需按各自契约处理响应不明。

401 可重新认证；403 停止访问；409/422 修正冲突或输入；429/503 有界重试，已受理请求继续查询原运行。排障保存请求标识、运行、步骤、尝试与来源请求标识，错误分类见 [错误契约](../contracts/integrations/errors.json)。

<a id="mcp"></a>
<a id="tools"></a>
<a id="mcp-business"></a>

## MCP 工具与主体复核

API 和 Worker 配置一致的 `CREATIVITY_MCP_DESTINATIONS`、`CREATIVITY_MCP_KEY_VERSION`、`CREATIVITY_MCP_ENCRYPTION_KEYS`。创建连接、更新服务凭据、发现远端工具，导入业务工具草稿并测试发布。Agent 绑定具体工具版本，远端 schema 或连接修订变化后重新验证发布。

连接测试和工具发现仅由管理端手动触发，不安排后台周期检查。历史检查间隔与调度字段仅为已有数据兼容保留，不再参与调度；升级前已排队的 `mcp.sweep` 消息直接完成，不访问远端或生成记录。

源端另提供 `_meta["creativity/purpose"]="subject_review"` 的只读身份工具，在管理端 `/subject-review-bindings` 绑定渠道、环境、接入服务和发现快照。身份工具不能导入模型工具目录；缺配置、主体停用、范围扩大、过期或超时均拒绝执行。

业务 MCP 使用连接专属凭据。受信身份放在 `_meta["creativity.identity"]`，业务数据放在 `structuredContent`，来源、范围、完整性与真实影响放在 `_meta["creativity.result"]`。对应 [身份](../contracts/mcp/McpIdentity.schema.json)、[主体复核](../contracts/mcp/SubjectReviewResponse.schema.json) 和 [结果元数据](../contracts/mcp/McpResultMetadata.schema.json) 契约；配置示例见 [MCP 示例](../examples/mcp/README.md)。

工具返回的无数据、缺失、部分结果及远端错误分别保留；有分页或截断时声明覆盖范围。业务写工具按额外的受控执行配置启用，默认只读接入流程不授权写入。

<a id="business-independence"></a>
<a id="access-decoupling"></a>

## 新业务与旧连接

新业务配置 MCP、Skills 和 Agent，示例见 [接入演示](../examples/onboarding/README.md)。旧 HTTP integration 与旧 Agent 入口保留兼容；迁移时新建 MCP 连接、工具版本和 Agent 草稿，完成调试评测后切换发布映射。旧版本及在途快照保留。
