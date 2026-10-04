> 后续状态（2026-10-05）：本文保留前一轮查询优化记录。运行受理的并发锁、配置页面、快照与历史配额查询已继续优化，最新结果见 [受理优化修复报告](admission-optimization-20261005.md)。下文受理 P95 3,562.318 ms 属于修复前基线。

本轮按 [全局规则第 8、9 条](../../rule.md) 排查并修复查询放大与完整业务流程嵌套复用。对照服务端起点 `218378d`，涉及 17 个业务模块及公共数据库、鉴权、删除图、版本和锁能力；这里按模块列举，不把函数调用点数量当成独立 Bug 数量。

| 模块 | 已修复的入口与问题 |
| --- | --- |
| [Agent](../src/creativity_service/modules/agents/services.py) | `list_agents/detail/options`：列表不再逐行生成未使用的操作按钮；批量读取环境状态、版本和父资源。依赖图按层读取，锁内共享授权数据。 |
| [提示词](../src/creativity_service/modules/prompts/services.py) | 列表、详情、版本、引用、发布记录及调试列表：批量读取版本、映射、来源名称和运行证据。 |
| [工具](../src/creativity_service/modules/tools/services.py) | 列表、详情、影响范围、执行选项：共用版本、引用与调用计数；工具结果的来源及证据批量校验、登记。 |
| [Skills](../src/creativity_service/modules/skills/services.py) | 列表、详情、工具和 Agent 选项：批量解析依赖。详情只返回版本摘要，选择版本后读取对应包；不再下载全部历史包。 |
| [MCP](../src/creativity_service/modules/mcp/services.py) | 导入工具状态批量读取连接、发现快照、凭据及 OAuth 状态；检查历史、发现历史在 SQL 中限量，差异比较只取相邻版本。 |
| [模型](../src/creativity_service/modules/models/services.py) | 模型/连接/路由/测试列表及历史：批量读取供应商、连接、权限与删除状态，测试列表不再调用完整详情。 |
| [会话](../src/creativity_service/modules/conversations/queries.py) | 列表与消息页：按页查询，再批量加载轮次、运行、产物和删除状态；不再逐消息查询关联数据。 |
| [运行](../src/creativity_service/modules/runs/queries.py) | 列表、筛选项、事件、轨迹、结果、详情和中断操作：SQL 分页与投影，批量读取关联内容并复用本次读取权限。 |
| [记忆](../src/creativity_service/modules/memory/queries.py) | 列表、详情、主体与整理任务：批量读取来源、当前版本、工具证据及来源权限；跨主体使用每条记录的准确范围。 |
| [评测](../src/creativity_service/modules/evaluations/datasets.py) | 样本集/任务列表、版本、报告、样本复制和批量导入：不再逐项调用详情；批量检查来源、权限与版本，批量登记样本、夹具和来源图。 |
| [渠道](../src/creativity_service/modules/channels/services.py) | 渠道列表通过目录关联主档；环境、数据域、客户端、Key 和概览复用授权及名称数据；工作区目录批量核对成员。 |
| [IAM](../src/creativity_service/modules/iam/presentation.py) | 成员、授权、审计和授权页选项：账号、角色、资源名称与范围批量读取；一次身份验证内部复用已读取账号。 |
| [接入与自动化](../src/creativity_service/modules/integrations/automation.py) | 委托 Key 列表批量读取客户端及后继 Key；批任务列表批量读取子项与删除状态，不再逐项进入详情流程。 |
| [预算](../src/creativity_service/modules/budgets/services.py) | 多条预算策略共享同一事务内账本读取，按适用时间窗及有效并发占用收窄查询；避免每条策略重扫账本。 |
| [用量](../src/creativity_service/modules/usage/management.py) | 预算列表批量解析范围名称、计算曝光；供应商账单列表在 SQL 中限量。 |
| [Runtime](../src/creativity_service/modules/runtime/storage.py) | Agent 目录、提示词调试证据、工具证据与运行输入来源批量读取或登记。 |
| [数据清理](../src/creativity_service/modules/data_lifecycle/services.py) | 记忆清空关联任务、清理步骤、回执与进度批量查询，复用当前读取授权。 |

公共能力新增带范围的分批读取和批量插入；所有记录继续由服务层校验。删除图按层遍历并批量判定，没有删除标记时避免无意义的来源遍历。多个事务锁按原有统一顺序通过一条 SQL 取得，未减少锁或改变互斥范围。

读取授权只用于当前调用，并绑定身份、渠道、环境、数据域及允许收窄的主体。写入事务重新读取锁内授权；SSE、实际下载、Worker 恢复和真正外部执行的复核继续独立执行。批量来源登记在当前事务内重新检查删除屏障，任一失败整批回滚。

前端 Agent 列表仅在打开新增窗口时请求依赖选项；技能详情按所选版本请求正文，并处理加载、失败、重试和切换状态。OpenAPI 与前端类型同步更新。

计数对照为同一本地 PostgreSQL、Redis 验收数据（4 渠道，每渠道 10 个 Agent），在服务方法边界计数：

| 一次返回 10 个 Agent | 修复前 | 修复后 |
| --- | ---: | ---: |
| SQL 总数 | 1,307 | 10 |
| 身份复核 | 62 | 1 |
| 账号读取 | 247 | 1 |
| 成员关系读取 | 309 | 1 |
| 需读当前身份的授权调用 | 61 | 1 次读取授权数据 |

修复后仍执行 11 组内存权限计算（10 条记录及新增入口），没有取消授权。HTTP 入口自身的身份认证不计入上表的服务层统计。SQL 总数下降约 99.2%；运行受理服务在同一夹具中由 455 条降至 146 条。探针只证明查询次数，不用探针耗时替代性能验收。

无探针的时延复测沿用原验收：4 个渠道，每渠道 10 个 Agent，20 并发，每类预热 20 次、正式采样 100 次。使用本地真实 PostgreSQL（127.0.0.1:55432）、Redis（127.0.0.1:56379）；管理请求走 TCP，模型调用次数为 0。复测时没有其他回归进程，源码摘要在测量期间保持一致。

| P95 | 历史基线 | 本轮 | 目标 | 结果 |
| --- | ---: | ---: | ---: | --- |
| Agent 管理查询 HTTP | 7,317.502 ms | 383.813 ms | ≤ 1,000 ms | 通过 |
| 从已认证上下文直接受理 | 5,970.125 ms | 3,562.318 ms | ≤ 500 ms | 未通过 |
| 包含主体复核的业务受理 HTTP | 见历史原始记录 | 7,141.514 ms | 单列总时延 | 包含真实 TCP MCP 主体复核，不能直接等同纯平台开销 |

**受理性能 P1 仍未关闭。** 另做的 20 并发诊断中，事务锁获取 SQL 占每个受理请求服务时间的比例平均为 81.6%。两次加锁分别出现在冻结候选和最终受理，锁集合包含系统 IAM 策略锁、渠道内容图/用量锁，最终事务还包含平台配额锁。探针定位到锁集合，未将等待归因到某一个具体锁。后续需要缩短持锁事务或细化互斥范围，并继续验证额度、撤权和删除的并发正确性；本轮不能宣布整体性能验收通过。

验证先执行全量服务端套件，得到 **600 通过、13 跳过**；随后针对新增和后续修改分别补跑：查询及锁回归 25 通过、边界回归 207 通过、批量写入及运行回归 126 通过/11 跳过、身份与渠道回归 63 通过、渠道概览 3 通过。以上分组有重复，不相加。跳过项是需显式启动的组合/性能验收及未配置固定镜像的隔离执行测试；性能验收在本轮另行执行并如实记录失败。

新增回归验证了列表从 1 条增至 10 条时 SQL 数不增长、跨主体读取隔离、下一请求 Token 撤销生效、技能历史包按需读取、批量锁互斥与回滚、批量来源登记的删除阻断和整批回滚。Ruff、格式检查、Mypy（312 个源文件）通过；前端 API 一致性、类型、lint、构建和 19 项单元测试通过，相关浏览器 15 个不同用例通过。浏览器使用路由夹具，不作为真实后端联调证据。

证据：[原始时延样本](acceptance/query-fixes-20261004/performance.json)、[SQL 计数](acceptance/query-fixes-20261004/service-query-counts.json)、[锁等待分解](acceptance/query-fixes-20261004/admission-lock-profile.json)、[验证汇总与复现命令](acceptance/query-fixes-20261004/verification.json)。历史 `docs/acceptance/performance.json` 保留原状。技能详情的版本摘要契约与前端按需读取需同步发布。
