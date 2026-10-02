# 07 模型配置与协议适配交接

实现范围见 [方案 07](../../代码编写执行方案/07-模型配置与协议适配.md)，功能依据为 MOD-F01—F10。07 已实现配置服务、协议适配、管理页面和单元交接；真实供应商、预算及完整 debug run 联调仍在 17/26 完成。验证证据见 [验证记录](models-validation.md)。

## 实现入口

| 入口 | 职责 |
| --- | --- |
| `modules/models/services.py` | 系统供应商字典和无凭据模板、渠道连接、稳定别名、映射历史、参数校验及 IAM 授权复用 |
| `modules/models/routing.py` | 路由版本、当前能力与授权校验、发布切换、16 的 `check_dependency`、17 的 `resolve_route` / `prepare_attempt` |
| `modules/models/testing.py` | 固定验证用例、冻结执行描述、测试状态及运行完成回调 |
| `integrations/models/adapter.py` | LiteLLM 的 Chat Completions / Anthropic Messages 转换，一次尝试一次 HTTP 请求 |
| `integrations/models/transport.py` | 使用已核准 IP 建连并保持原域名 SNI；禁止重定向和一次尝试内再次发送 |
| `integrations/models/usage.py` | 原协议 usage 与子集关系，不做价格计算 |
| `modules/models/assembly.py` | 当前资源读取器、独立凭据主密钥、出站策略和测试内容清理登记 |
| 前端 `src/features/models/` | 供应商、模型/连接、路由页面及连接、能力、参数、价格、验证、授权、历史详情 |

五张新表及字段见 [模型归档](data-model/modules/models.md)，冻结于 `baseline_v0004.json`。连接、映射和路由的内容、依赖、发布映射全部复用公共版本表。迁移 `0004_models` 与 `0004_usage` 在配置批次汇总为一个 Alembic head；不修改 03—05 的冻结表定义。

## 版本与能力

连接和模型都有普通 `revision` 及 `validation_revision`。普通修订用于并发冲突；验证修订只在协议、地址、超时、模型名、连接、上下文上限、参数或允许参数清单变化时递增。配置摘要包含这两个对象的验证修订，因此把地址改回旧值不会恢复旧证据。健康结果更新不改变配置版本，人工启停与健康状态分别展示。凭据轮换创建新的凭据引用及连接版本，不改写历史版本。

能力证据包含配置摘要、验证时间、test_id 和证据来源。只有 `live` 且摘要匹配的成功用例才产生“已支持”；`fixture` 仅验证软件契约。映射与连接当前启用、能力已验证且有当前使用授权，才能创建正式候选路由；发布时再检查一次。未知上下文上限保持空值。不支持或超范围参数返回明确错误，不传递为隐式供应商参数。

## 17 执行端口

`POST /admin/v1/models/{id}/tests` 始终先保存 `DebugExecution`，其中包含 `FrozenModel`、配置 revision/digest、具体用例和凭据引用。未注入 `DebugExecutor` 时返回一条 `BLOCKED` 记录，中文状态为“不可执行”，无 run、Attempt 或模型消耗。接口不会直接访问供应商。

17 注入 `DebugExecutor.submit(context, execution)`：按 test_id 幂等受理统一 debug run，进行预算准入与预占，并为每个真实调用创建独立 Attempt。完成后仅通过内部 `ModelTesting.complete` 回写 `TestCompletion`；HTTP 不开放写入验证通过证据的入口。回调须来自已核对的持久化 run，正确区分 live 与 fixture；迟到结果不覆盖已变化配置的能力。07 只保存无原文用例反馈和尝试引用，完整执行证据由运行/用量模块保存。

正式调用步骤：

1. 读取冻结路由，并用 `resolve_route` 获取同渠道环境的候选及固定尝试顺序。16 可用 `check_dependency` 校验直接模型依赖。
2. 用 `prepare_attempt` 获取实际连接版本和凭据标识；17 将其与 model_id、provider_model_name、预算/价格版本写入尝试来源。
3. 17 创建 `STARTED` 的 `Attempt` 和 `HELD` 的 `BudgetReservation`，再调用 `adapter.events`。两者必须匹配 Scope、run_id、attempt_id 和模型版本。每次请求显式提供 max_tokens；适配器不会增加总预算或创建隐藏重试。
4. 适配器在密钥读取后再次检查当前模型及授权。若登记后连接或凭据版本变化，拒绝发送，交回运行时重新准备，不静默改变已登记的实际来源。
5. 消费 `text/tool/structured/usage/completed/failed/cancelled`。工具增量以 tool_index 关联并保留供应商 tool_call_id。structured 结果经过本地 JSON Schema 校验。SDK 重试为零，协议层只允许一个 HTTP 请求；运行时据固定顺序和 `retryable` 决定下一独立 Attempt，并再次受 run 总限额约束。
6. 任何正文或工具增量开始后错误均不可在同一流回退。结构校验失败返回 `MODEL_OUTPUT_INVALID`，17 按 Agent 有限修复策略另记尝试，07 不循环修复。

取消与失败仍交付已捕获的 usage，`final=false` 避免把中断时的局部计量当作完整结算；缺失数量为 null。终结/计量事件的 `request_sent=false` 表示尚未进入 HTTP 发送，可由 17 按 08 的未发送路径释放预占；true 仅表示进入发送边界，不能作为零消耗证明。没有供应商请求标识时，UsageEvent 用 attempt_id 作本地来源关联，ModelEvent.source_request_id 保持 null，不伪造供应商编号。

## 08 计量与价格

`ModelPriceReader` 由 08 的 `ModelPrices` 在应用中注入。模型页通过 08 的 `/models/{id}/price-versions` 维护价格，07 不保存第二份价格。硬金额路由发布在同一策略锁事务中调用 `require_priced`；价格端口缺失或有效价格缺失时拒绝发布。08 继续负责币种、价格维度完整性、有效时间及计价结果。

原始样例见 [usage-examples.json](../contracts/models/usage-examples.json)。Chat 的缓存读取是 prompt_tokens 的子集。Messages 的 input_tokens 原本不包含缓存读写，07 合成总输入后把 cache_read/cache_write 明确标为 input 子集。供应商未报告的缓存量仍为 null；08 不能将其隐式补零。

## 服务端配置

`CREATIVITY_MODEL_DESTINATIONS` 是管理员部署的 JSON 数组，每项采用公共 `Destination` 字段：channel_id、environment、purpose=`model`、hostname、port、scheme、path_prefix、allowed_networks。须逐渠道环境登记实际目标。内网模型需明确网络授权。地址保存与发送都会校验；传输连接本次核准的 IP，不再次解析 DNS。

`CREATIVITY_MODEL_KEY_VERSION` 指定当前密文主密钥版本；`CREATIVITY_MODEL_ENCRYPTION_KEYS` 是版本到 Base64 编码 32 字节密钥的 JSON 对象。由部署密钥注入，不提供默认生产密钥；轮换时保留历史密钥解密能力。也可以向 `build_model_services` 注入外部 KeyProvider。缺少密钥提供器时拒绝保存或解密凭据。

平台管理员在平台工作区维护供应商模板。复制模板只填入协议、地址和超时，渠道重新保存凭据。账号/角色的模型使用授权复用 IAM `resource_grants`，撤销继续走 IAM 原接口；Agent 的独立身份及 agent_actions 交集由 16/18/17 组装，07 不新增一套 Agent 授权表。当前资源读取器串接既有 IAM 读取器，不替换其他模块的资源状态。

## 兼容矩阵

| 协议 | 本次夹具证据 | 真实验证 | 尚未启用 |
| --- | --- | --- | --- |
| Chat Completions 兼容 | LiteLLM 文本、工具标识/参数、schema 失败拒绝、流中断、usage、取消及错误分类 | 尚未执行；按具体连接和模型在 17/26 验证 | 视觉、embedding；兼容供应商的未验证参数 |
| Anthropic Messages | LiteLLM 文本、工具调用、流式事件、原始缓存计量合并 | 尚未执行；按具体连接和模型在 17/26 验证 | 原生结构化输出、视觉、embedding |
| OpenAI Responses | 独立协议登记，执行关闭 | 未执行 | 全部执行能力；不会经兼容端点冒充支持 |
| Gemini generateContent | 独立协议登记，执行关闭 | 未执行 | 全部执行能力 |

MOD-A01/A03/A06 的真实供应商及 run 总预算、用量账本组合仍由 17/26 验收。MOD-F01 的真实鉴权/最小调用、MOD-F10 的有限修复也通过同一个执行端口接入。配置保存、能力门禁及夹具通过不表示生产模型已经可用。
