# 10 工具管理与执行交接

实施日期：2026-10-02。依据 [方案 10](../../代码编写执行方案/10-工具管理与执行.md) 和 [工具需求](../../需求文档/10-工具管理.md)，开发规范统一引用 [rule.md](../../rule.md)。验证见 [tools-validation.md](tools-validation.md)。

## 实现入口

- `modules/tools/`：资源管理、公共版本冻结和环境发布、引用影响、停用、管理测试描述、调用查询、执行管线和证据持久化。
- `integrations/tools/`：统一注册表、HTTP 适配器和十进制求和预置函数。来源实现声明实际副作用、实现版本、渠道/环境和连接标识；不接收上传代码。
- `creativity-web/src/features/tools/`：列表筛选，以及契约、连接绑定、权限、策略、测试、版本与引用、调用详情页面，通过 06 的模块注册入口接入。
- `0010_tools`：新建 `tools`、`tool_calls`、`evidence_refs`，版本继续共用 `resource_versions` / `release_mappings` / `resource_references`。迁移接在并行单元的 `0009_prompts` 后，复用该修订对模型、用量分支的汇合。

## 管理接口

`GET/POST /admin/v1/tools`，`GET/PATCH /admin/v1/tools/{id}`，`POST /admin/v1/tools/{id}/versions`，`GET/PATCH /admin/v1/tool-versions/{id}`，以及 `freeze`、`tests`、`test-description`、`releases`、`impact`、`disable`、`tool-bindings`、`tool-calls` 管理接口已登记。完整类型以 [OpenAPI](../contracts/openapi.json) 为准。

编码在渠道锁内查重；草稿用修订号控制并发，冻结后不可编辑。发布显式要求独立发布授权。配置只允许授予当前核准工作区，切换工作区后可创建对应权限版本。写类型可保存草稿并显示真实影响，但不能冻结、发布或执行。

普通业务 API 不登记工具执行端点。`tests` 先校验管理权限、固定修订、输入和影响类型，再由 `ToolRunPort.create_debug_run` 创建 debug run；未装配 17 时返回依赖不可用，页面禁用执行按钮。

## 执行与来源契约

17 通过 `ToolExecutor.execute(context, ToolExecution(...))` 调用。`context` 来自当前认证或持久化运行身份核验，调用体只有 run、step、具体版本和业务参数。`ToolRunPort.authorize_call` 必须核对持久化 run/step、固定 Agent 版本、工具白名单、当前授权修订与剩余运行限额；此方法可在一次调用内重复复核，不重复扣减。实际预算及次数在 `start_attempt` 中原子占用，每次重试有独立 Attempt，`finish_attempt` 持久化终态。

执行前、重试前及结果交付前均复核当前身份、资源、Agent 与工具权限。正式调用只使用已冻结版本，调试/评测可使用运行中固定的草稿修订。受信字段包含渠道、俱乐部/租户、主体、用户、数据域、令牌、权限和执行目标；嵌套参数和常见命名变体也拒绝覆盖。输入使用封闭 JSON Schema 与明确业务字段白名单，结构定义不解析引用或远端地址。

HTTP 适配器接收服务端固定 URL 与 GET/POST 方法，不从模型参数读取目标。`OutboundPolicy` 必须提前登记同渠道、同环境的 `http_tool` 目的地；传输连接本次核准 IP，TLS/SNI 使用原域名。拒绝重定向和压缩结果，有界读取响应，超时或取消关闭连接；底层传输不自动重试。GET 业务参数进入查询串，受信身份使用独立请求头；POST 使用 `arguments` 与 `identity` 两个对象。来源仍需执行自身业务授权。认证头仅通过服务端 `CredentialHeaders` 回调取得，回调须复核连接状态及凭据使用权限，不可持久化或输出明文。

来源返回 `AdapterResult`，包含结构化 data、源请求标识、源版本、带时区观测时间及明确的分页/截断元数据。超时、429、502/503/504 和网络失败允许有限只读重试；契约错误、无权访问、重定向及超大结果不重试。截断须提供原始/返回数量；分页有后续数据时须提供游标和范围。外部文本仅作为结果数据，不能改写授权或运行策略。

结果经过 schema、大小、新鲜度、分页、证据和文件授权检查后才持久化并返回。外部提供的 `EvidenceRef` 必须已经登记且内容与当前主体授权一致；来源新事实由执行管线生成本次调用证据。文件须使用结构化 `artifact_id` / `artifact_ids`，必须同范围、未删除、未过期并通过实时下载授权。不得把外部 URL 当作平台文件引用。

## 缓存、证据与清理

Redis 缓存键包括渠道、环境、数据域、主体、成员/服务及 Key、Agent 版本、工具版本和完整定义、授权修订/动作与完整业务参数。缓存命中仍经过实时权限、schema 和删除屏障复核。价格/库存缓存不超过 5 秒，最终业务确认继续由场景和源服务复核。

`tool_calls` 记录每次实际 Attempt；拒绝和缓存记录没有虚构 Attempt。仅保存参数摘要、全部脱敏参数、结果结构摘要、来源请求标识、耗时、错误和证据关联，不保存业务原文。有效证据和调用登记来源图；复用证据是缓存调用的上游，删除缓存消费者不会倒置污染原始证据。

`register_tool_cleanup` 注册 `tool_call` / `evidence` 清理器，清理前强制检查删除标记。缓存正文最多保留 TTL，命中读取仍受证据来源删除屏障约束。25 继续负责定时清理调度与独立恢复账本。

## 后续装配

| 单元 | 交接入口 |
| --- | --- |
| 14 | 先登记同渠道 MCP 适配器，再调用 `ToolService.import_draft`；真实 MCP 连接状态、发现和调用由该单元实现 |
| 16 | `ToolService.check_dependency` 返回可执行的本地固定版本；正式 Agent 显式保存白名单与版本 |
| 17 | 实现并注入 `ToolRunPort`，将执行器与管理调试服务装配到同一个运行实例；覆盖 debug、evaluation 与 production 的统一限额 |
| 18 | 用 `AdapterRegistry.register` 登记业务适配器；实现当前主体委托和来源连接/凭据授权 |
| 19/20、26 | 真实租号/陪玩查询、价格库存最终复核、运行预算及外部接口联调 |

内部交接 schema 在 `contracts/tools/`，运行 `python -m creativity_service.modules.tools.export --check` 验证；`make check` 已纳入此项。真实 MCP、业务接口、生产运行预算及 P1 写操作的未知结果处置不计为本次已完成能力。
