# 运行、会话与记忆

API、Worker 与 Beat 共同提供持久化受理、执行、恢复和交付。启动方式见 [本地开发](local-development.md)，业务调用见 [接入指南](integration.md)。

<a id="runs"></a>
<a id="runtime"></a>

## 任务执行

受理固定 Agent 发布版本、依赖、输入、身份和执行限额，同一事务保存运行、幂等、预算准入、会话占用和投递意图。Worker 从持久化记录恢复身份和当前授权；消息只是执行唤醒。

模型或工具调用按步骤创建独立 Attempt，调用前复核取消、授权、删除、租约、时限和预算。成功响应与尝试一同保存，恢复优先复用已保存响应；已发送但结果未知的模型调用不自动重发。终态不会因迟到用量或响应反转。

| 开发端口 | 用途 |
| --- | --- |
| `RunService.admit_run` | 已认证上下文、业务输入和幂等键受理 |
| `claim_lease/heartbeat` | 领取与续租，受 deadline 和当前状态限制 |
| `start_step/start_attempt/mark_sent` | 准备执行并持久化发送意图 |
| `finish_attempt/commit_step/finish_run` | 保存响应、步骤、结果及终态 |
| `get_run/list_runs/trace/events` | 查询与事件交付 |
| `cancel/rerun` | 请求取消，或创建关联原运行的新任务 |

`finish_run` 的 `commit_result(uow)` 可在成功事务中回写模块数据，所需锁通过 `commit_keys` 声明。端口定义见 [运行服务](../src/creativity_service/modules/runs/)，字段见 [运行模型](data-model/modules/runs.md)。

运行详情展示步骤、实际模型输入、依赖、证据、用量完整性与产物。轨迹需要 `run:read`，原文与事件另需内容权限。Token 自然到期或连接断开不会取消任务，当前账号、渠道、Key 或源主体权限失效会阻止后续执行。

<a id="runtime-sse"></a>

## SSE

业务路径为 `GET /api/v1/runs/{run_id}/events`，管理路径为 `/admin/v1/runs/{run_id}/events`。使用带 Authorization 的流式请求；业务请求同时签署委托，Token 不放入 URL。

每个运行事件有递增 `sequence`，SSE `id` 使用该序号。重连携带 `Last-Event-ID` 或 `after_sequence`，请求头优先。客户端去重；事件缺口或 410 `EVENTS_EXPIRED` 时回查原运行快照。

`text_delta` 为未校验片段，`result` 才包含经过输出 schema 校验的结果，`completed` 表示技术终态。默认事件保留 24 小时，每 15 秒发送心跳。身份失效后发送无序号 `control` 并关闭，重新认证后可查询原运行。

响应使用 `text/event-stream`、`Cache-Control: no-store`、`X-Accel-Buffering: no`；代理需要支持流式转发。客户端解析示例见 [后端客户端](../examples/backend/client.py)。

<a id="conversations"></a>

## 会话

会话绑定已发布且允许会话的 Agent。消息以 `client_message_id` 去重，同一会话的不同请求同时受理返回 `SESSION_BUSY`；重发保持消息标识与内容。轮次、消息和运行通过受理事务关联，结果与片段投影受同一租约保护。

上下文读取近期消息和有效摘要，附件、导出及历史均复核当前权限与来源。会话归档控制状态，删除沿来源图传播；删除处理见 [运维指南](operations.md#data-lifecycle)。

<a id="memory"></a>

## 记忆

| 层级 | 内容 |
| --- | --- |
| 会话 | 当前消息、上下文与摘要 |
| 归档 | 后台整理已完成轮次，保留全部来源依赖 |
| 人物画像 | 通用或渠道配置的偏好与事实；模型推断先待确认 |

通过 `/admin/v1/memory-policy` 配置画像属性与后台整理策略。Beat 每 60 秒扫描，默认会话空闲 1800 秒后按完整轮次整理，每批最多 20 条消息。生成复用原 Agent 冻结模型路由、统一运行和用量；事实来源仍需受控证据。

关闭长期记忆暂停整理及跨会话读取，清空则删除长期内容并阻断旧消息再次生成。`/memory-consolidations` 查看后台任务，失败后可显式重试；限流等待与生成失败次数分别处理。来源删除后对应归档、画像候选、向量及恢复内容失效。

属性及接口字段见 [记忆契约](../contracts/internal/memory.json)，可选向量环境见 [部署说明](../deploy/vector.md)。
