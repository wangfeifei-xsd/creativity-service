# 22 统一 AI 接口接入指南

依据 [方案 22](../../代码编写执行方案/22-统一AI接口与调用交付.md)，公共规范见 [rule.md](../../rule.md)。业务后端以渠道服务身份和受信主体委托调用同一套运行服务；数据、规则和领域结论由业务 MCP、Skills/Agent 配置及业务后端解释。

交付入口：[通用客户端](../examples/backend/README.md)、[仅业务接口的 OpenAPI](../contracts/backend/openapi-v1.json)、[全部接口](../contracts/openapi.json)、[SSE 帧协议](runtime-sse.md)、[验证记录](unified-api-validation.md)。业务接口契约通过 `uv run creativity-openapi --backend` 从正式路由生成，`--backend --check` 校验漂移；没有第二套运行实现。18 的历史版本契约继续保留。

## 接入顺序

1. 在目标渠道配置环境、外部数据域映射和接入服务；签发 API Key 以及独立委托密钥。`channel_id/environment/client_id` 由 Key 和服务端确定，普通正文不能指定或覆盖。
2. 按 [20](mcp-business.md) 配置 MCP、工具发布和专用当前主体复核。身份复核工具不能作为模型工具调用。缺失配置、主体失效或远端复核失败会阻断业务访问和 Worker 后续步骤。
3. 按 [21](configuration-delivery.md) 导入技能、配置模型路由/提示词/工具及 Agent 输入输出 schema，调试后发布到目标环境。需要中间文本时模型路由应声明并验证 `streaming` 能力；`delivery=stream` 表示通过 SSE 交付，不修改已冻结模型能力。
4. 后端调用 Token 交换接口，再从当前登录及权限服务取得主体，按实际 HTTP 方法、路径、原始查询串、正文 SHA-256 和适用幂等键签署委托。签名规范和跨语言向量沿用 [18](integrations.md)，不能把浏览器传入的用户/数据域原样签名。
5. 持久化业务请求键，创建运行，保存运行引用，通过同步等待、查询或 SSE 取得结果。业务前端仅访问自己的后端。

| 用途 | 接入服务、Key 及委托所需动作 |
| --- | --- |
| 创建及取消运行 | `run:create`，以及实际 Agent/依赖资源授权 |
| 查询结果 | `run:read`、`run:content`、`data:read_sensitive` |
| SSE | `run:content`、`data:read_sensitive`；客户端快照回查另需 `run:read` |
| 创建、发言、修改和归档会话 | `conversation:write`；读取会话另需 `conversation:read` 与 `data:read_sensitive`；发言另需运行权限 |
| 上传/导出派生产物 | `artifact:upload`、`content:derive`，以及原会话读取权限；导出另需 `data:export` |
| 下载产物 | `artifact:download`、`data:export`、`data:read_sensitive`，以及当前资源授权 |

22 修补渠道模块遗漏的 `conversation:write/content:derive` 可选服务动作。不会给已有服务、Key 或主体自动增加权限；配置应同时满足角色/管理授权上限和当前源主体复核。`resources` 使用资源类型与平台资源标识；`*` 仍受渠道、环境、数据域、主体及调用服务归属约束。

## HTTP 交付

| 操作 | 请求与返回 |
| --- | --- |
| 换 Token | `POST /api/v1/auth/token`，正文 `{"api_key":"后端凭据"}`；200 返回 `access_token/token_type/expires_in/expires_at`，不要求委托 |
| 创建运行 | `POST /api/v1/runs`，头 `Authorization`、`X-Business-Delegation`、`Idempotency-Key`；正文 `agent_code/input/delivery`，可选会话标识见 OpenAPI |
| 同步等待 | `delivery=sync` 最多等待 15 秒；窗口内到达任何终态返回 200 `ResultEnvelope`，否则返回原运行的 202 `AdmissionReceipt` |
| 异步/流式受理 | `delivery=async/stream` 均返回 202，含 `run_id/state/created_at/deadline/status_url/events_url`；POST 不保持执行连接 |
| 查询 | `GET /api/v1/runs/{run_id}`，200 返回状态、结果、错误、用量、产物和发布快照引用；运行失败仍是成功取得快照的 200 |
| 取消 | `POST /api/v1/runs/{run_id}/cancel`，200 返回运行引用；已成功则保持成功，排队任务取消后终结，在途任务进入取消流程 |
| 订阅 | `GET /api/v1/runs/{run_id}/events`，200 为 `text/event-stream`；`Last-Event-ID` 优先于 `after_sequence` 查询参数 |
| 会话 | `/api/v1/conversations` 创建/列表；`/{id}` 详情/标题/删除；`/{id}/messages` 分页或提交；归档、恢复、关联运行和删除进度见 OpenAPI |
| 产物 | `GET /api/v1/artifacts/{artifact_id}/content`；会话附件和导出使用返回的受控路径。每次下载重新认证和授权，不返回可绕过授权的永久对象地址 |

运行结果只在通过冻结输出 schema 后提交。`result` 为公共 `business_status/schema_version/data/warnings/evidence_refs` 封装；`data` 可以是任意符合配置的 JSON 对象。平台不固定价格、风险等级、指标或候选字段。`SUCCEEDED` 表示技术运行完成，`NEEDS_INPUT/PARTIAL` 等业务状态仍由接入方解释。

会话 `POST /messages` 使用正文 `client_message_id` 去重，相同消息必须保持该标识和内容。创建会话/上传/导出没有运行幂等键语义，响应不明时先核查，示例不对这些写操作自动网络重发。

## 重发、恢复和撤权

Token 到期：再次用当前 Key 换取 Token，重新签名查询原 run。已受理任务的执行权依赖保存身份与当前权限复核，不依赖请求 Token 一直存活。Key、接入服务、渠道、环境、数据域、委托密钥或源主体撤销仍会阻止后续执行。

创建响应丢失：沿用原幂等键和语义输入，新委托按实际重发请求签名。delivery 可变而不产生新逻辑运行；同一键配不同语义输入返回 409 `IDEMPOTENCY_CONFLICT`。幂等范围使用稳定 `client_id`，API Key 轮换不产生第二个 run；记录的原 key_id 与用量不重写。旧 Key 失效不能继续作为执行凭据，轮换应保留足够重叠期以处理在途任务。

SSE：保存已处理的序号，断线后对新的 `after_sequence` 路径重新签名；也可携带 `Last-Event-ID`。无序号 control 仅表示该连接关闭。401 重新认证后恢复，403 停止访问，410 `EVENTS_EXPIRED` 回查原快照。SSE 断线不取消任务。`text_delta` 的 `validated=false` 表示中间文本；只有 `result` 事件的 payload 经过最终输出校验。同一 result 也可能通过快照再次取得，业务后端按 run_id 对最终结果幂等落地。

权限撤销：查询、事件发送、心跳、下载和幂等命中都检查当前权限。旧 URL、旧游标、旧签名或新的有效服务 Token 均不能绕过被撤销的主体权限。示例禁止对其他源站发送平台凭据，也不跟随重定向。

## 错误与排障

HTTP 请求失败格式为 `{"error":{"code":"…","message":"…","fields":[]},"request_id":"…"}`，请求标识与 `X-Request-ID` 一致。运行终态中的 `error` 为 `code/message/stage/retryable/request_id`；HTTP 200 不表示执行成功，必须检查 state。`stage` 当前通常为通用执行阶段，结合错误码和管理轨迹定位具体步骤。不要把供应商原文、Token、委托载荷或密钥写入日志。

| 类别 | 典型标识/状态 | 处理方式 |
| --- | --- | --- |
| 平台认证 | 401 `UNAUTHENTICATED`；403 渠道/环境/服务停用 | 首次 401 重新交换 Token；仍失败检查 Key 当前状态。Key 不能直接作 Bearer |
| 主体委托 | `DELEGATION_REQUIRED`、`DELEGATION_INVALID`、`DELEGATION_EXPIRED`；403 `DELEGATION_REQUEST_MISMATCH`、`DELEGATION_SCOPE_INVALID`、`DELEGATION_FORBIDDEN`；409 `DELEGATION_REPLAY` | 检查 kid、issuer/audience、时钟、实际方法/路径/正文、源域映射和权限上限；新请求使用新 nonce |
| 当前源身份 | `SUBJECT_REVIEW_REQUIRED`、`SUBJECT_REVIEW_CHANGED`、`SUBJECT_REVIEW_DENIED`、`SUBJECT_REVIEW_INVALID`、`SUBJECT_REVIEW_UNAVAILABLE` | 检查专用 MCP 绑定与发现版本、连接凭据、主体实时授权、超时；失败时不使用历史委托兜底 |
| MCP 工具 | `MCP_REMOTE_FORBIDDEN`、`MCP_REMOTE_TIMEOUT`、`MCP_TOOL_CHANGED`、`MCP_RESULT_INVALID`、`TOOL_INPUT_INVALID`、`TOOL_RESULT_INVALID`、`TOOL_EFFECT_MISMATCH` | 按具体前缀错误定位工具输入、远端权限/超时、已发布 schema 与效果声明；重新发现须新版本验证 |
| 模型调用 | 模型适配错误、`MODEL_CALL_LIMIT`、`STEP_TIMEOUT` | 查看步骤尝试、模型版本与 source_request_id；按服务端策略重试，来源不明用量不能当零 |
| 预算与并发 | 429 `BUDGET_EXCEEDED`、`BUDGET_PRICE_REQUIRED`、`BUDGET_ESTIMATE_REQUIRED`、`CALL_LIMIT` | 检查本渠道预算、预占、价格及调用上限，不能换幂等键规避 |
| 输入/输出校验 | 422 `INPUT_SCHEMA_INVALID`、`VALIDATION_ERROR`；运行 `MODEL_OUTPUT_INVALID`、`OUTPUT_SCHEMA_INVALID` | 输入按当前 Agent schema 修正；输出失败不把 text_delta 或工具原文当成功结果 |
| 事件/会话/幂等 | 410 `EVENTS_EXPIRED`；409 `SESSION_BUSY`、`SESSION_ARCHIVED`、`IDEMPOTENCY_CONFLICT` | 查询原快照、等待/取消已有会话任务、恢复归档或修正消息/请求键，避免重复创建 |

错误对象保留服务端完整 code，不靠中文文本做程序分支。403、409、422 不盲目重试；429/503 的外层恢复由业务后端有界安排，已受理请求仍沿用原运行。

排障提供：UTC 时间、入口 `X-Request-ID`、run_id、release_snapshot_id、步骤/尝试标识、source_request_id、错误码与状态。管理端受权轨迹将 run_id 关联到 MCP 工具调用和源请求标识；模型尝试单独记录供应商来源。示例 [脱敏执行摘录](unified-api-evidence.json) 仅包含隔离测试身份、测试内容和来源关联标识，无凭据。测试结束后数据库隔离 schema 已清理，摘录中的运行不可当作线上可查询对象。

23 继续使用同一客户端，将另一组工具、技能和 Agent 作为配置输入；24/26 补正式评测门禁及至少两种真实模型兼容证据。
