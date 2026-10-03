# 第 28 号增强配置

实现范围与验收见 [E1](../../../代码编写执行方案/28-E1-检索与配置体验.md)、[E2](../../../代码编写执行方案/28-E2-受控执行扩展.md)、[E3](../../../代码编写执行方案/28-E3-分析与集成.md)、[E4](../../../代码编写执行方案/28-E4-身份与运营.md)。所有外部能力使用服务端配置；示例标识需要替换为本环境实际值。第 27 项部署与上线已按用户决定跳过，本文件不代表正式环境已经启用。

## 数据库与依赖

新增迁移链：`0028_memory_vectors → 0029_mcp_oauth → 0030_automation → 0031_identity_operations`。迁移仅增加本次模型，保持应用服务层校验和事务互斥；模型档案在 `docs/data-model`。本次集成验收在临时 schema 升级到 head，测试结束清理 schema，没有升级主 public schema。应用开发环境准备使用时执行 `uv run alembic upgrade head`，不要把测试临时 schema 作为应用数据库。

语义检索依赖 pgvector 0.8.2，安装/启用方法见 [向量环境](../../deploy/vector.md)。本次本地数据库已安装并启用该扩展。向量缓存保存 JSONB，先物化完整 Scope 与已复核记忆集合，再使用 pgvector 的余弦距离精确排序；当前没有 ANN 索引。Agent 的 `embedding_route_version` 绑定已通过 embedding 能力测试的单模型路由，并开启记忆策略。缺扩展或供应商失败按该版本的 OMIT/FAIL 策略处理，向量成本进入用量和预算。

## 暂停与写工具

Agent 可视化流程的计算节点提供对象组装、等待补充和等待审批。等待期间释放 Worker，恢复仍使用原 run、冻结快照及截止时间，等待时长计入总限时，恢复不会续期。

管理端与业务端均提供 `GET /runs/{id}/interruption`、`POST /runs/{id}/resume`。恢复正文带 `interruption_id`、`revision`、`confirmation_digest`、`decision`、`input`、`idempotency_key`。审批需要独立的 `run:approve` 权限；`respond` 补充符合 schema 的输入，`approve/reject` 不修改参数，`verify` 只发起来源状态核查。

写工具固定 `effect_type`、`idempotency_policy="source_key"`，并配置 `write_policy.status_tool_version_id`、`max_submissions`、`max_checks`。查询工具必须是冻结的只读依赖，接收 `{ "operation_key": "服务端生成的稳定标识" }`，结果 `data` 必须包含同一个 `operation_key` 与 `outcome`：

- `SUCCEEDED`：同时提供 `result`，其结构为 AdapterResult，至少含 data、source_request_id、source_version、observed_at；仍需通过本地输出与证据校验。
- `NOT_EXECUTED`：来源明确未执行，允许以相同幂等键重新发起一次人工审批。
- `UNKNOWN`：继续等待显式核查，不自动重提。无效成功回执、来源断连或核查失败也保持未知。

适配器接收服务端 `operation.idempotency_key/confirmation_digest`，模型不能覆盖。达到核查上限、取消或截止后保留已有尝试和用量，终态不等于撤销已经发生的外部事实。评测不能执行真实写工具。

## Skills 脚本与 MCP stdio

`CREATIVITY_SANDBOX_PROFILES` 是 JSON 数组；`CREATIVITY_SANDBOX_DOCKER` 可配置 Docker 客户端绝对路径。每项包括：

| 字段 | 配置 |
| --- | --- |
| profile_id、name、channels | 执行配置标识、页面名称、允许渠道 |
| image | 固定 `sha256:…` 或 `仓库@sha256:…`；运行时不拉取 |
| mode、command | python 配置使用 `["python3", "-I", "-B"]`；stdio 使用镜像内已审阅入口 |
| cpu、memory_mb、pids | CPU 0.1–4 核、内存 32–1024 MiB、进程数 8–128 |
| seconds、input_bytes、output_bytes | 1–120 秒；输入/输出各最多 2 MiB |
| credential_env | 可选，仅 stdio 可声明一个凭据变量；计算脚本不注入服务凭据 |

固定 Python 镜像构建材料在 `deploy/Dockerfile.sandbox`，配置前先在实际执行主机准备镜像并记录摘要。默认禁止网络、非 root、只读根目录、丢弃 capabilities、禁止提权、16 MiB 临时目录；脚本和输入从 stdin 传入，不挂载宿主目录。执行结束或取消删除容器，Worker 周期扫描回收超过绝对期限的带平台标签容器；Docker 清理失败会明确报错。必须运行现有 Worker 与 scheduler，主机故障时仍需运行环境自身的容器恢复措施。

技能普通加载只提供脚本说明。实际计算在“工具”中新建 sandbox 来源工具，选择已冻结技能版本、`.py` 文件与执行配置，固定输出 schema 后通过统一 run 测试。`/admin/v1/tool-execution-options` 返回当前授权可选项与配置摘要。stdio 连接 endpoint 为该接口返回的 `sandbox://profile_id/digest`；切换配置或镜像后摘要变化，旧绑定拒绝执行。

## MCP OAuth

`CREATIVITY_MCP_OAUTH_PROFILES` 配置 profile_id、name、channels、resource、authorization_endpoint、token_endpoint、redirect_uri、client_id、client_secret（可选）、scopes。resource 必须等于所绑定 MCP 连接 endpoint；回调地址在提供方固定登记，管理页面可使用 `/mcp-connections` 的回调处理。

同时配置 `CREATIVITY_MCP_DESTINATIONS` 中 purpose 为 `oauth` 的授权/令牌地址，以及 MCP 地址原有目的地；密文使用 `CREATIVITY_MCP_KEY_VERSION` 和 `CREATIVITY_MCP_ENCRYPTION_KEYS`。密钥映射值是 Base64 编码的 32 字节主密钥，由服务端秘密配置提供。

页面“鉴权”选择当前身份或当前数据域服务后前往授权。授权码带 PKCE S256、十分钟一次性 state，绑定发起身份、Scope 与连接修订。用户凭据与服务凭据独立；用户授权明确撤销后不会回退借用服务凭据。刷新在事务外执行并由 nonce 租约互斥；刷新结果未知、轮换响应丢失或进程中断要求重新授权。流程过期/失败、授权替换、撤销会清理不再使用的密文；授权与撤销有审计。

## 调度、批量与分析

“业务接入 → 运行与事件”提供定时运行、事件投递、批量运行与外部告警。配置需 `integration:manage`；执行前按原身份重新进行统一运行授权。

定时支持固定间隔（至少 60 秒）或每日时刻加 IANA 时区。DST 不存在的时刻跳过，重复时刻只取第一个窗口；迟于窗口 60 秒跳过未生成的旧窗口，不集中补发。稳定窗口标识及条目幂等阻止重复 run。暂停停止新派发，已经受理的任务仍通过执行中心取消。周期条目和批次内容都与运行删除图关联。

批次每次最多 100 项、总正文 1 MiB，每项包含 `event_id` 与 `request`（agent_code、input 等统一 RunInput 字段）。相同 Scope/执行身份/event_id 跨批去重，相同事件不同内容返回冲突；整体请求用 `Idempotency-Key`。单项受理失败可重试；批次取消可同时取消已关联运行。

只读分析工具须来自 MCP，设置 `analysis_policy.rows_path` 和 `max_rows`（最多 10000）。工具现有 timeout/max_result_size 同时控制时长与字节数。部分结果必须带 coverage/truncated/has_more 信息；缺失值不填零。SQL、指标定义与领域公式归业务 MCP；平台通用 Python 计算走前述隔离执行器。

## Webhook 与告警

配置 `CREATIVITY_AUTOMATION_DESTINATIONS`、`CREATIVITY_AUTOMATION_KEY_VERSION`、`CREATIVITY_AUTOMATION_ENCRYPTION_KEYS`。白名单条目包含实际 channel_id、environment、purpose=`webhook`、host、port、scheme；访问内网必须显式设置 allowed_networks。主密钥格式与 MCP 相同，端点签名密钥通过管理页面录入（至少 32 字符）。

正文为 UTF-8 规范 JSON，只发最小状态信息。签名验证：`HMAC-SHA256(secret, timestamp + "." + 原始正文)`；请求头为 `X-Creativity-Event-Id`、`X-Creativity-Timestamp`、`X-Creativity-Signature: v1=<hex>`。接收方校验签名和时间窗口，按 event_id 去重。每轮最多六次自动投递，指数退避；3xx 不跳转，4xx 除 408/429 外终止。人工重投保留原 event_id 和累计尝试数，每轮自动计数重新开始。

运行事件包含 run_id、state、occurred_at、status_path。status_path 是原管理身份在平台内查询用的相对路径，接收方仍需持有获授权的平台身份；事件不携带 Token 或运行原文。

告警端点须同时订阅 `alert.triggered` 与 `alert.resolved`。可监测原有预算阈值转换、运行失败次数、清理失败、运行终态事件投递失败。非预算告警按窗口计数，每次“正常→触发→恢复”保持可追溯 generation；事件先持久化，端点停用竞态不丢待投递事件。预算告警复用预算账本已有 transitions，规则重新启用后会补齐其创建以来尚未入队的转换，不重算费用。

## 外部身份与角色

`CREATIVITY_IDENTITY_PROFILES` 每项配置 profile_id、name、scope（固定渠道/环境/数据域）、issuer、audience、endpoint、client_id、client_secret、subjects（外部 sub 到已有平台账号标识的映射）；`CREATIVITY_IDENTITY_DESTINATIONS` 登记 purpose=`identity` 的服务端凭据校验端点。

初版使用受信提供方 Token introspection 接口，验证 active、iss、aud、exp、nbf、sub，再复核既有账号、初始密码状态及成员范围。没有自动开户或自动提升权限；登录页提供外部凭据交换。平台仍签发 Redis 随机 Token，有效期不超过上游期限；切换渠道不能越出该来源固定渠道，退出沿用平台撤销机制。当前不包含通用 OIDC 浏览器重定向/自动发现。

“成员与权限 → 角色”编辑渠道自定义角色的动作上限与启停状态；内置角色（含 platform_admin 标识）不可改写。服务端按当前操作者、受影响成员和已有授权限制变更，停用立即影响后续授权；页面显示影响成员数。动作上限仍与资源/数据授权取交集。

## 供应商账单

“用量 → 供应商账单”导入规范 JSON 行：line_id、request_id、occurred_at（带时区）、amount（十进制字符串）；每版最多 1000 行/1 MiB。导入另选模型连接、币种、开始/结束时间、来源名称与版本。相同来源版本同内容幂等，不同内容冲突。

核查按真实发送尝试的 source_request_id/connection/time/currency 匹配，时间误差允许 300 秒；范围最多 10000 次实际尝试，过多需缩小时间。区分重复、冲突、平台/供应商缺失、时间/币种不符、缺价、暂估、金额差异与迟报；重新核查读取当前账本。金额不使用浮点，跨币种不相减，导入不会写回平台 usage 或生成支付、发票。

## SDK 与当前外部验证边界

Python / TypeScript 源码与接入示例见 [SDK 文档](../../sdks/README.md)。本次已验证协议、服务层、真实 PostgreSQL/Redis 和 Docker 隔离；MCP OAuth、外部身份、Webhook 接收方与供应商响应以受控夹具验收。真实第三方账号、账单、域名、网络和密钥尚未配置联调；Docker 正式镜像及生产限额也需按实际环境验证。SDK 未发布到外部包仓库。
