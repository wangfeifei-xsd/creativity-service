# 12 会话管理交接

实现日期：2026-10-02。依据 [方案 12](../../代码编写执行方案/12-会话管理.md) 与 [会话需求](../../需求文档/07-会话管理.md)，开发规范引用 [rule.md](../../rule.md)。模型见 [会话归档](data-model/modules/conversations.md)，验证见 [验收记录](conversations-validation.md)。

本单元实现会话生命周期、消息与运行原子受理、持久化消息投影、上下文来源快照、删除意图、受控附件与导出，以及 antd 管理页面。正式 Agent 发布解析、模型执行和 SSE 由 16/17 装配；当前应用没有测试 Agent 或模拟回复。缺少正式解析器时创建及新消息返回依赖不可用，已有历史仍可查询。

## 装配

`build_conversation_service` 登记 IAM 会话/产物读取器、运行会话钩子及四类清理器。API 与独立 Worker 均安装 `ConversationHooks`；Worker 的租约恢复及终态补偿可更新历史消息、释放会话占用。新增 `conversation:write` 权限控制创建、标题、发言、归档及恢复；读取仍需会话原文权限，取消还检查原运行执行权限。管理端从已存会话恢复主体并重新授权，不接受正文指定身份。

| 依赖端口 | 后续接入要求 |
| --- | --- |
| `runs.resolver` | 16/17 根据已认证范围与 `agent_code` 解析当前正式 `ResolvedDefinition`；创建时只定位定义，不执行输入。12 同时检查入口版本属于本渠道 Agent；每轮在 11 的受理事务中冻结具体版本 |
| `conversations.directory` | 16/17 提供已授权 Agent 的名称与调用编码；目录未接入时页面不展示可创建选项 |
| `conversations.names` | 18 提供当前主体可读名称；没有可信名称时保持缺失，不以内部编号代替 |
| `conversations.retention` | 生产装配读取 05 的渠道和环境保存策略，在策略互斥事务中取较短期限，默认上限 90 天 |
| `conversations.generator` | 17 的摘要生成必须走统一预算、计量及 run 入口，返回摘要与成功运行标识；缺失即拒绝，不在短事务内调用模型 |

## 路由

以下路径在 `/api/v1` 和 `/admin/v1` 下共用同一服务。业务路径保留 18 的请求绑定委托校验；管理路径验证工作区 Token、动作与数据范围。完整字段见统一 OpenAPI，独立 schema 位于 `contracts/conversations/`。

| 方法及路径 | 行为 |
| --- | --- |
| `POST/GET /conversations` | 创建；按当前工作区、Agent、主体名称、时间与状态筛选，固定创建时间和标识游标分页 |
| `GET/PATCH /conversations/{id}` | 详情、摘要与上下文记录；带 revision 改标题 |
| `POST/GET /conversations/{id}/messages` | `client_message_id`、正文、可选结构化业务 input、受控附件；按消息序号及首次查询上界稳定分页，返回对应轮次结果 |
| `POST /conversations/{id}/archive`、`restore` | 归档禁止新消息，恢复后继续原顺序；相同历史消息的重放仍返回原关系 |
| `GET /conversations/{id}/runs/{run_id}`、`POST …/cancel` | 经过会话关联及运行权限校验的执行详情、取消 |
| `GET /conversations/{id}/deletion-preview`、`DELETE /conversations/{id}` | 展示来源影响；标记、取消请求与共享 deletion_jobs 同事务登记 |
| `GET /deletions/{deletion_id}` | 原文不可访问后仍按原范围及删除权限查询进度 |
| `POST /conversations/{id}/attachments?name=…` | 有界读取原始文件正文，复用 03 产物服务登记，不存永久对象地址 |
| `GET /conversations/{id}/attachments/{artifact_id}/content` | 重新核验 Token、会话、文件与来源后交付；管理端恢复正确主体范围 |
| `POST /conversations/{id}/exports` | 受控 JSON 产物，包含会话、历史、结果及来源记录，下载继续经过上述鉴权接口 |

## 运行事务与历史

`ConversationTurnHooks.replay/prepare/admit/project/finish/rerun_request` 扩展 11 的事务钩子。消息重放先于发布解析和占用检查，同一个消息即使换 HTTP 幂等键、断网或后续发布也不会再生成。不同内容返回幂等冲突；不同消息占用会话时返回 `SESSION_BUSY`。预算、快照、轮次、消息、幂等及 outbox 任一失败均整体回滚，无状态运行不写会话。

用户和助手消息预先分配顺序，片段事件只写 `PARTIAL`，系统与工具消息使用同一会话序号。有效终态后才能显示完成、失败或取消，迟到租约结果不改终态。查询会核对 run 的有效终态，因此终态已提交但释放事务中断时也能正确展示，11 的恢复任务完成持久化投影与释放。

每轮保存版本名称、输入/输出契约、不可变业务输入。契约比较只接受可证明兼容的变化，并再次验证历史输入与本轮输入；无法证明兼容时要求新会话。`NEEDS_INPUT` 是成功返回的业务结果，下一消息新建 run，保存来源运行和已确认条件。管理 rerun 同样创建新轮次，原消息不覆盖。

## 上下文与来源

17 调用 `select_context(context, conversation_id, run_id, instructions, max_characters=…, recent_messages=…)`。必要指令、当前正文/附件、业务输入和上一轮追问结果先占容量；超出时直接拒绝，随后按时间选择已完成轮次消息及不重叠的有效摘要。选择器使用字符容量，17 应根据实际模型窗口传入保守限额并在供应商调用前进行 Token 校验。

`context_snapshots` 冻结全部实际消息引用（包含本轮用户消息）、摘要标识/版本及来源集合、选择参数摘要、截断原因和消息序号。重复选择恢复同一快照，修改参数会冲突；删除来源立即阻断再次使用。`memory_refs` 为 13 的接入位置，当前未装配记忆检索。

`record_summary` 是供本地确定性摘要登记的内部服务，没有 HTTP 任意写摘要入口。模型摘要调用 `generate_summary`，经上述受控生成端口执行后再验证来源与成功运行、登记版本。摘要始终保留来源集合与截断记录。13 的记忆来源使用 `message`/`summary` 引用及公共来源图；独立来源在删除预览中区分撤销和更新来源。

## 删除与迁移

迁移 `0012_conversations` 接在现有汇合修订 `0019_parallel_runs` 后，新增五张会话表，并提前实现 25 基线中的共享 `deletion_jobs`，不再新建另一份删除任务。数据库字段、中文注释及索引与冻结基线和机器归档一致。

删除时先在同一事务持有会话、内容图及关联运行锁，提交删除标记、`DELETING`、在途取消和 `PENDING` 清理任务。读取、发言、上下文恢复及受控文件立即拒绝。清理器 `conversation/message/summary/context` 已登记；运行清理继续使用 11 的 `run` 清理器。会话本地原文清除后进入 `WAITING_PROPAGATION`，不会提前报告完成。25 负责派发与重试共享任务、清理 checkpoint/缓存/关联记忆及文件，核验来源图后把任务置为 `COMPLETED`、会话置为 `DELETED`；该组合验收不计为本单元已完成。

前端入口为 `/conversations`，支持列表筛选、创建、版本与消息时间线、结果、取消、标题、归档/恢复、删除预览和独立进度页、附件与导出。未终结运行每三秒刷新持久化快照，保留正在填写的消息；删除进度每五秒刷新。真实 SSE 组件继续由 17 装配。

22 已用真实服务 Token 和主体委托验证会话创建、消息幂等、运行、导出及下载撤权；渠道服务的可选权限补齐 `conversation:write/content:derive`，见 [接入指南](unified-api.md) 与 [验证记录](unified-api-validation.md)。
