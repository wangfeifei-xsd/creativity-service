# 08 用量账本与预算控制交接

方案 [08](../../代码编写执行方案/08-用量账本与预算控制.md) 已实现。规则遵循 [rule.md](../../rule.md)，字段见 [用量模型](data-model/modules/usage.md)，验证见 [usage-validation.md](usage-validation.md)。真实供应商调用、运行终态以及回退编排由 11/17 联调，调试和评测费用由 17/24/26 组合验收。

## 入口与装配

`modules/usage/assembly.py` 的 `build_usage_services(engine, channels, store)` 返回 `budgets`、`ledger`、`management`、`queries`、`exports`、`prices`。应用已接入，05 的 `UsageReader` 与 07 的 `ModelPriceReader` 使用同一价格和账本。普通管理接口使用当前工作区；平台汇总和平台导出必须使用专门授权的平台管理会话。

管理路由包含 `/usage/summary`、`/usage/records`、明细核查及 `/{id}/reprice`、`/usage/aggregates/rebuild`、`/usage/exports`、`/usage/alerts`、`/usage/exchange-rates`、`/budgets`、`/models/{id}/price-versions`、`/platform/budget-limits`、`/platform/usage/exports`，统一前缀 `/admin/v1`。原有 `/channels/{id}/usage` 和 `/platform/usage` 已装配真实查询。平台导出输出按币种分组的渠道汇总；普通导出仅输出创建人当前仍有权导出的数据域及主体范围。

前端通过 `features/usage/registration.ts` 接入 06 工作区，提供总览、趋势、明细、预算与价格入口。动作依据服务端会话，下钻保留核算轨迹，金额缺失不补零，导出可查看任务并下载私有 CSV。

## 11/17 调用顺序

```python
from creativity_service.core.database import transaction
from creativity_service.modules.usage.schemas import AttemptPlan

plan = AttemptPlan(
    run_id=run_id,
    attempt_id=attempt_id,
    agent_id=agent_id,
    model_id=model_id,
    connection_id=connection_id,
    purpose="production",
    input_tokens=actual_input_upper_bound,
    max_output_tokens=output_limit,
    subset_relations=adapter_subset_relations,
    names=trusted_display_names,
)
keys = [*run_keys, *services.budgets.admission_keys(context, run_id)]
async with transaction(engine, context.scope, keys) as uow:
    await services.budgets.admit(uow, context, run_id, plan)
    # 在这里共用 uow 写 run、幂等记录及 outbox。

await services.budgets.reserve_attempt(context, plan)
await services.ledger.mark_sent(context.scope, attempt_id)
# 两次短事务均已提交后，才允许调用供应商。
# usage_event 为 03 的 UsageEvent，scope 必须来自原始运行快照。
await services.ledger.settle(usage_event, outcome="SUCCEEDED")
await services.ledger.release_unused(context.scope, attempt_id)
await services.budgets.finish_admission(context, run_id)
```

`reserve_attempt(..., uow=uow)` 可和受理复用事务；锁合并一次排序。后续模型回退必须生成新的 `attempt_id`。工具上下文增大时，发送前再次传入更新的实际输入上限；已经发送的尝试不能重新预占。`finish_admission` 只释放并发位，历史请求次数继续计数。run 的业务终态只能由运行模块维护，账本没有写 run 的入口。

`AttemptPlan` 是内部受信输入，不绑定外部 HTTP 正文。`AuthContext` 的渠道、数据域、主体、client、key、actor 和用途在受理时固定；同 run 不能换 Key、操作者或用途。管理调试和评测允许 Key 为空，仍必须归真实业务渠道。07 的调试运行未由 17 受理前，不绕过账本单独访问模型。

03 的原单条 `reserve(uow, context, attempt_id)` 保留为已有占用的回执适配；必须先调用含完整候选参数的 `reserve_attempt`。新消费者直接使用组回执 `ReservationReceipt`，不能只依赖其中一条预算。`record_usage(uow, event)` 兼容原计量端口；新调用通常用 `ledger.settle` 自行开启结算事务。新增内部 schema 在 `contracts/usage/`。

## 核算与预算语义

- 渠道预算、Key、模型限额在同一渠道账本事务中同时校验。P0 采用渠道粗粒度互斥，统一串行策略变更、预占、结算、提醒和重算；平台配额另有系统渠道锁。公共工作单元按稳定锁标识排序。这样不依赖动态策略查询前的锁集合，也不会部分提交多限额。高流量时可在保持协议下进一步细化锁。
- 金额硬预算要求匹配币种的有效价格与有界输入/输出。子集从父维度扣除，上限使用父子计价树中的最高单价，防止缓存折扣或推理高价造成低估。未配置的独立维度、缺失计量或关系不一致均保留未定价。非金额预算可在缺价时按 Token/次数控制。
- 价格按模型与生效时间选取并固定到尝试。累计事件替换当前有效值，最终供应商报告优先于估算；同连接、请求、版本内容冲突会拒绝。增量事件也去重，迟到增量归回原尝试。供应商报告替换估算及价格重算均追加 `usage_adjustments`，不覆盖历史依据。
- `HELD` 是尚未发送；`mark_sent` 提交发送意图后为 `PENDING`；最终有效用量可结算后为 `SETTLED`；确认未发送才可 `RELEASED`。客户端断流、超时、取消、Worker 退出和到期均不能证明已发送调用成本为零。补偿保留未知调用，等待 07/17 的供应商核实或晚到事件；重复补偿不重记或重释。扫描后在释放事务中再次检查到期，避免释放刚续期的预占。
- 策略每次修改创建公共 `resource_versions` 中不可变的 `budget_policy` 版本，原预占保留策略版本、时区及周期。子预算按同单位、币种、时区、周期限制在渠道总额度内。默认提醒为 80%/100%，同周期阈值只创建一条记录并保存解除/再次触发轨迹，仅站内展示。
- 汇总来自单一账本，请求数与尝试数分开；成功率分母是结果明确的尝试。已知 Token 合计附带缺失数量，费用分已计价/暂估/未定价和币种。折算保留原币、汇率日期、来源与折算值。查询返回计算时间、水位及价格完整性，持久化聚合可重复重建。
- 预算控制存储不可用时拒绝新调用并返回 `BUDGET_CONTROL_UNAVAILABLE`。硬预算约束新调用，第三方最终迟报仍可增加历史成本并阻止后续准入。

## 数据、调度与运行

迁移 `0004_usage` 新增 13 张表；预算版本复用公共表，未创建第二份版本内容库。迁移从 `0003_channels` 分支，当前与 07 的分支由 09 合并后衔接 10。全部普通索引和中文字段注释已进入冻结定义与模型归档。

新数据域创建事务现在初始化公共内容恢复屏障。已有范围不会自动绕过恢复校验；如范围在此前版本已经创建，由内容恢复流程确认后初始化或完成恢复。

运行 `make migrate`，API 和 Worker 使用现有命令；另启动 `make scheduler` 启动 Celery Beat。`usage.sweep` 每 60 秒扫描持久化任务，处理导出及到期预占；Worker 可重复执行。导出短事务领取五分钟执行租约，事务外生成文件，重新鉴权后再发布文件引用。失败记录中文原因，崩溃后可接管过期租约。导出文件有效期七天，普通明细上限 20 MiB；超限要求缩小筛选范围。文件按渠道隔离，下载前后重新验证创建人的当前权限，不公开对象地址。CSV 单元格对公式前缀加转义。

平台汇总产物归 `system`，记录明确的实际渠道集合、筛选口径、时区、完整性和导出人；不会迁移业务账本归属。普通导出登记 `usage_export` 清理处理器，与内容删除屏障复用。完整保留/删除传播调度属于 25。

`contracts/channels/UsageView` 的 Token 字段改为可空，并新增请求数、暂估费用、缺失数量、价格完整性和更新时间；05/06 消费端已同步，旧消费者须处理 `null`，不能继续显示零。核心 `UsageEvent` 契约及其夹具未改。
