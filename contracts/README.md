# 公共契约 1.2.0

Python 类型为唯一来源：`core/context`、`core/auth/types`、`core/contracts` 和 `core/primitives`。运行 `make contracts` 生成各 JSON Schema、成功/缺失/失败样例和 OpenAPI，再在前端执行 `pnpm api:generate`。`make check` 与 `pnpm api:check` 拒绝过期输出。

| 交接 | 提供 → 使用 | 类型与关键语义 |
| --- | --- | --- |
| 身份与范围 | 04/05/18 → 全模块 | AuthContext / Scope；HTTP 正文使用 RunInput，不能声明受信渠道；ControlAuthContext / ControlScope 单独表示控制面身份，仅适用于登记用途，不能传给业务仓储 |
| 渠道当前状态 | 05 → 04 | ChannelStateReader / ChannelState；缺少读取器、对象或适用状态即拒绝，不从历史 Token 推断有效性 |
| 版本及执行快照 | 09/15/16 → 11/17/24 | ResourceVersion / ReleaseSnapshot；具体内容、摘要、草稿 revision、依赖闭包及输出结构一起固定 |
| 受理与预算 | 08 → 11/17 | Admission / BudgetReservation / BudgetService；先声明锁清单，admit/reserve 接收调用方 UoW，失败整体回滚 |
| 实际尝试及用量 | 07/10/17 → 08 | Attempt / UsageEvent；每次实际尝试独立标识、源请求 id、事件版本、原始口径、子集关系与完整性；累计事件替换，迟到事实可修正 |
| 工具及证据 | 10/18 → 17/场景 | ToolResult / EvidenceRef；来源版本、观测时间、授权范围、字段/文本位置及分页/截断范围 |
| 运行交付 | 11/17 → 前端/业务 | ResultEnvelope / RunEvent；技术状态独立于业务状态；非成功运行无正式 result；部分输出单列；sequence 单调，EVENTS_EXPIRED 后查询快照 |
| 受控文件与删除 | 03 → 全模块 | Artifact / DeletionGuardResult；文件地址为服务端路径；检查结果仅说明本次检查，不得缓存为永久授权；DeletionGuard 每个读写事务重新核对 |
| 界面组装 | 04/06/各模块 → 前端 | NavigationItem / VisibleAction / VersionOption / DisplayStatus；名称、中文标签及允许展示的动作均来自服务端 |

`core/*.schema.json` 使用 JSON Schema 2020-12。`examples/<类型>.json` 的 success 可直接按同名 schema 校验；missing/failure 是统一 ErrorResponse，表示关键对象缺失或当前授权拒绝。业务数据允许缺失时使用明确 nullable 字段及完整性状态，不把缺失变成零；JSON Schema 验证结构，Pydantic 及服务层额外检查跨字段关系、摘要与隔离。

认证和任务上下文是服务内部交接类型，不是可直接提交的认证凭证；公开 schema 不改变信任边界。04 已装配真实账号认证与授权；05 已提供真实渠道/Key 状态和工作区目录，未接入的资源或主体委托仍拒绝访问；没有内置允许全部的生产替身。业务运行、SSE 及具体版本管理 HTTP 接口由各所属方案实现。

兼容策略：1.x 可增加不影响执行语义的可选展示字段；新增必填字段、改变状态语义、金额精度、摘要算法或事件顺序需提升主版本。消费者对未知错误码使用服务端 message，对未知导航键不渲染；未知业务状态不能映射为成功。写请求 extra 字段拒绝，不把认证字段、权限或任意参数透传到模型。公开读取契约若需要容忍新增字段，在消费者解析层显式实现，服务内部模型维持严格检查。

Money 以十进制字符串传输，numeric(24,8) 保存，禁止客户端浮点数充当计价输入；时间使用带时区 ISO 8601。分页游标由服务签名并绑定 Scope 和查询摘要；查询变化、跨范围或签名错误拒绝。事件游标的时间窗口与补发服务由 17 实现，失效响应保留运行结果查询路径。

1.1.0 新增 IdentitySource，用于 11/17 保存并恢复原始运行身份；不包含 Token、session_id 或权限快照。04 的 TokenResponse、SessionView、账号/成员/授权/审计响应进入 OpenAPI 和前端生成类型。具体行为见 [IAM 交接](../docs/development.md#iam)。

1.2.0 为 ChannelState 增加可选 channel_status，使暂停/归档的业务拒绝具有明确状态；治理入口的当前授权不变。渠道生命周期和用量查询契约另见 [channels](channels/)，由 `modules/channels/export.py` 生成，接口和语义见 [05 交接](../docs/development.md#channels)。

08 的 AttemptPlan、ReservationReceipt 和管理查询结构由 `modules/usage/export.py` 导出至 `contracts/usage/`，供 11/17 内部受信调用使用。03 UsageEvent 继续复用原样例。渠道 UsageView 的 Token 字段现在允许 null，新增暂估费用和完整性字段；消费者需使用缺失展示，见 [08 交接](../docs/operations.md#usage)。

07 的 FrozenModel、DebugExecution、TestCompletion、ModelRequest 和 ModelEvent 由 `modules/models/export.py` 导出至 `contracts/models/`，供 16/17 内部受信调用使用。ModelEvent 保留供应商请求标识和发送边界；原始 usage 及缓存子集样例见 [models/usage-examples.json](models/usage-examples.json)，执行、取消和版本复核约定见 [07 交接](../docs/configuration.md#models)。

`contracts/runs/` 为 11 的独立交接契约，包含待 17 挂载的运行 OpenAPI、受理回执、租约、轨迹及服务端冻结定义。`ResolvedDefinition` 仅供内部解析器使用，不接受 HTTP 上传。通过 `python -m creativity_service.modules.runs.export --check` 核验。

接入契约位于 `integrations/`：当前 OpenAPI 为 `openapi-v2.json`，委托载荷为 `DelegationClaims-v2.schema.json`。v2 仅绑定渠道、环境和主体，撤销源数据域声明；HMAC 签名上下文为 `business-delegation-v2`，旧委托不再接受，接入方须同步升级并重新签发。实体、事实及指标 schema 保持业务语义。调用约定见 [业务接入](../docs/integration.md)。生成与核对入口：`python -m creativity_service.modules.integrations.export [--check]`。

MCP 的发现、差异及草稿导入契约位于 `contracts/mcp/`，由 `python -m creativity_service.modules.mcp.export` 生成，`--check` 已进入 `make check`。连接管理以主 OpenAPI 为准，执行继续使用 `contracts/tools/` 的统一入口。

技能包契约位于 `skills/`：固定依赖摘要、内部加载请求、加载结果、可移植设置及加载测试快照。运行授权字段只由 16/17 受信服务构造，不作为业务请求参数。导出命令为 `python -m creativity_service.modules.skills.export`；加载语义与边界见 [技能交接](../docs/configuration.md#skills)。

会话契约位于 `conversations/`，HTTP 路由同时进入主 OpenAPI。`ConversationRunRequest` 与 `SelectedContext` 为 12/17 内部交接对象，客户端只提交 `MessageInput`。导出及过期核对：`python -m creativity_service.modules.conversations.export [--check]`；语义见 [会话交接](../docs/runtime.md#conversations)。

22 的 `backend/openapi-v1.json` 从正式 `/api/v1` 路由裁剪，覆盖 Token、运行/SSE、会话与产物。通过 `uv run creativity-openapi --backend` 生成，`--backend --check` 校验；已纳入 `make contracts/openapi/check` 对应目标。签名仍采用 integrations 目录的版本化协议和向量。
