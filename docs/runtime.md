# 17 运行编排与流式交付

实现日期：2026-10-02。依据 [方案 17](../../代码编写执行方案/17-运行编排与流式交付.md)。本单元把 [运行受理](runs.md)、[Agent 冻结定义](agents.md)、模型、工具、提示词、技能、会话和记忆装配为统一执行链路。检查见 [验证记录](runtime-validation.md)，事件协议见 [SSE 契约](runtime-sse.md)，外部供应商状态见 [兼容记录](runtime-providers.json)。

代码实现和自动验证不代表真实模型验收通过。本次环境没有可用模型凭据，两个真实供应商或协议组合仍待验证；生产发布仍须通过 24 的评测门禁。

## 装配和受理入口

API 的 `create_app` 和 Celery 的 `worker_services` 均调用 `install_runtime`。API、Worker 和 Beat 使用相同数据库、模型加密主密钥、出站白名单及资源配置；Beat 继续补偿持久化投递意图。路由已经正式挂载。

| 入口 | 解析与用途 |
| --- | --- |
| `POST /api/v1/runs` | 只接收 Agent 编码及业务输入，由 `RuntimeResolver` 解析当前环境发布映射；用途为 production |
| Agent 版本测试 | `AgentService.freeze_candidate` 固定 revision 和依赖，`RuntimeAdmission.submit` 创建 debug |
| 评测服务内部端口 | 将 purpose=evaluation 的受控 `FrozenExecutionSpec` 交给 `RuntimeAdmission.submit`；无公共上传执行定义的 HTTP 接口 |
| 模型验证、提示词、工具、技能测试 | 模块端口生成冻结测试描述，无需已经存在正式 Agent；统一创建 debug 及独立 Attempt/用量 |
| 管理重新执行 | 新建 run 并记录 parent_run_id；正式运行重新解析发布，Agent 调试/评测重新冻结当前版本并保留用途；模块测试创建新冻结描述 |

模块测试重新执行只形成新的运行记录，不改写原模块测试的能力认证结果；需要更新能力验证记录时，从模型管理重新发起测试。

冻结测试的公共 `resource_versions` 只保存执行结构和描述摘要。样例原文、模型请求、加载文件和测试描述进入 `run_contents`，测试/样例到运行的来源关联在受理事务内登记，避免个人内容被放入不具备主体归属的配置版本。已有运行的冻结快照不随环境映射改变。

## 执行与计量

`RuntimeExecutor` 用普通服务循环执行结构化、固定模板和工具循环；stateful 流程用 LangGraph。计算节点通过 `StepRegistry.register(entrypoint, node_key, handler)` 加载，未注册明确报错，不执行用户上传代码。现有固定场景入口是旧基线遗留，方案 19 处理兼容。新增任务通过 Skills/Agent 配置表达，领域计算通过业务 MCP；不再要求 21–23 为每种场景注册代码处理器。通用算子扩展仍需独立实现与验证。

API 可传入 `create_app(runtime_registry=..., current_subjects=...)`；Worker 在启动模块配置 `workers.runs.step_registry` 和 `workers.runs.current_subject_reader`。受信工厂也可直接传入 `worker_services(..., registry=..., current_subjects=...)`。业务主体权限读取器遵循 18 的 `CurrentSubjectReader`，必须向源系统复核当前权限；未装配时服务身份的后台执行拒绝继续，不能拿原 Token 或委托历史声明替代当前授权。20 的配置式主体复核仍是服务身份联调前置，不按业务仓库实现专用读取器。

每次调用前复核运行身份、冻结白名单、当前资源授权、取消、删除、deadline、次数和预算。模型按冻结路由顺序选择候选，单步骤默认最多重试 2 次，总模型交互默认 6 次、工具默认 10 次；冻结路由和工具策略可以收紧重试次数。结构修复占用同一重试及运行限额，崩溃恢复从已有 Attempt 计算已用次数。

实际模型参数按 UTF-8 字节数保守估算，预算预占提交后再发送。每次重试有独立 Attempt 和账本记录；失败且实际用量不明时保留待核实，不计作零。流已输出正文后失败不启动回退拼接。取消、租约失效、删除和步骤超时会发出取消信号，消费者最多等待 5 秒收取适配器取消尾部用量；不能取得的用量继续 PENDING。

工具使用冻结白名单和独立 Attempt，输出 schema、来源时间、证据及产物权限通过校验后才保存成功响应。证据登记先于成功响应提交；恢复可以复用保存的响应，不跳过证据。最终结果仅引用本运行已登记且字段一致的工具证据，产物下载单独授权。

计算结果先校验再提交。`failure_policy=partial` 仅在冻结最终 schema 接受时返回 `PARTIAL`，数据为 `{"steps": 已完成步骤结果}`，并附失败步骤说明；schema 不接受此结构则失败。成功的技术状态和部分完成的业务状态分别展示。

## 上下文与恢复

提示词只接收已声明且来源匹配的变量；指令段和输入/工具/记忆数据使用不同消息角色。技能按冻结绑定加载并记录文件路径和哈希，会话只读取受本次运行占用保护的上下文。记忆将当前策略与冻结的类型、读取开关、检索数量、TTL 和降级方式取交集；降级提示从持久化加载记录合并至最终业务 warnings，恢复后仍保留。实际消息、模型版本和加载来源可在运行详情查看。

会话创建目录列出当前环境已发布、启用会话并获执行授权的 Agent，创建和每轮受理再次验证。当前上下文策略提供近期消息与已有有效摘要的读取；不自动触发新的摘要模型调用。

`integrations/checkpoints.SQLAlchemySaver` 复用 `checkpoints` 和 `run_contents`，支持 LangGraph checkpoint、父节点和 pending writes。每次读写都绑定完整 Scope、run 和 lease_version；不调用框架建表或 upsert。相同恢复点重复提交必须内容一致。取消、终态、授权失效、来源删除和旧租约均不能恢复或提交。

模型响应与成功 Attempt 同事务保存。进程在响应保存后、步骤提交前崩溃时复用该响应；已发送但响应未保存的模型尝试保留 UNKNOWN，恢复停止自动重发。流断开和访问 Token 自然到期不取消任务；当前账号、成员、Key、渠道或源业务权限失效会阻止后续执行。

## 页面与契约

执行中心提供任务名称、状态、用途、智能体、调用密钥、错误类别、主体和时间筛选，环境/数据域沿用工作区。`RunViewer` 供执行中心和各模块调试区复用，展示步骤与尝试、输入权限、部分内容、实际加载、工具证据、依赖版本、用量完整性、产物、取消及新运行链接。内部编号只出现在可展开的排障区。

`RunDetail`、`RunFilterOptions`、公开运行路由、SSE 响应类型、JSON Schema 和前端生成类型已同步。详情原文需要 `run:content` 与敏感数据权限，轨迹元数据只需 `run:read`。产物枚举和下载分别复核授权。前端使用带 Authorization 的 fetch 流，游标去重；过期回到原运行快照，网络断线重连，不创建新任务。

## 存储增量

本单元复用既有表，无 DDL 迁移。`execution_policy` JSON 补充步骤名称、token_limit、cost_limit；`run_contents.kind` 增加 execution_spec、inputs:* 和部分文本；`checkpoints.namespace` 使用 `langgraph:<namespace>` 与 `langgraph:<namespace>:writes`。运行删除清理器同时删除 checkpoint 和受控原文，用量、终态及脱敏元数据继续保留。详见 [数据模型增量](data-model/changes.md)。
