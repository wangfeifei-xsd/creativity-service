# 19 渠道与接入边界解耦

2026-10-02。依据 [执行方案 19](../../代码编写执行方案/19-渠道与接入边界解耦.md)。开发规则引用 [rule.md](../../rule.md)。验证见 [验证记录](access-decoupling-validation.md)，模型变更见 [1.4.0](data-model/changes.md)。

渠道开通不再选择固定业务类型。`business_type` 是可选展示文本，最多 32 字符；历史 `gamerental/playmate` 保留原值和中文名称，不参与路由或授权。初始域和后续数据域均必须显式填写 `external_scope_type`（最多 64 字符）与 `external_scope_id`（最多 128 字符）。支持源系统的中文、冒号及斜杠等标识；拒绝空值、首尾空白和控制字符，不改写、大小写折叠或自动补齐源值。

映射仍在渠道与环境内通过事务锁互斥去重。委托先按 Token 的渠道、环境、client 查独立签名密钥并验签，再精确解析映射，最后检查当前 Key、服务及数据域授权。没有默认域回退。请求、模型以及元数据分类均不能改变已认证范围。

## 兼容清单

| 对象 / 入口 | 现状与处理 | 历史事实 |
| --- | --- | --- |
| 渠道创建、列表和页面 | 移除必选分类及两业务专用字段，分类可留空；未知类型不显示为默认域 | 原分类不改写；旧渠道请求仍接受 |
| 数据域 | 统一输入外部类型、编号；配置页直接显示这些必要技术标识 | `default/default`、`club/<编号>` 原样保留；不得自动转换 |
| Key、client、独立委托密钥 | 原认证、轮换与 nonce 协议沿用；新增 `/admin/v1/delegation-keys/options` 独立列出授权服务 | 不替换或重签历史 Key；client_id、kid 和来源可追溯 |
| Agent | 新增 `workflow.v1` 通用流程；四个通用执行入口与旧入口分开 | `matching.v1/risk.v1/analysis.v1` 保留原拓扑校验与执行；版本、摘要和快照不自动更新 |
| Agent 管理页面 | 新增向导只列通用模板；编辑旧版时显示当前旧入口，可主动选择通用流程 | 保存旧配置不隐式改 entrypoint；迁移使用新草稿，经校验与发布后切换映射 |
| 旧 HTTP 接入 | `/admin/v1/integrations*` 与旧凭据端点标记 deprecated，管理页标识为旧 HTTP 连接 | 保存、验证和运行旧绑定继续可用，不删连接、工具或测试记录 |
| MCP 工具目录 | 继续使用 MCP 连接、发现、导入和工具版本模块 | 不把旧八类 operation 当成新业务工具清单，不新增业务适配器 |
| 历史契约 | `integrations/openapi-v1.json`、`DelegationClaims.schema.json`、旧签名向量保持不变 | 当前生成契约另存 `openapi-v1.1.json` 与 `DelegationClaims-v1.1.schema.json`；线上的签名串和头版本不变 |
| 未实现领域表 | 03 已撤销的 12 张匹配、风险、分析设计表继续退出清单 | 本次不新建场景表，结果、证据、产物复用公共模型 |

`ChannelView.business_type/business_type_name` 现在可为 null。旧消费者升级时需要同步当前 OpenAPI，不能假定新建渠道一定有分类；旧渠道返回的历史值不变。`external_scope_type_name` 仅保留旧类型的展示兼容，未知类型为 null；配置值读取 `external_scope_type`，不猜测名称。

## 通用 Agent 与配置迁移

通用执行入口为 `structured.v1`、`workflow.v1`、`tool_loop.v1`、`stateful.v1`，业务调用编码由渠道自行配置。`workflow.v1` 沿用 `workflow_type=template` 的配置流程执行能力，允许通过配置定义步骤、数据来源和流转，不注册每个业务的处理器。任意代码执行仍不开放；模型和 MCP 工具按已发布依赖及当前授权执行。

[examples/agents](../examples/agents/README.md) 提供三个可直接作为创建接口正文的配置样例，均使用 `workflow.v1`。它们是初始配置，需绑定本渠道的模型、提示词以及业务方工具/Skills 后校验、调试和发布，不代表实现了领域规则。旧定义的逐字段兼容样本在 `contracts/agents/legacy-v1/`，已与修改前代码产生的三个定义核对一致。

迁移已有 Agent 时，从旧版本创建新草稿，选择通用流程并配置所需依赖；校验、调试、评测后发布。旧发布版本和已受理任务继续读取各自冻结快照。不能直接更新 `resource_versions.content`、运行快照或摘要。

## 旧 HTTP 连接迁移清单

| 原对象 | 配置迁移目标 | 切换条件 |
| --- | --- | --- |
| 连接地址、环境、域及服务凭据 | 新建同渠道、同环境的 MCP 连接并单独配置服务认证 | 业务方部署 MCP 封装，平台完成发现与健康验证；不复制密文跨渠道使用 |
| `operation_paths`、字段转换、字典/候选/报价/政策/指标 | 由业务 MCP Server 提供工具名称与 schema，字段与领域计算在源端维护 | 发现后导入、授权、发布新工具版本，不能直接复用旧 operation 作为完整目录 |
| 工具绑定中的旧连接 ID / 修订 | 新工具版本绑定新 MCP 连接与已发现工具 | 新 Agent 草稿显式换绑，验证输出结构、证据与权限；旧版本继续引用旧连接 |
| HTTP 契约测试、已保存结果和证据 | 作为旧协议的审计记录保留 | MCP 验证生成自己的记录，不能把旧测试通过状态继承为 MCP 已通过 |
| HMAC 委托 / API Key | 无需因工具迁移而重建 | 仍独立轮换；当前主体复核由 20 接通 |
| 最后一个旧工具使用者 | 停用旧连接前盘点工具版本、Agent 版本和在途运行 | 先完成依赖迁移与停止新增调用，再按保留策略处理，不自动删除 |

本地开发库盘点业务渠道为 0，旧 HTTP 连接为 0，无需改写存量业务配置。迁移前后 87 张已实现表的记录摘要一致；保留系统渠道 1 条。见 [盘点结果](access-decoupling-inventory.json)。这份盘点只覆盖当前本地开发库，其他部署应运行自己的只读盘点。

## 数据库升级与回退

`0020_access_decoupling` 接在 `0016_agents` 后，只调整分类与两列映射的中文注释。原列已允许空值，存储类型、索引和全部记录保持不变。旧 `0003_channels` 及 `baseline_v0003.json` 不变；运行元数据显式使用 `baseline_v0020.json`，避免重跑旧迁移得到新的历史定义。

在 `creativity-service` 目录执行：

```sh
uv run python scripts/inventory_access.py --output /tmp/access-before.json
uv run alembic upgrade head
uv run python -m creativity_service.core.database.audit --database
uv run python scripts/inventory_access.py --output /tmp/access-after.json
```

只读盘点不输出密钥、Token 或业务正文；部署人员比较 `record_fingerprints` 并逐条记录旧 HTTP、Agent 配置迁移情况。迁移降级到 `0016_agents` 只恢复注释，不能使旧应用自动支持空分类、自定义域或 `workflow.v1`。应用回退应先核对新配置使用情况，保留兼容读取能力，不通过删除或改写新渠道恢复旧代码。

## 后续交接与剩余问题

- 20：接通 MCP 受信主体传递和配置式 `CurrentSubjectReader`；当前缺少读取器时后台复核继续拒绝，不视为 MCP 端到端接入已完成。
- 21/22：配置导入、技能绑定与统一调用样例的完整交付；19 的示例不包含真实供应商凭据或业务领域计算。
- 23：以固定平台构建物完成两种外部 MCP/新业务配置验证。19 已证明自定义渠道、显式映射、签名及凭据可用，INT-A07 的完整证据仍由 23 提供。
- 25/26：现有生产装配尚未登记 `TaskLifecycleGuard`；存在任务表时渠道归档会明确返回 503。19 保留该拒绝边界；渠道生命周期回归使用显式的无任务测试检查器，不把夹具当成生产装配完成。
- 26：真实模型、外部业务 MCP、业务政策与完整隔离链路验收继续按各自计划执行。
