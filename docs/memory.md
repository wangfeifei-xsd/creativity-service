# 13 结构化记忆管理交接

对应 [执行方案](../../代码编写执行方案/13-结构化记忆管理.md) 与 [MEM 需求](../../需求文档/08-记忆管理.md)，开发规范引用 [rule.md](../../rule.md)。本单元交付 P0 属性检索；pgvector 在 P1 接入。

## 实现入口

服务位于 `modules/memory/`，应用装配为 `app.state.memory`；前端位于 `creativity-web/src/features/memory/`，导航为“记忆管理”。`0013_memory` 接在 `0012_conversations` 后，落地六张记忆基线表及 `memory_deletion_jobs`。冻结字段、中文注释、索引与机器归档一致。

| 能力 | 入口及行为 |
| --- | --- |
| 列表、详情 | `GET /api/v1/memories`、`GET /api/v1/memories/{id}`；管理端使用 `/admin/v1`；列表有绑定范围与筛选条件的稳定游标 |
| 明确保存、修正 | `POST /memories`、`PATCH /memories/{id}`；修正携带 `revision`，冲突返回 409 |
| 候选确认 | `POST /memories/{id}/confirm`，携带 `revision`；已撤销或已替代对象不可重新启用 |
| 遗忘、清空 | `DELETE /memories/{id}`、`POST /memories/clear`；返回持久化清理任务，立即阻断使用 |
| 主体开关 | `GET/PUT /memory-preferences`，更新携带 `enabled/revision`；关闭保留数据，清空不改开关 |
| 清理进度 | `GET /memory-deletions/{id}`，允许重新打开页面查询 |
| 管理主体定位 | `GET /admin/v1/memory-subjects`；通过已有会话或记忆的 `anchor_id` 定位主体，服务端复原并验证其完整范围 |
| 渠道与 Agent 策略 | `GET/PUT /admin/v1/memory-policy`；可选 `agent_id` 指定当前渠道已有 Agent；更新携带 `revision` |

业务入口只使用 18 已验签的主体委托。`anchor_id` 不能扩大业务身份范围；管理身份也只能恢复当前环境、数据域内的主体。请求正文不接受渠道、主体、可信度或来源声明。来源标题逐资源授权，来源不可见时返回“来源名称不可用”。页面不回退显示内部标识。

新增动作：`memory:write`、`memory:delete`、`memory:preferences`，与既有 `memory:read` 分开；读取仍要求 `data:read_sensitive`。渠道策略要求 `channel:manage`。既有接入服务、Key 和资源授权需要明确添加所需新动作，本单元不自动扩权。

## 属性、状态与策略

`validation.ATTRIBUTES` 是服务端属性白名单，当前包含常玩游戏、偏好玩法、通常预算（人民币整数区间）、常用时段和会员等级。前四项为偏好，会员等级为权威事实示例属性；新增业务属性需在此登记类型、中文名称与严格结构。自由字段、额外 JSON 字段、实时价格/余额/库存和凭据类内容不能进入通用记忆。

渠道未配置时采用服务层默认：允许偏好与事实，读取/建议写入开启，明确保存可生效，最长 180 天，主体 100 条、每次检索 10 条。主体未设置时为启用；**Agent 未单独声明时禁用读取和建议写入**。Agent 配置不能扩大渠道类型、能力、写入方式及数值上限；运行时再次与最新渠道策略求交集。

`TASK_ONLY` 不落库，`INFERRED` 生成 `PROPOSED`，明确保存按策略激活。冲突按确认、来源等级和观测时间比较；无法覆盖现有依据时保留候选，明确确认或修正可替代现有值。同值追加独立来源，同来源重试去重。旧值变为 `SUPERSEDED`，到期变为 `EXPIRED`，失去有效来源或请求遗忘变为 `REVOKED`。

主体锁同时覆盖所有属性、条数上限、偏好开关及清空；内容图锁协调来源删除与派生写入；渠道策略锁协调策略更新。历史版本只保存序号、状态、操作者、固定原因与来源引用，`value` 始终显式为空，不提供旧值恢复入口。

## 向 16、17 交接

`ports.MemoryRuntimePort` 给出类型化端口：

- `validate_agent_policy(context, policy)`：16 在配置/发布校验时调用；`set_policy(..., agent_id)` 保存显式策略。17 使用持久化 run 的 Agent 定位当前策略，不能以模型参数指定 Agent 或范围。
- `write_candidate(context, run_id, CandidateInput)`：17 提交偏好候选。`intent` 必须来自编排层对真实用户请求的判断；来源为完整 Scope 内已有用户消息，`source_version` 为消息序号。模型推断必须使用 `INFERRED`，任务条件使用 `TASK_ONLY`。
- `write_fact(context, run_id, key, ToolResult)`：值从工具证据定位的字段提取。工具层登记结果内容摘要，本模块比对同 run 的成功调用、实际结果摘要、证据字段、版本、范围及当前有效性。候选接口和 HTTP 明确创建不能伪造事实背书。此前没有结果摘要的旧调用需重新通过工具查询；不补造权威数据。
- `select(context, run_id, keys, current_keys)`：先限定完整 Scope 再查询属性，记录选择原因和版本，仅返回引用。当前任务明确指定的属性排除在历史召回外。
- `load(context, run_id, selection, current_keys, required_fact_keys)`：在模型调用前执行，核对当前开关、策略、来源、版本、有效期和撤销标记。必需事实返回 `required_tool_keys`，由 17 通过业务工具获取；关闭长期记忆不影响 12 的会话上下文。

P0 不缓存记忆值；如 17 需要缓存引用，使用 `reference_cache_key`，它包含渠道、环境、数据域、主体类型/编号、Agent 和属性集合。缓存或队列中的引用不能直接作为事实，需要重新 `load`。关闭再开启会使先前选择失效；清空、遗忘和开关变化前已排队的候选不能自动写回。

检索/来源设施故障按 Agent 的 `OMIT/FAIL` 策略处理；降级返回 `warnings` 并写检索记录或脱敏运行日志。身份、范围及引用不匹配不被吞掉。17 必须合并这些 warnings 到最终结果，并在真实模型交付前调用加载端口。运行本身或公共授权/数据库前置不可用时仍拒绝执行，不能以记忆降级绕过公共检查。

## 向 25 交接

沿用公共 `deletion_markers` 和 `source_links`。会话来源直接登记为 `message → memory`，已有会话删除预览可以区分独占与独立来源。每次检索、详情或清理先分别复核所有来源；撤销失效来源并移除其当前图边，有独立来源则保留记忆，完全失去依据则清空值并撤销。

`build_memory_service` 向公共清理注册表登记 `memory → clean`。25 消费 `memory_deletion_jobs`，逐个调用 `clean`，或者在会话传播图上调用相同处理器；`reconcile_source(context, ContentRef)` 可按已删除来源重新计算当前主体的记忆。单项遗忘和清空的标记、撤销、值清除、历史脱敏及清理意图同事务提交；排队自动写入也检查删除意图的时间边界。

本模块本地清理完成后进度为 `WAITING_PROPAGATION`。只有 25 核对全图、外部缓存/索引、快照及恢复账本后才可标记 `COMPLETED`；本单元不把本地清理冒充完整删除与备份恢复验收。空清空无内容需要传播，可直接完成。

关键验证与范围见 [验收记录](memory-validation.md)。
