# 18 业务接入与身份委托交接

2026-10-02 增量：渠道分类、显式映射、通用流程及旧 HTTP 兼容处理已由 [19](access-decoupling.md) 交付；下文保留原单元交付说明。当前接入契约使用 `contracts/integrations/openapi-v1.1.json`。

执行日期：2026-10-02。依据 [执行方案 18](../../代码编写执行方案/18-业务接入与身份委托.md)；公共规则引用 [rule.md](../../rule.md)。本文记录 18 在旧需求 v0.6 下交付的公共协议、服务与页面。按 v0.7 边界，领域工具改由业务 MCP 提供；19/20 负责旧协议兼容与通用主体复核，22/23/26 验证统一 API 及业务无关接入。本文的标准 HTTP 领域协议为既有实现说明，不是新增业务的必选接口。

## 已实现接口与存储

`modules/integrations` 提供当前渠道、环境和数据域内的连接列表、新建、详情、修订更新、健康、能力清单与脱敏契约测试。`GET/POST /admin/v1/integrations`、`GET/PATCH /admin/v1/integrations/{id}`、`GET/POST /admin/v1/integrations/{id}/tests`、`GET /admin/v1/integrations/{id}/capabilities`；选项由 `/integrations/options` 返回。业务服务 Bearer 凭据通过 `POST /admin/v1/integration-credentials` 加密保存，连接仅保存引用。

独立委托密钥接口为 `GET/POST /admin/v1/delegation-keys`、`POST /delegation-keys/{kid}/rotate` 和 `/revoke`。创建/轮换仅本次返回 Base64 签名密钥，并设置 `Cache-Control: no-store`；列表只返回配置。渠道 API Key 与 HMAC 签名密钥分开管理、加密与审计。密钥绑定 Token 已确定的 `channel_id + environment + client_id`，`kid` 不可跨该范围定位。轮换产生新 kid；旧密钥重叠期可配置，默认 330 秒，建议覆盖委托有效期与容差。吊销立即阻断旧声明和后续执行边界。

前端 `/integrations` 由现有工作区登记协议接入，提供连接配置、能力和测试明细、签名密钥创建/轮换/吊销。委托密钥入口使用服务端返回的操作入口；无前端角色计算。系统菜单新增 `integration:manage`，既有成员的显式授权不会自动扩大，需要通过已有授权页面授予。

`0018_integrations` 新建 `integrations`、`delegation_keys`、`integration_tests`、`delegation_nonces` 四表；前三张从设计基线实现，nonce 表补齐防重放和后台主体来源。密文复用公共 `credentials`，配置及轮换审计复用 `audit_events`，源数据域映射只读取 05 的 `data_scopes`。表定义见 [模块模型](data-model/modules/integrations.md)。

## 签名协议 v1

请求头 `X-Business-Delegation` 格式为 `v1.<kid>.<payload>.<signature>`，payload 是 UTF-8 JSON 的无填充 Base64URL；signature 是下列 ASCII 字节的 HMAC-SHA256，输出无填充 Base64URL：

```text
business-delegation-v1\n<kid>\n<payload>
```

上例 `\n` 表示一个 LF 字节，末尾无换行。验签覆盖收到的原始 payload 段，不重新序列化 JSON；拒绝重复 JSON 字段、非规范 Base64URL、未知字段和超长载荷。JSON 时间为 UTC Unix 整数秒。签名密钥为至少 32 字节的独立随机秘密，配置接口签发 32 字节；算法固定，声明不能选择算法。Python 和 JavaScript 向量见 [delegation-vectors.json](../contracts/integrations/delegation-vectors.json) 与 [sign-vector.mjs](../contracts/integrations/sign-vector.mjs)，样例密钥公开，仅供验证。

声明包括 `subject_type/subject_id`、源 `data_scope.type/id`、`actions`、`resources`、`issuer/audience`、`issued_at/expires_at`、`nonce` 和 `request`。可选渠道和环境必须与 Token 一致。`request` 包含 HTTP 大写方法、原始 ASCII 路径及原始查询串、实际发送正文的 SHA256、适用的 `Idempotency-Key`。正文摘要不重排 JSON；签名后必须发送相同字节，空正文按空字节计算。查询串顺序、百分号编码和路径变化都必须重新签名，不对代理改写前的路径签名。

默认最长有效期 300 秒、时钟容差 30 秒，密钥配置分别允许 30–900 秒和 0–60 秒。首次检查 Token、当前服务/渠道/Key，按该范围取密钥并验签，再解析 05 的源数据域映射，检查当前 client/Key 权限上限并持久化 nonce。伪造签名不能触发数据域探测。

`resources` 使用资源类型到标识数组的映射，例如 `{"agent":["agent_match"],"run":["*"]}`。`*` 只覆盖已经通过 IAM 渠道、环境、数据域、主体、调用服务归属校验的资源，不提供跨范围查询。Agent/工具白名单仍由运行/工具服务再次约束；声明不得请求 client/Key 未授予的动作。

nonce 作用域为渠道、环境、稳定 client_id；安全重发要求 kid、完整声明、主体范围和请求摘要都相同，绝不以可轮换 API key_id 为 nonce 边界。重发不会第二次插入记录；改变主体、正文、目标资源或幂等键，返回 `DELEGATION_REPLAY`。超过委托有效期应获取新委托和新 nonce，仍沿用原运行幂等键；11 的运行幂等事务独立决定是否返回原 run。nonce 最少保留 24 小时；清理与已完成任务保留策略交给 25，当前不会提前移除记录。

`anonymous` 仅允许 `run:create/run:read`，资源类型仅限 Agent/运行；业务适配能力还须标记为公开只读。匿名主体不能请求记忆、敏感原文、文件导出等动作，源后端应生成受限匿名会话标识。17 的匿名公开结果需要经过公开输出裁剪，不能给匿名主体授予敏感原文权限；11 的通用原文查询仍保持拒绝。普通主体的用户/俱乐部必须来自业务登录与当前权限服务；不能将前端传入字段原样签名。

## 运行与业务适配交接

统一 `require_http_context` 对服务接口调用 `app.state.delegation.verify_request`，不创建项目专用运行 API。受信 `AuthContext` 增加可选 `delegation_id`；`IdentitySource.worker_context` 与 11 保存的运行身份保留该引用。IAM 的 `SubjectAuthorityReader` 已装配为 `DelegationService`，每次读取核对声明所属范围、当前密钥和源数据域。

Worker 当前需要注入 `CurrentSubjectReader`，后续由 20 提供可配置的通用实现，在源业务后端再次查询主体是否启用和当前权限；返回范围必须一致，动作和资源只能收窄。未注入则拒绝执行，不能把一份 300 秒声明无限延长。已合法受理任务不依赖 Redis Token 的自然到期；短声明到期后的执行由源权限复核决定，Key/渠道/委托密钥吊销仍立即阻断。17 在完整主体范围建立经过恢复门禁核对的内容范围后受理运行，并应将发布快照的 Agent 权限与工具白名单接入最终执行判断；资源 `*` 始终先经过原始身份范围校验。

`integrations/business/base` 给出字典、候选、报价、详情复核、风险事实、政策引用、指标目录及指标结果的独立方法；基类所有未实现方法返回 `BUSINESS_UNSUPPORTED`。`BusinessRegistry` 只登记代码内明确实现及版本，拒绝重复版本或虚报能力。`standard_http@1.0.0` 实现统一只读协议，按管理员配置的单项能力路径发送 POST；不接受模型提供的 URL、HTTP 头或 SQL。

标准源接口接收 `operation/arguments/identity/request_id/run_id/contract_version`；identity 包含平台范围、源数据域 type/id、主体、操作者或接入服务及当前动作。源服务必须验证服务间 Bearer 凭据，再用原业务授权服务检查主体、俱乐部、资源和数据权限。响应遵循 [BusinessResult](../contracts/integrations/BusinessResult.schema.json)，实体引用不得覆盖受信范围，名称和单位须来自授权字典。源响应字段变化、缺名称、分页不完整、范围不符均返回 `BUSINESS_CONTRACT_CHANGED`。显式字段映射为标准字段名到源字段名，仅做重命名，不推断价格、状态或单位。

传输复用固定 IP/TLS 校验和部署目的地白名单，不自动跟随重定向；15 秒 HTTP 超时、20 秒单能力边界、2 MiB 响应上限。每次请求包含 `X-Request-ID` 和 `X-Run-ID`。测试的 run_id 使用独立契约测试编号，仅用于链路关联，不声称创建正式运行。适配器的外部调用全部在配置/测试结果事务之外，发送和接收边界复核身份与连接修订。

既有 HTTP 兼容接入可调用 `register_business_tools` 将连接能力登记到 10 的工具注册表；`BusinessToolAdapter` 固定连接修订和单项能力，`AdapterRequest.run_id` 由统一执行器从实际 ToolExecution 注入。连接变更后旧绑定拒绝继续执行，须用新修订登记并重新发布工具。后续不再为租号/陪玩编写真实字典与字段转换器；这些领域职责移至业务 MCP。新接入通过 14/20 的 MCP 发现与授权流程完成，不使用旧固定 operation 作为能力全集。

错误语义及 HTTP 状态见 [errors.json](../contracts/integrations/errors.json)：无权限、超时、无数据、下架、缺失、不支持、不可用、契约改变均保留。源错误原文、查询参数和响应内容不存入契约测试记录，仅保留能力、固定结论、条数和配置修订。同配置按每个能力的最近一次测试显示验证状态；配置变化使旧测试失效。

## 配置与调用

服务端环境变量 `CREATIVITY_BUSINESS_KEY_VERSION`、`CREATIVITY_BUSINESS_ENCRYPTION_KEYS` 配置加密主密钥版本与 Base64 的 32 字节密钥字典；HMAC 密钥由管理接口生成，与这些加密主密钥不同。`CREATIVITY_BUSINESS_DESTINATIONS` 为出站白名单，字段沿用公共 Destination：channel_id、environment、purpose=http_tool、hostname、scheme、port、path_prefix 及必要的 allowed_networks。未配置时拒绝保存/调用目标，不提供生产默认秘密。

业务后端示例 [business_backend.py](examples/business_backend.py) 展示 Token 缓存、换取、签名、提交、查询、订阅及取消；业务前端只请求自己的后端。正常断网重试沿用原委托、正文及幂等键，401 最多换 Token 重试一次。SSE 使用 Authorization 请求头，恢复时携带游标并重签实际请求路径；完整流协议由 17 实现、26 联调。

版本化接口为 [openapi-v1.json](../contracts/integrations/openapi-v1.json)；独立实体/事实/指标 schema 位于同目录。运行路由在离线契约中复用 11 的待装配 router（实际挂载仍归 17），后续 17 补充 SSE 后重新生成，不复制一份项目专用实现。执行 `python -m creativity_service.modules.integrations.export --check` 校验交付物。
