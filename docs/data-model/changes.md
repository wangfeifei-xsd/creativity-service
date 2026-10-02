# 模型变更记录

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

新增 `0011_runs` 与运行冻结基线：落地运行、幂等、投递、步骤、尝试、事件、租约及 checkpoint，新增 `run_contents`、`run_recoveries`、`run_occupancies`。运行增加稳定身份、冻结执行策略、deadline 来源、事件序号与释放标记；幂等补充管理操作者范围；投递和租约均记录代次。`0019_parallel_runs` 汇合同期迁移分支。完整验收见 [运行验证](../runs-validation.md)。

## 2026-10-02：14 MCP 连接与工具发现

实现 `mcp_connections`、`mcp_checks`、`mcp_discoveries`、`mcp_imports`，迁移 `0014_mcp`。连接补配置/凭据修订、当前测试有效性、健康阈值与检查租约、原授权成员/数据域；快照补协商协议与凭据版本；导入补本地固定输入、影响类型、可读名称及契约可用状态。导入与 10 的草稿、版本、来源关联在同一短事务写入，重复导入按渠道、环境、连接、快照、远端名加锁处理。字段清单与实现冻结在 MCP 模块档案及 `baseline_v0014.json`，执行记录见 [MCP 交接](../mcp.md)。

## 18 业务接入与身份委托实现 / 2026-10-02

`0018_integrations` 落实业务连接、委托密钥和契约测试三表，新增 `delegation_nonces` 防重放与已验签身份来源。连接与测试补齐数据域；连接固定适配器编码、单项能力路径与字段映射。委托密钥补齐 issuer、最长有效期、时钟容差、生效时间及轮换来源；复用 credentials 密文，不复用渠道 API Key。nonce 记录绑定 client_id、请求摘要、完整声明摘要与验签后的 Scope，最少保留 24 小时。字段冻结在 `modules/integrations/baseline_v0018.json`，新表无历史回填。公共 AuthContext/IdentitySource 增加可选 delegation_id，旧管理及测试上下文保持兼容。

## 方案 15 技能包实现

- `0015_skills` 实现技能资源、文件清单和加载测试；冻结定义在 `modules/skills/baseline_v0015.json`。
- 文件清单新增 `unavailable_reason`，区分脚本禁执行与不支持格式；所有文件指向不可变归档产物。
- 技能版本内容增加入口兼容元数据、哈希清单、归档产物、可移植工具声明和互斥规则组。具体依赖仍由公共版本与引用表保存。
- 加载测试增加 `context_snapshot`；`run_id` 与 `release_snapshot_id` 可空，仅本地加载验证时不伪造运行或发布快照。

## 2026-10-02：12 会话管理

`0012_conversations` 接在 `0019_parallel_runs` 后，实现五张会话表及共享 `deletion_jobs`。增加固定 Agent 编码/名称、主体名称、保存期限、消息与轮次序号、不可变输入和版本契约、普通追问来源及已确认条件、摘要生成来源和截断记录、实际上下文来源。字段冻结在 `modules/conversations/baseline_v0012.json`，删除任务归属仍为 25，交接见 [会话管理](../conversations.md)。

## 2026-10-02：13 结构化记忆管理

`0013_memory` 接在 `0012_conversations` 后，落地六张记忆表及主体级 `memory_deletion_jobs`。新增确认依据、来源等级、观测时间、使用次数、版本状态与序号、读取/建议开关、降级策略和检索 warnings；清理意图记录单项/清空范围，用于阻断旧运行回写。所有主体记录要求完整 Scope。历史 `value` 只保留显式空槽位，不保存可恢复的已改/已删原文。工具调用的既有 `result_summary` JSON 增加 `data_digest`，用于校验事实确实来自已登记结果，不存工具原文。冻结模型见 `modules/memory/baseline_v0013.json`，无需历史数据回填。交接见 [结构化记忆管理](../memory.md)。

## 16 Agent 定义与发布实现增量 / 2026-10-02

`0016_agents` 接在 `0013_memory` 后，新增 `agents`、`agent_candidates`、`agent_release_records`、`agent_environment_states`；冻结字段见 `modules/agents/baseline_v0016.json`。版本、依赖引用及环境映射继续复用公共表，不回填历史数据。候选按完整 Scope 保存不可变定义与摘要，环境状态和发布记录按渠道/环境保存；发布草稿生成独立版本并保留原草稿。运行既有 `execution_policy` JSON 新增可空 `frozen_spec_id`，旧运行读取兼容。配置和候选均登记公共删除来源图；候选清理后保留摘要，清空正文。实现见 [Agent 交接](../agents.md)。

## 17 运行编排实现增量 / 2026-10-02

复用运行、版本、产物和来源表，无 DDL 迁移，不修改已冻结的基线文件。`runs.execution_policy` JSON 增加步骤可读名称、`token_limit` 与 `cost_limit`，旧记录字段缺失时使用兼容模型。`run_contents.kind` 增加 `execution_spec`、`inputs:<步骤或尝试>` 和 `partial`，保存冻结描述、实际输入、加载文件和未校验片段；持续文本在同一受控内容记录中累积。

`checkpoints.namespace` 使用 `langgraph:<namespace>` 和 `langgraph:<namespace>:writes` 保存恢复点及 pending writes。内容仍在 `run_contents`，携带父恢复点、原快照和租约代次；服务锁处理幂等，不创建框架表或 upsert。删除处理器同时清理两表的运行内容，保留脱敏状态、Attempt 及账本。

无 Agent 的模块调试使用 `resource_versions.resource_type=runtime`；版本只保存执行结构和测试描述摘要，个人样例及完整测试描述保存在带完整 Scope 的 `run_contents`。测试/样例、提示词、技能、记忆、工具证据和产物通过 `source_links` 登记关联，删除屏障覆盖恢复和迟到响应。交接见 [运行编排](../runtime.md)。

## 1.3.0 / 2026-10-02：Creativity 业务无关边界修订

需求基线更新为 v0.7，技术基线为 v1.6。匹配、风险与分析转为业务方可选配置示例；从 catalog.json 撤销原三个场景模块的 12 张未实现设计表及其领域版本载荷，模型清单由 110 张调整为 98 张。三份原字段文档改为撤销记录，不再用于生成迁移。模型检查只要求需求 00–14 的平台持久化归属，15–17 不再要求独立表。

本次未改动任何已实现表的字段、冻结基线或迁移，也未执行数据库变更。已实现的渠道 business_type、固定数据域限制、旧 HTTP 业务 operation 与 Agent 场景入口仍是现状，方案 19/20 负责兼容整改；历史交付记录不代表已满足新边界。通用结果、证据、产物和评测复用原表；业务权威领域数据由源系统和 MCP 维护。

## 1.4.0 / 2026-10-02：19 渠道与接入边界解耦

先修订模型归档，再以 `0020_access_decoupling` 接在 `0016_agents` 后更新三列中文注释。`channels.business_type` 改为可选展示文本，保留历史分类；外部数据域类型/编号保留原长度与显式配置值，不推导默认映射。原列已经允许空值，不需回填、重建或改写数据；所有渠道、Key、client、数据域、运行来源和版本快照保持原值。旧 `0003_channels` 继续使用冻结基线，新运行元数据使用 `baseline_v0020.json`。

Agent 增加通用 `workflow.v1` 配置入口，历史 `matching.v1/risk.v1/analysis.v1` 仍按原规则解析，不改写版本内容、摘要或运行快照。旧 HTTP 表和委托凭据无存储变更，工具目录归 MCP；兼容清单见 [19 交接](../access-decoupling.md)。

## 1.5.0 / 2026-10-02：20 MCP 业务工具配置接入

新增 `0021_mcp_subject_review` 迁移与独立冻结定义 `baseline_v0021_review.json`，只创建 `subject_review_bindings` 及普通索引。旧迁移建表函数保留原输出，已保存连接、工具、版本和运行不改写。当前主体协议、MCP 身份和结果元数据通过公开 JSON Schema 交付；来源证据复用已有调用和证据模型。
