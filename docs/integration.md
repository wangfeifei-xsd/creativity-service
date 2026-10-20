# API 与 MCP 接入

业务后端通过统一 API 调用已上线且启用的 Agent；业务数据与工具由 MCP 服务提供。可直接复用 [后端客户端](../examples/backend/README.md) 或 [SDK](../sdks/README.md)，接口字段见 [业务 OpenAPI](../contracts/backend/openapi-v1.json)。

<a id="unified-api"></a>

## 接入流程

1. 开通渠道与环境，在“业务接入 → 渠道 Key 与密钥”创建渠道凭据。
2. 按智能体需要配置模型、Skills 和 MCP 业务工具，保存、校验并上线 Agent。
3. 业务后端用渠道 Key 和密钥换取 Token。
4. 需要按用户隔离会话时，后端从自己的登录会话取得用户类型和编号，通过可选主体头传递。
5. 使用已保存的业务幂等键创建运行，保存 `run_id`，通过查询或 SSE 获取结果。

管理页面分步开通：填写渠道基本信息与首位管理员，配置环境，即可按角色授权进入渠道。POST /admin/v1/channels 可省略 environment，也可携带初始环境。创建环境同步整渠道管理员，初始化内容恢复屏障；随后创建渠道 Key 与密钥，平台自动维护内部接入身份；无需单独登记接入服务。渠道列表的“Key 与密钥”也可进入同一管理页，支持创建、轮换、吊销及查看有效期和最近使用时间。

新建渠道凭据自动具备渠道业务 API 的调用能力，不需要逐项配置动作或模型、提示词、技能、工具资源名单。历史受限凭据继续保留原动作范围。平台仍校验渠道、环境、凭据有效性、资源状态和会话归属。密钥和服务 Token 留在业务后端。

| 操作 | 接口与行为 |
| --- | --- |
| 换 Token | `POST /api/v1/auth/token`，正文为 `{"key_id":"渠道 Key","secret":"渠道密钥"}`；旧 `api_key` 单字段仅兼容既有调用，不可与新字段混用 |
| 创建运行 | `POST /api/v1/runs`，正文为 `agent_code/input/delivery`，携带 `Idempotency-Key` |
| 同步等待 | `delivery=sync` 最多等待 15 秒；超时返回原运行的 202 回执 |
| 异步或流式 | `delivery=async/stream` 返回 202 及状态、事件地址 |
| 查询与取消 | `GET /api/v1/runs/{run_id}`；`POST /api/v1/runs/{run_id}/cancel` |
| 事件 | `GET /api/v1/runs/{run_id}/events`，重连与帧格式见 [SSE](runtime.md#runtime-sse) |
| 会话与文件 | `/api/v1/conversations`；下载使用返回的受控产物地址 |

HTTP 200 表示成功取得快照，仍需检查 `state` 和 `result.business_status`。最终 `result.data` 遵循 Agent 输出 schema，中间文本不作为正式结果。

<a id="integrations"></a>

## 渠道身份与重试

渠道与环境始终来自通过 Key 和密钥认证的服务 Token。请求正文、请求头或模型不能另选渠道与环境。渠道 Key 不是最终业务用户的登录凭据；业务后端负责验证用户登录与业务权限。

除换 Token 外，业务请求使用 `Authorization: Bearer ...`。需要用户内容隔离时成对传递 `X-Subject-Type` 和 `X-Subject-ID`；不传则归属当前渠道内部服务身份。用户标识由持有渠道凭据的业务后端提供，不能透传浏览器自填值。它们只决定会话、运行和记忆的归属，不包含动作或资源授权声明。无需身份委托密钥、签名或 MCP 主体复核配置。

Token 到期后重新交换 Token 并查询原运行。创建响应丢失时保留原幂等键和语义输入，同键异内容返回 409。Key 轮换不产生新的逻辑运行；原 Key 失效会影响在途任务后续授权。会话消息用 `client_message_id` 去重；创建会话、上传等操作需按各自契约处理响应不明。

401 可重新认证；403 停止访问；409/422 修正冲突或输入；429/503 有界重试，已受理请求继续查询原运行。排障保存请求标识、运行、步骤、尝试与来源请求标识；具体响应见业务 OpenAPI。

<a id="mcp"></a>
<a id="tools"></a>
<a id="mcp-business"></a>

## MCP 业务工具

具有本渠道连接管理权限的成员可直接保存 HTTP 或 HTTPS 的 MCP 地址与鉴权地址，无需追加环境白名单，保存不依赖 DNS 或网络可用性；内网、本机及代理虚拟地址均可使用，统一行为见 [出站地址策略](../../技术方案.md#outbound)。`CREATIVITY_MCP_DESTINATIONS` 仅保留历史配置读取兼容，不再限制目的地。MCP 鉴权不需要加密主密钥。创建连接、配置鉴权、发现远端工具，导入业务工具草稿并测试发布。Agent 绑定具体工具版本，远端 schema 或连接修订变化后重新验证发布。

更新凭据后，先重新测试、发现并启用连接，再在远程工具的导入窗口选择原有工具执行“重新绑定”。契约与影响类型相同时，仅更新发现绑定，保留原工具的权限、预授权、输出校验和执行策略；当前配置转为未发布，重新发布后才能恢复 Agent 调用。契约发生变化时拒绝覆盖，须创建独立工具并重新配置 Agent。重复提交同一发现的导入保持幂等。

MCP 的 appSecret、静态 Token、OAuth 令牌和 PKCE verifier 直接保存在同渠道、同环境的 `credentials.secret_value`，无需配置解密主密钥。连接通过凭据引用读取，管理响应与日志不回显原文。初始化归档可直接携带配置凭据；不同环境修改连接地址与凭据后，手动测试、发现并启用。短期 Token 和 OAuth 临时授权不作为初始配置归档。

服务间鉴权使用 `POST /admin/v1/mcp-connections/{id}/authentication`，提交当前 revision、token_endpoint、app_id 和 app_secret。密钥原文存入独立凭据记录，连接仅保存鉴权方式、地址与应用标识；同一应用可省略密钥以保留现值。平台以 client_credentials 表单交换短期 Token，缓存按渠道、环境、连接与凭据修订隔离，到期前续取。HTTP 与 HTTPS 令牌端点均可直接配置。业务收到无效 Token 时清理缓存，不自动重放该业务，下一次调用重新交换。传输选择 streamable_http，独立于 oauth 用户授权码模式。

`streamable_http` 支持完整 JSON 和 SSE 响应，源服务可以按场景选择，无需增加旧版 `/sse` 连接。租号服务基础连接正文见 [配置示例](../examples/mcp/gamerental.connection.json)，工具目录以当前源端发现结果为准。

连接测试和工具发现仅由管理端手动触发，不安排后台周期检查。历史检查间隔与调度字段仅为已有数据兼容保留，不再参与调度；升级前已排队的 `mcp.sweep` 消息直接完成，不访问远端或生成记录。

MCP 是标准的能力协商、工具发现和调用协议，也支持资源、提示词及授权。本项目使用它连接智能体与业务工具；业务后端调用 Creativity API 的渠道认证与 MCP 连接鉴权各自独立。无需为渠道 API 单独提供主体复核工具。源工具按自己的规则检查租户、数据范围和业务条件。

业务 MCP 使用连接专属凭据。受信身份放在 `_meta["creativity.identity"]`，业务数据放在 `structuredContent`，来源、范围、完整性与真实影响放在 `_meta["creativity.result"]`。对应 [身份](../contracts/mcp/McpIdentity.schema.json) 和 [结果元数据](../contracts/mcp/McpResultMetadata.schema.json) 契约；配置示例见 [MCP 示例](../examples/mcp/README.md)。

工具返回的无数据、缺失、部分结果及远端错误分别保留；有分页或截断时声明覆盖范围。业务写工具按额外的受控执行配置启用，默认只读接入流程不授权写入。

<a id="business-independence"></a>
<a id="access-decoupling"></a>

## 新业务与旧连接

新业务配置 MCP、Skills 和 Agent，示例见 [接入演示](../examples/onboarding/README.md)。固定业务 HTTP 接入及凭据管理已移除，业务工具统一通过 MCP 发现和导入。业务接入页面管理渠道 Key 与密钥及运行与事件。智能体只有一份当前配置，使用上下线、启停用控制；历史运行保存执行时的快照。

定时计划在“运行与事件 → 定时运行”按计划设置连续失败停止次数（1–100，默认 3）。受理失败、执行失败和超时按触发顺序累计，成功清零；达到阈值自动暂停，手动启用后重新计数且不补跑旧窗口。调度结果游标与失败次数由同一计划事务维护，升级 `0051_schedule_failures` 从当前时间开始统计。

## PC 服务匹配卡片

销售推荐助手 `service_match` 的 PC 契约为 `schema_version="2.0"`：

```json
{"business_status":"COMPLETED","schema_version":"2.0","data":{"questions":[],"recommendations":[{"product_id":"901","sku_id":"902"}]},"warnings":[],"evidence_refs":[]}
```

推荐只含本次完整可售目录中的商品与规格 ID 字符串，最多一项，SKU 必须属于该商品；不返回名称、图片、价格、规格正文或目录。业务后端复核可售状态，PC 再按 ID 读取权威商品详情展示卡片。`COMPLETED` 恰好一项推荐且无问题；`NEEDS_INPUT` 无推荐且有 1–3 个问题；`NO_MATCH/INSUFFICIENT_DATA` 两数组均为空。问题最多 300 字，warnings 最多三条且每条 200 字，evidence_refs 固定为空。所有出口经过 Schema 和脚本检查。

会话必须开启 `conversation_enabled`，历史策略为 `recent`。预检与匹配均保留用户最近明确的条件、本轮修改优先，不接受用户对 tenantId 或身份的覆盖。Core 从业务登录身份注入租户，并传递 `user` / `club_{tenantId}_user_{userId}` 作为内容归属。Core 使用渠道 Key 和密钥接入；智能体通过现有 MCP 目录工具获取业务数据，不需要身份复核工具。

`GET /api/v1/conversations/{id}/messages?latest=true&limit=20` 从最新快照向前分页，页内始终按 sequence 正序。`next_cursor` 读取更早消息；翻页期间新消息不插入该快照，方向不同的游标不能混用。省略 latest 时保留原来的正向分页。PC 新消息重试必须重用同一 client_message_id、正文和 input；状态查询失败不重新发起任务。

配套归档见 [销售推荐配置](../sql/README.md#销售项目推荐助手配置归档)。归档更新不自动修改当前运行环境。Core 配置与完整 JSON Schema 维护在同工作区 playmate-core 的 `docs/ai-match/`。
