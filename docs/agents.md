# 16 Agent 定义与版本发布交接

对应 [执行方案](../../代码编写执行方案/16-Agent定义与版本发布.md) 与 [AGT 需求](../../需求文档/06-Agent与流程管理.md)，开发规范引用 [rule.md](../../rule.md)。本单元完成定义、配置向导、静态检查、冻结快照和环境发布；模型执行、循环调度及会话编排由 17 实现，真实评测由 24 实现。验收证据见 [验证记录](agents-validation.md)。

## 实现与接口

服务端位于 `modules/agents/` 和 `modules/releases/`，装配入口为 `build_agent_service`，应用实例为 `app.state.agents`。前端位于 `creativity-web/src/features/agents/`，导航为“智能体”。生成契约位于 `contracts/agents/` 与 `contracts/openapi.json`，前端复用生成的 API 类型。

以下 HTTP 接口均使用 `/admin/v1` 前缀，范围来自已验证工作区。

| 接口 | 行为 |
| --- | --- |
| `GET/POST /agents` | 按当前授权列出资源；创建稳定调用编码及初始草稿 |
| `GET /agents/options` | 四类流程模板与有权使用的提示词、模型路由、工具、技能版本 |
| `GET/PATCH /agents/{id}` | 详情、基本信息、版本差异、当前环境映射与发布记录；修改携带 revision |
| `POST /agents/{id}/versions` | 从已有可见版本创建草稿，指定版本名称 |
| `GET/PATCH /agent-versions/{id}` | 读取或按 revision 修改草稿；已发布版本禁止编辑 |
| `POST /agent-versions/{id}/validate` | 返回流程、依赖与能力、预算、生产评测等检查结果 |
| `POST /agent-versions/{id}/tests` | 固定草稿 revision，校验输入后委托真实调试运行器；未装配时返回 503 |
| `POST /agents/{id}/releases` | 发布或回滚，携带目标环境、版本 revision、映射 revision、说明及报告引用 |
| `POST /agents/{id}/state` | 当前环境的下线、紧急停用和重新启用，携带 Agent revision 及原因 |

页面提供四步配置向导、按名称选择依赖、只读流程图、步骤输入及分支说明、运行与上下文限制、差异、检查列表和发布记录。调试表单使用 schema 标题生成字段；只有真实运行器返回结果时才展示运行结果、耗时和费用。revision 冲突保留正在编辑的表单。

## 定义与校验

`registry.py` 注册 `structured.v1`、`tool_loop.v1`、`stateful.v1` 及固定场景 `matching.v1`、`risk.v1`、`analysis.v1`。这些入口提供定义和模板；实际执行器分别由 17、21—23 提供。固定场景允许绑定资源及参数，禁止改变固定拓扑或上传代码节点。

`AgentDefinition` 固定输入输出 schema、步骤和边、直接绑定、运行限制及上下文策略。步骤声明字段来源、输出结构、超时和失败方式；检查来源字段类型、必填输入、前序步骤是否覆盖所有到达路径、分支兜底、不可达节点、终止出口及循环上限。schema 使用内联定义，不解析本地或外部 `$ref`。结果必须具备 `business_status`、`schema_version`、`data`、`warnings`、`evidence_refs`，业务状态与运行技术状态分离。

依赖解析展开完整闭包：提示词、模型路由、具体模型和连接、工具及技能。正式发布要求全部依赖已发布；调试/评测可使用获授权草稿，每份草稿均固定 revision 与内容摘要。模型检查当前配置摘要、凭据状态、能力验证及上下文容量；工具检查只读属性、环境、数据域、执行适配及所需授权，MCP 另检查连接与凭据当前验证状态。Skills 不能加入 Agent 工具白名单外的工具。

发布预算检查当前匹配策略、可用额度、估算与价格，不产生运行预占。缺少价格时金额硬限制无法放行；17 实际受理和每次外部调用仍须通过 08 的准入、预占与结算。Token、轮数、工具次数、循环时间、总时限与费用配置交给 17 执行，不能将静态通过当作这些运行限制已被真实验证。

渠道和模型预算版本进入配置依赖摘要；调用 Key 的独立预算随当前身份实时检查，不写入发布者的配置摘要，避免正常调用因身份不同被误判为发布内容已变化。

## 发布事务及评测

发布在 Agent、版本、环境映射、依赖资源、授权、预算和报告锁下重读当前事实。首次发布要求 `expected_mapping_revision=null`；后续必须与现有映射修订匹配。目标环境必须等于当前工作区环境。

发布草稿会生成独立的不可变 `PUBLISHED` 版本，保留原草稿继续编辑；发布映射、引用、检查证据、操作记录和审计同事务提交。内容摘要及完整依赖摘要共同标识评测候选，最终新分配的版本标识不参与评测绑定。任何内容、草稿依赖 revision 或相关策略变化都会使原报告失效。

24 通过 `EvaluationGate.keys(context, refs)` 声明锁，通过 `read(uow, context, refs)` 从受信报告存储复核并返回 `EvaluationEvidence`。读取使用当前工作单元，不访问网络；报告必须匹配渠道、Agent、生产环境、内容摘要、完整依赖摘要及报告集合，且通过、未过期。报告摘要记录在发布检查证据中，不放入被评测内容摘要。默认未装配评测门禁，因此 prod 始终拒绝缺少有效证据的发布。

回滚仅能指向当前环境的历史发布版本，并重新检查当前授权、依赖、预算及生产评测。下线阻断新受理；紧急停用还由 17 在下一次外部调用前检查。环境状态不会跨环境扩散，已生成快照的历史内容不因映射或状态切换而改写。

## 向 17 交接

| 入口 | 使用约定 |
| --- | --- |
| `freeze_candidate(context, version_id, revision, purpose)` | `debug/evaluation` 使用获授权草稿；固定完整配置、具体依赖、策略及摘要并存入候选表 |
| `resolve_published(context, agent_code)` | 正式运行只解析当前环境已发布映射；重查授权和依赖，不要求调用方重复提交发布报告 |
| `load_candidate(context, snapshot_id)` | 按完整 Scope 读取持久化候选，并检查当前授权、来源删除及恢复屏障 |
| `check_external_boundary(context, spec)` | 每次外部调用前检查候选未被替换及当前紧急停用状态；普通下线允许既有运行继续 |
| `AgentRunResolver` | 为 11 的 `RunService` 提供解析、锁及事务内复核；受理策略和输入输出契约必须与冻结定义一致 |
| `AgentDebugRunner.submit` | 17 注入真实调试受理、幂等、预算及结果查询；不得返回模拟成功 |

`FrozenExecutionSpec.payload_json` 是冻结内容的唯一真值，解析属性返回新副本。其范围、来源 revision、内容摘要和依赖摘要不能由请求或模型替换。受理使用冻结的 `versions` 写入公共 `release_snapshots`，并将 `snapshot_id` 保存在 `runs.execution_policy.frozen_spec_id`；Worker 按此标识加载同一候选，不重新解析最新草稿或发布映射。受理测试已验证旧排队任务和随后新任务分别使用旧、新版本，幂等重放仍返回原任务。

17 需要按冻结流程实现四类执行方式，将预算估算放入 `admission_plan`，执行所有硬上限及有限输出修复，并接通统一运行详情、会话与 SSE。这里保留 `POST /api/v1/runs` 的既有公开契约；本单元没有把未实现的执行器挂到正式调用入口。AGT-A03/A06 的真实循环、恢复和对话行为在 17 联调。

记忆声明固定在 Agent 快照中，发布时验证不得超过 13 的渠道策略；发布不会改写渠道或 Agent 当前记忆策略。17 使用记忆前须将冻结声明与 13 的当前限制、主体开关及来源有效性求交集。冻结允许不能覆盖之后的撤销，当前放宽也不能扩张旧快照的权限。

## 数据与删除交接

`0016_agents` 接在 `0013_memory` 后，当前迁移链已包含 14/15 的前置表。新增 `agents`、`agent_candidates`、`agent_release_records`、`agent_environment_states`；配置版本、引用和发布映射复用公共表，无历史数据回填。字段和版本内容见 [模型归档](data-model/modules/agents.md)。

候选登记 Agent 及每个具体版本到 `agent_candidate` 的来源边；删除任一来源后立即不可读取候选。应用向公共 `CleanupRegistry` 注册候选清理器，有删除标记和当前清理授权才可清空 `spec`，保留摘要及关联历史。详情不返回被删除版本正文。25 负责有类型的来源删除授权、全图遍历、保留和备份恢复核对，本单元不替代完整删除验收。
