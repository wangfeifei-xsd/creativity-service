# 11 任务受理与可靠调度交接

实现日期：2026-10-02。依据 [方案 11](../../代码编写执行方案/11-任务受理与可靠调度.md) 与 [RUN 需求](../../需求文档/12-执行记录与任务运行.md)。数据库模型见 [运行模型](data-model/modules/runs.md)，故障注入与检查见 [验收记录](runs-validation.md)。

本单元提供持久化受理、调度、租约、状态、尝试、恢复、查询与取消/重新执行服务。17 已装配正式 Agent 解析、模型/工具适配、LangGraph 和 SSE，并开放运行业务路由；当前装配与验证见 [运行编排交接](runtime.md)。缺少解析器、会话钩子或执行器时仍拒绝对应操作。模块调试由服务端创建冻结测试描述，不存在客户端上传执行定义的入口。

## 装配与调用

`modules/runs/services.py:RunService` 汇总受理、执行和查询服务；`assembly.build_run_service` 接入 IAM 资源读取器与删除清理器。`api.py` 的业务和管理 router 已挂载，独立 OpenAPI 位于 `contracts/runs/openapi.json`。默认 `/api/v1/runs` 受理返回 202；sync 默认等待 15 秒，超时返回原 run，流式协议见 [SSE 契约](runtime-sse.md)。

| 接口 | 用途与调用要求 |
| --- | --- |
| `admit_run(context, request, key)` | 认证后的上下文、业务输入与幂等键。`DefinitionResolver` 在事务前解析服务端发布定义，输入按冻结 schema 验证 |
| `claim_lease(TaskEnvelope, worker_id)` | 消息只有渠道与运行标识，身份从运行恢复；已有有效租约或终态返回空 |
| `heartbeat(lease)` | 续租不跨越 deadline；取消或超时收敛终态后返回空 |
| `start_step` / `start_attempt` / `mark_sent` | 校验授权、快照、删除、租约、时限与调用限额；模型尝试必须提供 08 的 `AttemptPlan`。`mark_sent` 原子保存发送意图与账本 PENDING，返回真后才能在事务外调用 |
| `finish_attempt` | 成功尝试必须同时提供返回内容，避免尝试成功后崩溃丢失模型结果；失败是否可重试由受信适配器判断；未知模型结果禁止自动重发 |
| `commit_step` / `load_progress` | 受租约保护的步骤、checkpoint 提交与恢复读取；重复提交必须与既有结果、恢复点一致；恢复优先复用已保存结果 |
| `append_event` / `finish_run` | 片段事件与最终结果分开；Agent 的完整 `BusinessResult` 按冻结输出 schema 校验，兼容 11 既有仅定义 data 的内部契约；证据范围必须一致 |
| `get_run` / `list_runs` / `trace` / `events` | 状态结果、筛选列表、步骤/尝试轨迹及单调事件游标。原文/事件需 `run:content`，轨迹元数据需 `run:read`；游标过期返回 `EVENTS_EXPIRED` |
| `cancel` / `rerun` | 排队取消直接终结；运行中登记取消请求；管理 rerun 新建运行、快照、预算准入并保存 parent_run_id |

`ResolvedDefinition` 是内部端口，包含具体版本、输入输出结构、执行限额及准入预算候选。当前默认在线 60 秒、分析 300 秒，单步骤最多重试 2 次、模型 6 次、工具 10 次、租约恢复最多 2 次。实际时限、配置来源、deadline 和执行策略在受理时保存。17 的解析器校验发布内容并生成这些字段，不能使用模型或请求正文覆盖。

18 新增的 `delegation_id` 随原始身份保存；不保存登录 Token、session_id 或历史 granted_actions。后台每个执行边界重新调用当前授权端口，Token 自然过期不撤销运行。正式服务调用还需要 18 的当前主体权限读取器，由 17 在 Worker 装配。Key 轮换不改变幂等范围，原运行执行仍复核其原始 Key 当前状态。

## 会话事务钩子

12 实现 `TurnHooks.keys/admit/finish`。`keys` 在事务开始前一次提供全部锁；`admit` 在受理工作单元中验证 Agent、消息身份与摘要，分配轮次和消息；预算、轮次、run、快照、幂等和 outbox 任一失败都会回滚。`RunRequest.client_message_id` 可携带消息标识，不能单独绕过钩子的消息幂等检查。

会话执行互斥键统一使用 `repositories.conversation_key(scope, conversation_id)`。不同幂等请求同时占用返回 `SESSION_BUSY`；相同请求先命中幂等。`finish` 与预算释放共用终态补偿事务，必须可重复调用，不能发 HTTP 或另开独立事务。运行保留 `resources_released=false` 直到释放事务成功。

## 并发、投递与恢复

幂等范围包含渠道、环境、数据域、主体、稳定 client_id/管理 actor_id 与 Agent 调用编码；不包含可轮换 key_id。异内容冲突返回 409，重放重新授权。记录保存最早清理时间为 24 小时，当前实现保留记录不自动删除，未终结运行始终能重放；最终保留清理由 25 接入。

所有运行及派生写入持有同一 run 锁；受理另含稳定幂等锁、会话锁、预算与快照锁，一次排序取得。事件序号保存在 run 并在同一事务递增，状态变更与对应事件同时提交。P0 严格使用需求状态图，恢复不会把 RUNNING 改回 QUEUED。成功/取消先取得有效提交者获胜，终态不可反转。

`workers/dispatcher` 先短事务声明投递，再在事务外发布；成功未确认、PUBLISHING 超时、已发布未被消费都可以再次唤醒。发布异常仅保存脱敏类别，run 保持可查询的排队状态。`workers/executor` 每条消息独立绑定并清理上下文，自动续租；失去租约或收到取消时停止后续步骤。`workers/recovery` 复核 deadline、快照、当前授权与删除标记，补偿租约失效和终态占用。

Celery 登记 `runs.execute` 和 `runs.sweep`，Beat 每 5 秒扫描持久化渠道目录。部署需同时运行 API、Worker 和 Beat。17 已在 Worker 启动时安装正式 Executor 与会话钩子；服务身份还须注入源系统当前主体授权读取器，缺少时拒绝执行，见 [生产装配](runtime.md)。

已发送未知模型调用保留旧 Attempt、用量 MISSING/PENDING 和预算预占，运行终结为待核实错误；恢复不会自动再产生一次模型调用。明确未发送的占用可释放。只读工具可在有限策略内产生新 Attempt，旧尝试保留。晚到用量经 08 原始 attempt/scope 校验补记，仅修正账本，不改终态。17 的模型执行器按实际供应商事件调用账本结算。

## 存储与后续边界

`0011_runs` 新增运行、幂等、outbox、step、attempt、event、lease、checkpoint、受控内容、恢复和会话占用表。`0019_parallel_runs` 仅汇合 11 与同期 14/15/18 的迁移分支，不改写这些模块的修订。SQLAlchemy 定义、中文注释、普通索引与机器归档一致，独立 schema 已验证升级、降级、再升级和存储审查。

删除先由 25 登记标记；服务当场拒绝读取、恢复与敏感写入，清理器只在存在标记时清除内容和 checkpoint，保留费用及诊断元数据。17 已完成 LangGraph 适配、SSE Token 边界、流式过滤、产物列表及运行页面；pending writes 复用既有 checkpoint 命名空间，不新建框架表。真实供应商兼容与业务发布验收不计入本单元测试通过口径。

## 12 会话装配增量

API 与 Worker 已接入 `ConversationHooks`；`replay/prepare/project/rerun_request` 扩展协议见 `ports.ConversationTurnHooks`。重发先检查会话消息幂等，片段与消息投影在同一有效租约事务写入；管理 rerun 生成新的轮次。正式 Agent 解析与流程执行已由 17 安装。会话、附件及删除接口已由 12 挂载，详见 [会话交接](conversations.md)。
