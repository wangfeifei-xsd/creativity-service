# 05 渠道管理与接入凭据交接

完成日期：2026-10-02。依据 [方案 05](../../代码编写执行方案/05-渠道管理与接入凭据.md)、[渠道需求](../../需求文档/01-渠道管理.md)；开发规则引用 [rule.md](../../rule.md)。模型与公共契约版本 **1.2.0**。验证见 [channels-validation.md](channels-validation.md)。

## 初始化与装配

```bash
uv sync --locked
make migrate
make channels-init
uv run creativity-iam init-admin --login-name admin --display-name 管理员
```

`channels-init` 幂等初始化唯一系统渠道，不开通业务渠道、不生成 Key；API 启动不建表或创建账号。已有管理员时跳过 `init-admin`。本次迁移 `0003_channels` 新建 9 张表，完整定义见 [渠道模型](data-model/modules/channels.md)。

`build_channel_services` 同时装配 IAM、真实 ChannelStateReader、ServiceIdentityReader、WorkspaceDirectory、渠道资源名称读取器和渠道服务。账号、成员、资源授权、Token 与撤销继续使用 04 的唯一实现。后续业务资源读取器需按资源类型组合到 IAM；未登记的业务资源与主体委托继续拒绝访问，不能以渠道元数据读取器替代。

## 开通及配置

平台管理会话调用 `POST /admin/v1/channels`，填写名称、编码、负责人、`business_type`、`first_admin_user_id`、初始 `environment` 和 `data_scope`。一次事务完成渠道主档、基础保存策略、环境、数据域、系统编码索引、首位管理员成员与授权及审计。环境名称初始使用开发/测试/验收/生产，可通过环境接口修改；编码创建后不变。

- 租号类型为 `gamerental`，数据域为 `external_scope_type=default`、`external_scope_id=default`。
- 陪玩类型为 `playmate`，数据域为 `external_scope_type=club`、`external_scope_id=原业务俱乐部编号`。
- 首位管理员默认获得普通渠道管理动作。发布、导出及敏感原文权限只能通过明确的 `independent_actions` 授予。
- 新增数据域可由平台治理会话明确填写 `administrator_id`，由 IAM 在同一事务授予该新工作区的普通管理员权限；成员 revision 更新使原管理会话失效，需要重新登录。新域不会自动扩展其他成员的数据权限。

编码经过 NFKC、去首尾空白、小写规范化；渠道编码在 system 索引中互斥查重。环境在渠道内唯一；数据域外部映射在渠道和环境内互斥查重。绑定关系不可 PATCH 改写。成员的环境和数据域按真实配对校验，支持一个成员进入多个环境，避免将两个数组的笛卡尔积视为业务范围。

接入服务填写 `environment`、`scopes` 与明确的 `data_scopes`。域必须同渠道同环境且启用；可调用动作必须属于服务动作集合，且不超过操作者在目标域内的当前授权。平台治理会话可检查配置及吊销 Key；创建接入服务、签发或轮换 Key 须使用获目标渠道数据授权的管理工作区，治理角色本身不会得到业务接入凭据。

## Key 与 Token

Key 使用 32 字节随机数，完整值仅创建/轮换响应的 `api_key` 返回一次，响应禁止缓存。数据库只保存 SHA-256 摘要与辨认前后缀，列表只返回掩码、状态、名称、能力名称、有效期、最近使用时间和配置 revision。没有查询明文或恢复接口；丢失创建响应后，管理员通过列表定位并轮换。

Key 和 system 中受限身份索引同事务提交。未认证的 Token 交换只能按完整摘要精确查询索引，再在目标渠道复核 Key、接入服务、环境、数据域和能力。服务 Token 由 04 签发；默认 3600 秒，到期不晚于 Key。`POST /api/v1/auth/token` 只接收 `api_key`，额外渠道/环境/权限字段拒绝，伪造头不能改变绑定关系。业务 Bearer 不接受 Key 原值。

轮换请求携带当前 revision、新有效期和 `overlap_seconds`（0–604800 秒），生成新的 key_id 并保留 client_id。旧 Key 的截止时间缩短到原有效期与重叠截止时间中较早者；零重叠直接吊销旧 Key。已轮换过的旧 Key 不允许再次轮换，使用新记录继续轮换。最近使用时间不改变配置 revision，避免正常高频换 Token 使管理员无法吊销。

每次请求及 worker 执行边界重新读取当前 Key、服务动作、数据域、环境和渠道。权限交集缩小立即生效；Key 吊销、到期、服务停用与环境停用立即拒绝旧 Token。Key 吊销在同一事务写入 IAM 撤销意图，Redis 清理失败仍由当前状态阻断，后续 `make iam-reconcile` 补偿。

## 工作区及生命周期

04 的 `auth/channels`、`auth/channel-context` 已接入真实目录。服务端验证账号、成员及资源授权后，Redis Lua 原子创建有 TTL 的新 Token、更新索引并撤销旧值。Redis 失败返回 503，不返回新工作区成功结果；并发切换只成功一次。既有任务的 Scope 不因浏览器切换改变。

暂停/恢复/归档通过独立治理入口授权。暂停后业务身份立即失败，管理人员仍可查看渠道元数据、凭据掩码、审计和影响预览并恢复；管理 Token 的普通业务入口也保持拒绝。整渠道操作要求覆盖全部真实环境/数据域，平台治理入口则单独校验平台权限。已归档渠道不可恢复或修改。

`GET channels/{channel_id}/impact?action=suspend|resume|archive` 返回当前 revision、有效 Key、接入服务、未终结任务数与阻塞原因。实际变更在事务内重新检查，不能凭旧预览绕过状态变化。归档要求先吊销有效 Key、结束或取消未终结任务，不删除历史用量和审计。尚未安装 runs 表时不存在运行记录；安装后未登记任务检查器会返回未知任务数并拒绝归档。

渠道、环境、数据域、服务和 Key 变更与 `channel_lifecycle_events` 同事务提交。`LifecycleService.register_tasks` 供 11 注入数据库任务检查器；任务受理和终结须共用渠道策略锁及检查器返回的锁，检查器不得访问远端。11/25 分别以 `runs`/`retention` 调用 `pending(channel_id, consumer)` 和幂等 `acknowledge`，消费事件按 event_id 幂等。事件持久保存原渠道、目标、环境、前后状态与修订，进程退出不丢失通知。周期投递与清理执行由 11/25 完成。

## 接口及后续交接

完整接口位于 [OpenAPI](../contracts/openapi.json)，包括渠道列表/详情/修改、环境/数据域/clients/keys、rotate/revoke、suspend/resume/archive、overview、audit-events、impact、渠道用量和明确渠道范围的平台用量。修改请求使用 `revision`；绑定字段不进入修改 schema。状态和关联名称由服务端提供。

| 后续单元 | 已交付入口 | 后续职责 |
| --- | --- | --- |
| 06 页面 | OverviewView、中文状态/名称、成员与审计路径、一次性 KeyCreated；前端生成类型已同步 | 工作区与渠道管理页面，向业务后端交付 Key |
| 08 用量 | `UsageReader.query(channel_id, scopes, query)`；明确枚举已授权环境/域，验证返回渠道及时间区间 | 实际账本和预算查询；未注入返回 503，不伪造零用量 |
| 资源模块 | `ResourceReferenceReader.references(scope)`、渠道资源名称读取器 | 注入真实资源引用和各自资源状态；未注入时概览引用为 null |
| 11/17 运行 | 稳定 client_id、IAM IdentitySource、实时状态读取器、TaskLifecycleGuard、生命周期事件 | 幂等受理、排队/在途任务停止和迟到结果归集 |
| 18 业务委托 | 环境内数据域映射、接入服务当前能力与域范围 | 验证主体委托，不以 Key 能力替代业务用户权限 |
| 25 保留 | 原渠道生命周期事件、保留策略、幂等消费确认 | 实际删除、恢复标记与清理流程 |
| 26 验收 | `tests/integration/channels/conftest.py` 的真实渠道、管理会话与服务 Token 夹具 | 运行、缓存、用量、恢复及页面全链路组合 |

[channels 契约目录](../contracts/channels/) 单独导出 LifecycleEvent、UsageQuery 和 UsageView，`make check` 核验其一致性。服务不会因尚未接入运行、用量或委托模块而允许未经授权的业务执行。

06 已完成上述页面交接及真实浏览器开通至归档流程，见 [06 交接与验证](workspace.md)。
