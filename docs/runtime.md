# 运行、会话与记忆

API、Worker 与 Beat 共同提供持久化受理、执行、恢复和交付。启动方式见 [本地开发](local-development.md)，业务调用见 [接入指南](integration.md)。

<a id="runs"></a>
<a id="runtime"></a>

## 任务执行

受理固定 Agent 发布版本、依赖、输入、身份和执行限额，同一事务保存运行、幂等、预算准入、会话占用和投递意图。Worker 从持久化记录恢复身份和当前授权；消息只是执行唤醒。

模型或工具调用按步骤创建独立 Attempt，调用前复核取消、授权、删除、租约、时限和预算。成功响应与尝试一同保存，恢复优先复用已保存响应；已发送但结果未知的模型调用不自动重发。终态不会因迟到用量或响应反转。

模型输出校验失败时，有限修复携带上一轮原文和具体校验错误，重新检查上下文及预算。失败正文与尝试原子保存，恢复沿用实际请求和修复反馈；失败正文不作为成功结果，已发生用量仍正常结算。

模型步骤的业务输入严格使用该步骤的输入映射，不自动附加全部前序步骤输出或完整工具响应。任务指令、绑定提示词及技能、显式启用的会话与记忆按原配置加载；提示词中显式声明的工具变量仍可引用对应步骤结果。模型主动调用工具时，仅在当前步骤的循环内保留成对的调用与返回消息，步骤完成后清除，后续步骤通过输入映射选择所需结果。完整工具响应及来源证据继续保存在运行轨迹中，未映射给模型的证据不能要求模型自行补全。

`bindings.skill_loading[].step_keys` 可限定技能生效的模型节点，同名变量读取节点映射入参，必填类型在受理时校验。没有指定节点的旧配置继续读取智能体运行入参。脚本、工具和计算节点不可选择；技能能力要求按选中节点的路由检查，实际加载记录仍按运行步骤保留。

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

<a id="node-python"></a>

## 节点 Python 处理与全量分页

流程支持独立的 `kind=script` 脚本处理节点，只有一份 `script`（`source`、`profile_id`、`profile_digest`）。不配置模型、提示词、技能、工具、算子或失败收尾工具。映射值先通过 `input_schema`，再交给同步 `transform(value, context)`；函数返回 JSON 对象直接作为节点出参，通过 `output_schema` 后沿连线流转。纯脚本流程可以不绑定任何模型或业务资源。

业务校验失败是可流转的正常结果：建议返回业务结果封装，`data` 包含 `success`、`conclusion` 和 `inputs`，分别表示校验是否通过、具体结论和整理后的入参。成功边用 `data.success == true`，后续可映射整个 `data` 或分别选择状态和结构化入参；失败边可连接 END 或其他节点，保留失败结论。直接结束时须满足 Agent 最终业务结果契约，例如返回 `NEEDS_INPUT`。技术异常、超时、无效 JSON 或输出结构违规则阻断正常流转，不伪装成业务校验失败，也不自动重试。示例见 [入参校验脚本](../examples/agents/node-scripts/validate_input.py)。

`context.inputs` 是本次映射入参，`previous_output` 是本节点上一轮处理结果（首次为 null），`iteration` 从 1 开始。不加载模型、提示词和技能上下文，不提供平台身份、凭据、网络、宿主文件或任意前序输出。需要的数据须通过输入映射声明。执行环境使用固定镜像和受限资源，不能在 API 或 Worker 主进程执行源码。

脚本作为计算步骤保存输入、处理结果、源码摘要和错误，运行详情按内容权限显示；异常原文只保存在获授权的处理记录。源码随版本与运行快照固定，保存、发布和执行前复核环境授权及摘要。环境变化须重新选择，恢复复用已提交的处理结果。

全量分页只用于独立的只读工具节点。`pagination` 声明 `cursor_input`、`items_path`、`has_more_path`、`cursor_path`，可选 `count_path`（本页条数）与 `total_path`（全部条数）。路径来自原始业务响应；续页入参是顶层字段，首页为空或省略。`max_items` 默认 1000、最大 10000，累计结果最多 1 MiB；循环、工具次数和时长同时受运行上限约束。平台保持查询条件，持续翻页至末页才进入后续节点。

分页工具最终输出 `complete`（布尔）、`received`（累计条数）、`page_count`（页数）、`total`（总条数或 null）和 `pages`（全部原始业务响应数组），节点输出结构须声明此聚合结构。每页原始工具契约独立验证。后续脚本通过映射取得这些页面及业务租户，确定性检查租户、项目、规格并计算，参考 [项目校验脚本](../examples/agents/node-scripts/validate_projects.py)。

查询失败、响应截断、列表和条数不符、续页标记错误、游标缺失或重复、总条数不一致均阻断正常流转。未完成即达到次数、耗时、条数或大小上限时，明确返回数据不完整；配置 `failure_policy=partial` 且 Agent 结构允许时返回 `PARTIAL`、`data.complete=false` 和原因，否则失败。整体 deadline 先到时返回超时及未确认完整的说明，后续脚本不会产生全量最优结论。

<a id="runtime-sse"></a>

## SSE

业务路径为 `GET /api/v1/runs/{run_id}/events`，管理路径为 `/admin/v1/runs/{run_id}/events`。使用带 Authorization 的流式请求；业务请求同时签署委托，Token 不放入 URL。

每个运行事件有递增 `sequence`，SSE `id` 使用该序号。重连携带 `Last-Event-ID` 或 `after_sequence`，请求头优先。客户端去重；事件缺口或 410 `EVENTS_EXPIRED` 时回查原运行快照。

`text_delta` 为未校验片段，`result` 才包含经过输出 schema 校验的结果，`completed` 表示技术终态。默认事件保留 24 小时，每 15 秒发送心跳。身份失效后发送无序号 `control` 并关闭，重新认证后可查询原运行。

响应使用 `text/event-stream`、`Cache-Control: no-store`、`X-Accel-Buffering: no`；代理需要支持流式转发。客户端解析示例见 [后端客户端](../examples/backend/client.py)。

<a id="conversations"></a>

## 会话

会话绑定已发布且允许会话的 Agent。消息以 `client_message_id` 去重，同一会话的不同请求同时受理返回 `SESSION_BUSY`；重发保持消息标识与内容。轮次、消息和运行通过受理事务关联，结果与片段投影受同一租约保护。

上下文读取近期消息和有效摘要，导出及历史查询复核当前业务权限与来源。附件经通用上传取得 URL，消息只保存 URL；文件流程见 [OSS 说明](../deploy/oss/README.md)。会话归档控制状态，业务删除沿来源图传播；删除处理见 [运维指南](operations.md#data-lifecycle)。

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
