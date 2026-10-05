# 模型变更记录

## 2026-10-05：未上线阶段合并初始迁移

将 26 个历史迁移合并为 [0001_initial.py](../../alembic/versions/0001_initial.py)，冻结模型 1.8.0 的完整建库定义；表、字段、中文注释、普通索引及应用模型保持一致。修订号继续使用 `0034_admission_indexes`，已完成旧迁移的开发库无需重建、重新标记版本或再次回填数据。原始迁移和旧数据回填资源随源码备份保留，不再进入当前迁移目录。

`catalog.json` 和模块基线中的历史修订来源继续用于模型归属检查，以下历史变更记录也保持可追溯。新建数据库通过单一基线或完整初始化 SQL 建库；后续新增修订直接接续当前基线。执行及旧开发库衔接见 [初始化说明](../../sql/README.md#初始迁移基线)。

核验结果：新基线与原完整迁移建库结果的 110 张表、1665 个字段和 244 个索引完全一致；开发库升级识别在只读事务中通过，结构及逐表数据摘要未变。数据库备份已恢复到独立临时库核验。64 项相关集成测试、211 项非集成测试及完整工程检查配方通过。

## 2026-10-05：完整初始化 SQL 归档

新增 [sql/init.sql](../../sql/init.sql)，按当前最终结构一次创建 109 张应用表与 1 张迁移版本表、1665 个字段、244 个普通索引及全部中文注释，包含系统渠道初始记录。对应模型版本 1.8.0、迁移基线 `0034_admission_indexes`；此次归档不改变表结构或历史迁移。生成与一致性检查纳入工程命令，空库 SQL 与完整迁移链的等价性由真实 PostgreSQL 集成测试核验。执行方式见 [初始化说明](../../sql/README.md)。

2026-10-03 增量 `0032_run_subscriptions`：`webhook_endpoints` 与 `alert_rules` 增加 `client_ids` JSONB 列，用于显式选择运行通知和失败监测的调用服务。旧记录回填空列表，保留本人运行范围；新配置由服务层校验渠道、环境、数据域与当前权限。旧迁移建表输出不变，新增列没有数据库默认值或业务约束。

| 版本 / 日期 | 原因及影响 | 代码与迁移 | 兼容及数据处理 |
| --- | --- | --- | --- |
| 1.0.0 / 2026-10-02 | 完成全量 P0 统一设计，98 表及认证 KV；明确 04–25 所属与共享关系 | 公共 10 表：core/database/baseline_v0001.json；Alembic 0001_core；框架版本表沿用 02 | 02 只有空迁移链，无业务数据回填。普通 id、可空物理列与显式服务校验；全部索引为普通索引。业务设计表后续分别迁移，不一次性创建 |

| 1.1.0 / 2026-10-02 | 实现四张 IAM 基线表，账号增加 credential_version；新增 iam_revocations 持久化退出与撤销补偿，Token 增加用途/凭据代次/成员修订/数据域/索引和签发毫秒时间 | modules/iam/baseline_v0002.json；Alembic 0002_iam；应用 storage.py 汇总模型；复用 audit_events | 新建表，无既有 IAM 数据回填。Token 用途严格分离；当前代次拒绝旧授权，索引清理可重试。模型共 99 张逻辑表，16 张已实现（含版本表）、83 张待实现；认证 KV 已实现 |

| 1.2.0 / 2026-10-02 | 实现渠道 8 张基线表；主档增加 business_type、接入服务增加 data_scopes；新增 channel_lifecycle_events 供运行与保留模块消费 | modules/channels/baseline_v0003.json；Alembic 0003_channels；受限摘要索引、真实状态读取器与生命周期服务 | 9 张新表，不改写既有 IAM 或历史业务归属。模型共 100 张逻辑表，25 张已实现（含版本表），75 张待实现；系统渠道由显式命令初始化。公共契约 1.2.0 为 ChannelState 增加可选 channel_status。最近使用时间不递增 Key 配置 revision；轮换缩短旧 Key 截止时间且保留历史标识 |

后续修改 catalog.json、关系和不变量后重生成模块归档；当前修订的 baseline_v0001.json 永久冻结，新迁移另建修订文件。检查 `make model-check` 对齐实现字段和归档。26 核验实际 PostgreSQL，27 将模型目录连同契约版本冻结成发布快照。

## 09 提示词实现增量 / 2026-10-02

`0009_prompts` 新增提示词资源、样例和测试三表；版本、引用与环境映射继续使用公共表。测试新增冻结版本、样例快照、渲染内容、执行描述摘要、样例标识、路由名称及受理状态；run/snapshot/rendered 引用在统一运行受理前允许为空，由服务显式写入。样例与测试登记清理类型。字段与迁移定义见 `modules/prompts/baseline_v0009.json`，不改变其他模块既有字段。新建表没有历史数据回填。

## 08 用量与预算实现 / 2026-10-02

`0004_usage` 实现 11 张设计基线表，新增 `platform_quota_occupancies` 与 `usage_exchange_rates`，合计 13 张表。账本补充原始运行来源快照、计量上限、发送状态、有效事件指针及公式；事件保存去重摘要和应用状态；提醒保存状态迁移；导出保存授权范围、文件引用及完整性。预算版本继续存放公共 `resource_versions`，预占固定版本引用。冻结定义为 `modules/usage/baseline_v0004.json`，新建表无既有业务数据回填。

## 07 模型配置实现增量 / 2026-10-02

`0004_models` 实现供应商字典、连接、模型、路由和测试五张基线表。连接与模型增加公共版本引用和验证语义修订，测试保存冻结执行描述、配置摘要、统一运行引用及状态。映射、连接和路由版本、依赖、发布映射均复用公共表；价格仍由 08 独占。测试登记 `model_test` 清理处理器，连接加入配置删除来源类型。字段冻结于 `modules/models/baseline_v0004.json`；无既有模型业务数据回填。与 08 的独立迁移由配置批次合并修订汇总，实际迁移链以 Alembic 为准。

## 10 工具实现增量 / 2026-10-02

`0010_tools` 新增工具资源、调用实例和证据三表；调用新增工具关联、脱敏参数、结果摘要、证据集合及独立 Attempt 状态，拒绝和缓存调用允许没有 attempt_id。版本内容补充模型字段白名单和适用环境，继续复用公共版本/发布/引用存储。调用与证据声明清理类型，缓存仅存 Redis 并受完整授权及来源删除屏障约束。字段定义冻结在 `modules/tools/baseline_v0010.json`；新表不回填其他模块数据。

## 2026-10-02 · 方案 11

新增 `0011_runs` 与运行冻结基线：落地运行、幂等、投递、步骤、尝试、事件、租约及 checkpoint，新增 `run_contents`、`run_recoveries`、`run_occupancies`。运行增加稳定身份、冻结执行策略、deadline 来源、事件序号与释放标记；幂等补充管理操作者范围；投递和租约均记录代次。`0019_parallel_runs` 汇合同期迁移分支。回归用例见 [运行测试](../../tests/integration/runs/)。

## 2026-10-02：14 MCP 连接与工具发现

实现 `mcp_connections`、`mcp_checks`、`mcp_discoveries`、`mcp_imports`，迁移 `0014_mcp`。连接补配置/凭据修订、当前测试有效性、健康阈值与检查租约、原授权成员/数据域；快照补协商协议与凭据版本；导入补本地固定输入、影响类型、可读名称及契约可用状态。导入与 10 的草稿、版本、来源关联在同一短事务写入，重复导入按渠道、环境、连接、快照、远端名加锁处理。字段清单与实现冻结在 MCP 模块档案及 `baseline_v0014.json`，执行记录见 [MCP 交接](../integration.md#mcp)。

## 18 业务接入与身份委托实现 / 2026-10-02

`0018_integrations` 落实业务连接、委托密钥和契约测试三表，新增 `delegation_nonces` 防重放与已验签身份来源。连接与测试补齐数据域；连接固定适配器编码、单项能力路径与字段映射。委托密钥补齐 issuer、最长有效期、时钟容差、生效时间及轮换来源；复用 credentials 密文，不复用渠道 API Key。nonce 记录绑定 client_id、请求摘要、完整声明摘要与验签后的 Scope，最少保留 24 小时。字段冻结在 `modules/integrations/baseline_v0018.json`，新表无历史回填。公共 AuthContext/IdentitySource 增加可选 delegation_id，旧管理及测试上下文保持兼容。

## 方案 15 技能包实现

- `0015_skills` 实现技能资源、文件清单和加载测试；冻结定义在 `modules/skills/baseline_v0015.json`。
- 文件清单新增 `unavailable_reason`，区分脚本禁执行与不支持格式；所有文件指向不可变归档产物。
- 技能版本内容增加入口兼容元数据、哈希清单、归档产物、可移植工具声明和互斥规则组。具体依赖仍由公共版本与引用表保存。
- 加载测试增加 `context_snapshot`；`run_id` 与 `release_snapshot_id` 可空，仅本地加载验证时不伪造运行或发布快照。

## 2026-10-02：12 会话管理

`0012_conversations` 接在 `0019_parallel_runs` 后，实现五张会话表及共享 `deletion_jobs`。增加固定 Agent 编码/名称、主体名称、保存期限、消息与轮次序号、不可变输入和版本契约、普通追问来源及已确认条件、摘要生成来源和截断记录、实际上下文来源。字段冻结在 `modules/conversations/baseline_v0012.json`，删除任务归属仍为 25，交接见 [会话管理](../runtime.md#conversations)。

## 2026-10-02：13 结构化记忆管理

`0013_memory` 接在 `0012_conversations` 后，落地六张记忆表及主体级 `memory_deletion_jobs`。新增确认依据、来源等级、观测时间、使用次数、版本状态与序号、读取/建议开关、降级策略和检索 warnings；清理意图记录单项/清空范围，用于阻断旧运行回写。所有主体记录要求完整 Scope。历史 `value` 只保留显式空槽位，不保存可恢复的已改/已删原文。工具调用的既有 `result_summary` JSON 增加 `data_digest`，用于校验事实确实来自已登记结果，不存工具原文。冻结模型见 `modules/memory/baseline_v0013.json`，无需历史数据回填。交接见 [结构化记忆管理](../runtime.md#memory)。

## 16 Agent 定义与发布实现增量 / 2026-10-02

`0016_agents` 接在 `0013_memory` 后，新增 `agents`、`agent_candidates`、`agent_release_records`、`agent_environment_states`；冻结字段见 `modules/agents/baseline_v0016.json`。版本、依赖引用及环境映射继续复用公共表，不回填历史数据。候选按完整 Scope 保存不可变定义与摘要，环境状态和发布记录按渠道/环境保存；发布草稿生成独立版本并保留原草稿。运行既有 `execution_policy` JSON 新增可空 `frozen_spec_id`，旧运行读取兼容。配置和候选均登记公共删除来源图；候选清理后保留摘要，清空正文。实现见 [Agent 交接](../configuration.md#agents)。

## 17 运行编排实现增量 / 2026-10-02

复用运行、版本、产物和来源表，无 DDL 迁移，不修改已冻结的基线文件。`runs.execution_policy` JSON 增加步骤可读名称、`token_limit` 与 `cost_limit`，旧记录字段缺失时使用兼容模型。`run_contents.kind` 增加 `execution_spec`、`inputs:<步骤或尝试>` 和 `partial`，保存冻结描述、实际输入、加载文件和未校验片段；持续文本在同一受控内容记录中累积。

`checkpoints.namespace` 使用 `langgraph:<namespace>` 和 `langgraph:<namespace>:writes` 保存恢复点及 pending writes。内容仍在 `run_contents`，携带父恢复点、原快照和租约代次；服务锁处理幂等，不创建框架表或 upsert。删除处理器同时清理两表的运行内容，保留脱敏状态、Attempt 及账本。

无 Agent 的模块调试使用 `resource_versions.resource_type=runtime`；版本只保存执行结构和测试描述摘要，个人样例及完整测试描述保存在带完整 Scope 的 `run_contents`。测试/样例、提示词、技能、记忆、工具证据和产物通过 `source_links` 登记关联，删除屏障覆盖恢复和迟到响应。交接见 [运行编排](../runtime.md)。

## 1.3.0 / 2026-10-02：Creativity 业务无关边界修订

需求基线更新为 v0.7，技术基线为 v1.6。匹配、风险与分析转为业务方可选配置示例；从 catalog.json 撤销原三个场景模块的 12 张未实现设计表及其领域版本载荷，模型清单由 110 张调整为 98 张。三份原字段文档改为撤销记录，不再用于生成迁移。模型检查只要求需求 00–14 的平台持久化归属，15–17 不再要求独立表。

本次未改动任何已实现表的字段、冻结基线或迁移，也未执行数据库变更。已实现的渠道 business_type、固定数据域限制、旧 HTTP 业务 operation 与 Agent 场景入口仍是现状，方案 19/20 负责兼容整改；历史交付记录不代表已满足新边界。通用结果、证据、产物和评测复用原表；业务权威领域数据由源系统和 MCP 维护。

## 1.4.0 / 2026-10-02：19 渠道与接入边界解耦

先修订模型归档，再以 `0020_access_decoupling` 接在 `0016_agents` 后更新三列中文注释。`channels.business_type` 改为可选展示文本，保留历史分类；外部数据域类型/编号保留原长度与显式配置值，不推导默认映射。原列已经允许空值，不需回填、重建或改写数据；所有渠道、Key、client、数据域、运行来源和版本快照保持原值。旧 `0003_channels` 继续使用冻结基线，新运行元数据使用 `baseline_v0020.json`。

Agent 增加通用 `workflow.v1` 配置入口，历史 `matching.v1/risk.v1/analysis.v1` 仍按原规则解析，不改写版本内容、摘要或运行快照。旧 HTTP 表和委托凭据无存储变更，工具目录归 MCP；兼容清单见 [19 交接](../integration.md#access-decoupling)。

## 1.5.0 / 2026-10-02：20 MCP 业务工具配置接入

新增 `0021_mcp_subject_review` 迁移与独立冻结定义 `baseline_v0021_review.json`，只创建 `subject_review_bindings` 及普通索引。旧迁移建表函数保留原输出，已保存连接、工具、版本和运行不改写。当前主体协议、MCP 身份和结果元数据通过公开 JSON Schema 交付；来源证据复用已有调用和证据模型。

## 2026-10-03：21 Skills 与 Agent 配置交付

复用现有 JSONB 内容，无新表、列、迁移或历史数据回写。技能 `resource_versions.content` 新增可移植依赖的来源与输入输出契约、本地 `tool_bindings`；导出剥离本地映射，旧定义仅恢复原来已固定的工具引用。Agent 内容新增 `bindings.skill_loading` 和步骤 `operator`，进入既有版本摘要及 `agent_candidates.spec`。运行上下文的 JSON 内容新增 `tool_results`，保存实际查询的来源、时间和数据版本；与配置冻结时间分开。包文件 SHA-256、引用闭包、授权锁和运行来源关系沿用现有实现，详见 [21 交接](../configuration.md#configuration-delivery)。

## 1.6.0 / 2026-10-03：24 效果评测与发布门禁

`0024_evaluations` 接在 `0021_mcp_subject_review` 后，新增样本集、不可变样本版本、样本、工具夹具、评测任务、历次结果及报告七表。冻结定义为 `modules/evaluations/baseline_v0024.json`，不改旧迁移，无历史数据回填。固定资料清单含版本与内容摘要；候选继续复用 `agent_candidates`，子 run、费用和工具证据继续复用既有模块。来源删除可清除正文及人工理由，保留非原文状态、摘要和用量关系。交接见 [评测模块](../operations.md#evaluations)。

## 1.7.0 / 2026-10-03：25 删除传播与数据保留

`0025_data_lifecycle` 接在 `0024_evaluations` 后，新建 `deletion_work_items`、`deletion_receipts`，沿用共享 `deletion_jobs`、公共标记、来源及屏障。冻结定义为 `modules/data_lifecycle/baseline_v0025.json`；存量任务由扫描幂等生成步骤，旧标记在当前可信数据库上导出到独立卷后启用清理。

渠道 `retention_policy` JSON 增加运行/元数据/SSE/暂存/导出周期，旧 JSON 通过服务默认值兼容，无列回填。评测样本增加 `source_mode`，独立依据的等价摘要使用既有来源版本字段。工具缓存增加来源运行引用；旧缓存无有效来源时失效。账本 `raw_usage` 与事件载荷收敛为计量白名单。独立删除清单不进入数据库恢复快照；恢复步骤见 [删除生命周期交接](../operations.md#data-lifecycle)。

2026-10-04：新增 `0033_layered_memory`，增加 `memory_consolidations` 持久化后台批次；`memory_policies` 增加通用画像属性与整理配置，`memories` 增加联合来源有效性模式。已有业务属性迁为已有渠道的显式配置，原模型迁移函数保持冻结；数据库不承担业务默认值或约束。
## 2026-10-04 受理并发与配额查询

新增 `0034_admission_indexes`，在 `0033_layered_memory` 后增加四个普通索引：平台当前配置、平台并发占用、平台周期占用、渠道有效受理。历史迁移与基线不变，没有新增表、业务约束或数据库默认值。升级使用 `uv run alembic upgrade head`；降级仅删除本次索引。

当前平台配置和占用查询返回每个限额的一条配置与聚合计数，不把历史占用明细传入受理事务。管理接口返回当前修订、占用、剩余与生效时间。共享读取和预算锁分层顺序见 [服务不变量](service-invariants.md)，新旧应用的锁排序不可混用。

2026-10-05：渠道并发从新的 Agent 发布摘要中分离，仍在每次受理实时校验。旧摘要通过已存冻结预算版本校验兼容，不修改历史版本，无额外数据迁移。

## 1.9.0 · 管理工作区闭环

修订 `0035_management` 新增系统渠道菜单目录，目录、菜单和按钮由服务端维护；`custom_roles.menu_ids` 支持平台与渠道角色选择可见菜单，空值兼容旧角色。账号和审计目录新增普通分页索引。菜单初始数据与全量 SQL 同步；角色动作仍为独立权限上限。

## 角色目录与账号分配

修订 `0036_role_catalog` 增加 `platform_accounts.role_id`、`custom_roles.grant_scope` 与 `builtin_roles.account_assignable`。内置角色通过冻结初始数据入库；开发库仅补缺失记录，不覆盖已有名称或动作。角色管理、账号选项、成员授权和实时鉴权读取同一目录。旧账号不强行推断身份或重建成员，首次明确选定角色后保留其关联。平台维护的共享渠道自定义角色须经逐渠道成员及资源授权才能生效。


## 1.9.1 / 2026-10-06：清除渠道业务分类

通过 `0037_remove_business_type` 删除渠道冗余分类列，同步移除输入、响应、选项接口及页面展示。运行模型切换到 `baseline_v0037.json`，既有建库基线保持冻结；全量初始化 SQL 与当前模型一致。渠道身份、环境、外部数据域映射、接入凭据、授权和历史审计保持原值；迁移回退只恢复可空列，不恢复已清除的分类值。
