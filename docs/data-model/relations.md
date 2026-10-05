# 对象关系与生命周期

模型版本 1.7.0；完整表及字段见 [索引](README.md)。下文为逻辑关联，实施服务对关联对象的范围、存在及状态负责。

| 来源 → 目标 | 逻辑关联键与归属 | 版本、状态与删除关系 |
| --- | --- | --- |
| channels → channel_environments / data_scopes / service_clients / channel_keys | channel_id；主档 id 等于 channel_id；环境与接入服务环境不可改绑 | ACTIVE → SUSPENDED → ACTIVE；ACTIVE/SUSPENDED → ARCHIVED，归档须无活动 Key 与运行 |
| channels → channel_code_index | 主档归自身渠道，编码定位索引归 system；同事务创建 | 系统渠道编码互斥，不通过普通业务仓储扫描全平台 |
| platform_limits → platform_limits | 平台限额归 system 专用控制面，replaces_id 指向前版 | 规则变更新增不可变版本行，停用旧版本；准入固定具体记录 id |
| budget_policies → resource_versions | 业务预算归实际渠道 | 周期、限额变更冻结 budget_policy 版本，预占固定 policy_version_id |
| channel_keys → key_identity_index / key_rotations | 身份索引归 system，通过 target_channel_id + key_id 定位真实渠道 | Key、索引同事务创建；轮换新增 Key；不覆盖历史调用归属 |
| platform_accounts / builtin_roles → channel_memberships → resource_grants | 系统身份；成员/资源授权在真实渠道按 user_id 或 grantee_id 关联 | 账号/成员 ACTIVE ↔ DISABLED；撤销即影响下一次授权边界 |
| platform_accounts → builtin_roles / custom_roles → iam_menus | 系统账号的 role_id 为所选角色，platform_roles 为有效平台授权；内置角色以 role_code 关联，自定义角色以 id 关联；menu_ids 引用系统菜单 | 菜单清单只控制入口，不扩大接口权限；旧角色空值按动作生成；被角色引用的菜单不能直接删除 |
| provider_catalog → model_connections → models → model_routes | 字典仅描述供应商；连接和模型属于同一真实渠道，连接凭据按环境绑定 | 实际版本固定供应商名称、能力及参数；价格仅引用 price_versions |
| prompts / agents / tools / skills / model_routes / models → resource_versions | resource_type + resource_id；版本 id 用于依赖，输出结构单独固定 | DRAFT 按 revision 修改；冻结为 PUBLISHED；退役为 RETIRED，禁止新绑定 |
| resource_versions → resource_references → resource_versions | source_version_id → target_version_id，均同渠道 | 发布时依赖必须已发布；被引用版本保留追溯；退休前检查引用 |
| resource_versions → release_mappings | 渠道 + 环境 + 资源类型 + 资源 id → version_id | 映射切换与内容冻结分别操作；回滚重新校验当前授权，不改历史内容 |
| release_mappings → release_snapshots → runs | 受理冻结整份依赖闭包；run_id 与 snapshot_id 双向关联 | 草稿调试固定 revision、内容摘要与正文；正式运行只绑定发布版本 |
| credentials → model_connections / mcp_connections / integrations / delegation_keys | 同渠道环境 credential_ref，密文 AAD 固定渠道、环境、用途及 id | ACTIVE → DISABLED；密钥版本由外部密钥服务解析，轮换保留解密能力 |
| runs → run_idempotency / dispatch_outbox / admissions | 单事务受理；幂等包含 client_id，不含可轮换 key_id | QUEUED → RUNNING → SUCCEEDED/FAILED/TIMED_OUT；取消竞争遵循先提交者，终态不可反转 |
| runs → run_steps → attempts → usage_records / usage_events | run_id / step_id / attempt_id，继承受理身份 | 重试新增 attempt；累计用量有效值替换，修正留 usage_adjustments |
| budget_policies → budget_reservations / budget_alerts | 策略版本、周期、币种、渠道共同定范围 | HELD → PENDING/SETTLED/RELEASED；待核实消耗不当零；迟到事件只修账本 |
| runs → run_events / run_leases / checkpoints / checkpoint_writes | 渠道 + run_id；恢复点再按 namespace + checkpoint_key；待写按 task_id + sequence | 有效租约代次才能提交；事件单调序号；清除内容不抹除消耗事实 |
| conversations → messages / conversation_turns / conversation_summaries / context_snapshots | 完整渠道、环境、数据域、主体；父子均核对归属 | ACTIVE ↔ ARCHIVED；ACTIVE/ARCHIVED → DELETING → DELETED；同会话只一个活动生成运行 |
| memories → memory_sources / memory_versions / memory_retrievals / memory_preferences | 完整主体范围 + 属性；不同类型同号主体也隔离 | PROPOSED → ACTIVE → SUPERSEDED/EXPIRED/REVOKED；来源全部失效则撤销；历史内容也清理 |
| mcp_connections → mcp_checks / mcp_discoveries / mcp_imports → tools | 固定 connection_revision / discovery_id / schema_hash | 发现只形成快照；人工导入、授权、发布后才能执行；协议变化不静默替换 |
| skill_files / tool_calls / evidence_refs → artifacts | 具体版本或调用 id；文件路径不作为授权 | 文件属于上传上下文；证据带来源版本、观测时间与位置；访问重新授权 |
| evaluation_datasets → evaluation_dataset_versions → evaluation_cases / evaluation_fixtures | 样本修订产生新行，新版本固定 case_ids 与 fixture_ids | 人工标注不改旧基线；来源删除使原文不可见，报告注明不可复现 |
| evaluations → evaluation_results → runs / evaluation_reports | 同渠道快照、样本和子 run；purpose=evaluation | 未完成/无效不当通过；发布门禁绑定依赖摘要 |
| integrations → data_scopes / delegation_keys / integration_tests | 域映射引用渠道模块；适配器不复制映射真值 | 委托受众、来源、有效期和环境均须匹配；领域差异由业务 MCP 负责，旧 HTTP 协议按 19/20 兼容处理 |
| 任意内容 → source_links → 派生内容 | 同渠道，边保存在派生范围；source_type/id/version → derived_type/id，回溯使用各来源真实范围 | 消息→摘要/记忆→上下文→运行/恢复点→文件/样本/报告；删除任一祖先阻断后代读写 |
| deletion_markers → deletion_jobs → deletion_work_items | 标记同渠道原范围、目标类型和 id，不含原文 | 先写永久标记再清理；重试幂等；未登记处理器阻止完成；来源有独立依据的记忆由 13/25 重算 |
| recovery_barriers → 所有恢复内容入口 | 完整内容范围 + recovery_id + 外部删除账本摘要 | 缺失/BLOCKED 拒绝；仅新空范围可初始化；已有内容导入标记并核对后 READY |
| audit_events / usage_exports | 渠道审计归实际目标；明确平台汇总归 system 并带授权渠道集合 | 保留无原文元数据；导出生成与下载分别检查权限 |

公共配置版本归渠道并跨环境共享；配置类型的删除标记与来源链由专用检查器在同一渠道核对，其余个人内容按完整 Scope 过滤。内容图更新使用渠道级短事务锁，配置删除不能通过切换环境绕过。配置版本不能隐式派生自个人内容，运行快照则显式登记入口运行与各具体版本来源。方案 04/05/18 负责实时授权与目标范围，方案 25 汇总完整清理图并逐授权范围处理。

24 的经审阅反馈允许同渠道、同环境、同域的主体 run 派生管理范围样本。来源边保存在派生样本范围，读取和派生检查按原 run 服务端 Scope 回溯标记。25 须按渠道与来源标识发现这种边，再切换到派生记录的真实 Scope 调用清理器，不能仅扫描原主体范围。

04 关系增量：platform_accounts.credential_version 与管理 Token 的签发代次比较；channel_memberships.revision 与管理工作区 Token 的成员修订比较。资源授权从数据库实时读取，不保存在 Token 中。resource_grants 通过目标渠道关联有效成员或系统内置角色，撤销保留原对象与 revision 并清空 allowed_actions。iam_revocations 保存账号/成员/Key 索引或单 Token 摘要撤销意图，完成缓存补偿后保留元数据供后续保留策略处理。

渠道不保存业务分类；外部数据域类型与编号采用显式配置，不限定 default/default 或 club。`0037_remove_business_type` 仅删除冗余分类列，保留渠道及原映射标识。`service_clients.data_scopes` 显式列举同渠道、同环境的数据域，服务身份每次取其中仍启用的范围。`key_identity_index` 与渠道 Key 主记录同事务提交，身份索引归 system，主记录归真实渠道。`key_rotations` 保留新旧 Key 标识与重叠截止时间，不更改 client_id。

`channel_lifecycle_events` 与治理变更、审计共用事务，载荷只包含状态及修订等元数据；原始 channel_id、environment、target_id 不因消费或清理改写。runs 与 retention 消费进度分别记录；消费按至少一次交付，接收方按 event_id 幂等。归档不删除 Key、轮换、用量和审计记录。

08 实现：`usage_records` 按原始 run/attempt 固定渠道与来源，`usage_events` 的连接/供应商请求/版本去重键归同一渠道；`usage_adjustments` 关联账本和可空来源事件（价格重算没有新供应商事件）。`budget_reservations.policy_version_id` 指向公共预算版本，`platform_quota_occupancies` 的系统记录只引用实际渠道/run 元数据。普通导出包含授权业务范围；平台汇总导出归系统渠道并保留实际渠道集合。汇率只用于展示折算，不跨币种共享硬预算。

## 11 运行派生关系

`runs` 引用公共 `release_snapshots`，`run_contents` 按 run 保存输入、返回内容、事件正文与恢复点内容。会话→run 来源链接与快照的来源链接由同一受理事务登记。`run_occupancies` 保存会话运行占用；`run_recoveries` 保留失效租约代次的处理决定。清理运行原文时删除内容及 checkpoint，保留幂等定位、尝试和用量引用；晚到用量只能回到原渠道的原尝试。

### 18 业务接入关系

`integrations.scope_mapping_ref` 引用同渠道环境中 05 的 data_scopes，并与服务端 data_scope_id 一致；credential_ref 引用同渠道环境的 http_tool 密文。delegation_keys 按稳定 service_clients 与环境绑定独立 delegation 密文，rotated_from 保留轮换来源。delegation_nonces 按已验证上下文关联 kid，保存请求及身份摘要，运行只保存 delegation_id；同 nonce 重发不新增运行，运行幂等仍归 11。integration_tests 固定连接配置 revision，不保存业务参数与原文。

## 技能包实现关系

`skills → resource_versions(skill) → skill_files` 构成资源与版本文件清单；文件引用该版本不可变 ZIP 产物。可移植工具声明在目标渠道解析后进入公共版本依赖，冻结时登记 `resource_references`。`skill_tests.context_snapshot` 保存本次定义、输入和加载结果，仍引用旧产物时不回收旧包。来源图登记技能到版本、版本到文件/产物/测试；原文与包正文不进入审计摘要。

## 12 会话及来源关系

会话固定完整身份范围与 Agent；轮次关联用户/助手消息及唯一有效 run，每轮保留其发布版本和契约。conversation→message/run/artifact/context、message→summary/context、summary→context 通过公共来源图登记；受控附件作为消息来源，既有独立产物不因被引用而改变归属。普通追问保存 source_run_id 及 confirmed_conditions。删除共享 deletion_jobs 保存来源图影响，先访问屏障，再由 25 完成关联传播。

## 13 实现补充

记忆的当前值由 `memory_sources` 独立来源集合支撑；消息/证据通过公共来源图指向记忆，来源检查采用“至少一个有效依据”，剔除失效边后再检查派生对象。版本只保存变更元信息和来源引用。`memory_deletion_jobs` 与记忆共享完整主体范围，遗忘和清空的时间边界同时约束已排队候选；25 消费清理意图后负责全图及恢复核对。

## 16 Agent 实现关系

`agents → resource_versions(agent)` 共用渠道内配置身份；草稿可修订，发布生成独立不可变版本。`release_mappings` 是当前环境版本的唯一真值，`agent_release_records` 保存来源修订、前后版本与检查证据，`agent_environment_states` 保存各环境启停状态。`agent_candidates` 按完整 Scope 固定 Agent 定义、完整依赖及策略；运行策略引用候选标识，公共运行快照复制同一冻结内容。Agent 和每个具体版本通过来源图指向候选，来源删除后禁止读取；清理候选原文保留摘要及历史关联。

## 20 当前主体复核关系

`subject_review_bindings` 按渠道、环境、数据域和 client 固定 MCP connection/discovery/schema。该绑定只授权受控身份查询，不生成本地模型工具。MCP 工具导入继续复用 `mcp_imports → resource_versions(tool)`；来源、观测时间和证据复用 ToolResult、tool_calls 与 evidence_refs，不增加领域表。旧连接修订与新凭据之间不存在自动重绑定。

25 实现 `deletion_jobs → deletion_work_items → deletion_receipts`：任务下按目标生成独立步骤，跨主体步骤保留目标 Scope；任务证明归原申请范围，只含数量和摘要。独立卷条目按 Scope/类型/目标去重，导入同一个 `deletion_markers`。永久来源标记与旧数据库的任务/对象重放由同一水位核对；没有第二套业务删除真值。
