# 04 账号认证与授权交接

完成日期：2026-10-02。对应 [方案 04](../../代码编写执行方案/04-账号认证与授权服务.md)、[账号与权限需求](../../需求文档/02-账号与权限管理.md)。模型及公共契约增量为 **1.1.0**，验证见 [iam-validation.md](iam-validation.md)。开发规则仍引用 [rule.md](../../rule.md)。

## 启动与初始化

```bash
uv sync --locked
make migrate
uv run creativity-iam init-admin --login-name admin --display-name 管理员
```

初始化命令以隐藏输入读取两次密码，密码不能通过命令参数传入。命令在同一事务创建六类内置角色和唯一初始管理员；已有账号时返回冲突。应用启动不创建账号、默认密码或数据库表。初始化账号及随后创建的账号均需先修改初始密码；本人改密成功返回 204，并撤销该账号全部旧会话，使用新密码重新登录。

密码使用每条独立的 32 字节随机盐及 PBKDF2-HMAC-SHA256、600,000 次迭代，只保存摘要。密码长度为 12–256 字符。登录名做 NFKC、首尾空白去除和大小写规范化，规范化后的登录名在系统渠道互斥查重。账号登录名不可改绑。登录对规范化账号及实际连接 IP 分别限速，窗口 300 秒、上限 10/60 次；成功尝试也计数，限速窗口到期后重试。未识别账号仍执行相同口令摘要工作；不使用客户端转发头建立限速身份。

新增可配置项 `CREATIVITY_MANAGEMENT_TOKEN_TTL=7200`、`CREATIVITY_SERVICE_TOKEN_TTL=3600`，允许 60–86400 秒。普通请求不续期，服务 Token 另外受 Key 到期时间限制。

## 接口与界面契约

以下接口均位于 `/admin/v1`，完整输入、输出和错误定义见 [OpenAPI](../contracts/openapi.json)。

| 路径 | 行为 |
| --- | --- |
| `POST auth/login` | 返回不透明 access_token、Bearer 类型、固定到期信息及 must_change_password |
| `POST auth/change-password` | 校验原密码，更新本人凭据并撤销旧会话 |
| `POST auth/logout` | 持久化本次会话撤销意图，再清理 Redis |
| `GET auth/session` | 返回用户、服务端过滤的导航与动作、当前工作区及到期信息 |
| `GET auth/channels` / `POST auth/channel-context` | 过滤可进入的渠道/环境/数据域，原子替换工作区 Token；已接入 05 的生产渠道读取器 |
| `GET/POST accounts` / `PATCH accounts/{user_id}` | 平台账号列表、创建、显示名称/状态/平台角色修改 |
| `POST accounts/{user_id}/reset-password` | 管理员重置初始凭据，并立即使原会话失效 |
| `GET roles` | 仅返回操作者可授予的内置角色及中文动作名称 |
| `GET channels/{channel_id}/members` / `PUT/DELETE …/members/{user_id}` | 读写唯一成员记录；两个页面入口共用该服务 |
| `GET channels/{channel_id}/resource-grants` / `PUT/DELETE …/resource-grants/{grant_id}` | 读写资源授权，撤销保留历史记录并清空允许动作 |
| `GET audit-events` | 仅返回当前获授权范围的审计元数据 |

创建成员和资源授权时 `revision=null`；修改时传当前 revision；DELETE 通过查询参数传 revision。重复登录名、重复成员首次创建、重复资源授权自然键和旧 revision 返回 409。账号列表与审计查询支持有上限的 limit。响应提供显示名称、角色名称、环境名称、数据域名称、动作名称及状态标签；关联对象不可解析时名称返回 null，不用编号替代。认证和会话响应禁止缓存。

首次凭据未修改时，仅允许改密及退出，其余接口返回 `PASSWORD_CHANGE_REQUIRED`（403）。正常身份缺失/失效或用途不符返回 401，操作无权返回 403，跨渠道/数据域对象返回 404，Redis 或当前状态依赖缺失返回 503。

## 认证与撤销

`core/auth/tokens.py` 生成 32 字节随机 Token，仅用 SHA-256 摘要定位 Redis KV。三种用途为系统临时登录 `login`、渠道管理 `management`、接入服务 `service`，不能互换。系统登录会话可查询本人身份并访问获授权的平台账号及系统审计接口；不能用于公共业务或文件接口。

Redis Lua 在一次操作中写入 KV 与固定 TTL、维护账号/成员/Key 的摘要索引。工作区切换先核对原值与 TTL，再签发新值并删除旧值及旧索引；并发切换只有一次成功。没有 TTL、超过 expires_at、被删除、绑定身份变更或用途错误的 Token 均拒绝。网络中断导致结果不确定时返回失败，客户端重新登录，不假称切换成功。

账号凭据代次 `credential_version` 在重置、本人改密、状态或平台角色变化时递增；成员 Token 绑定成员 revision。每次请求查询当前账号、成员和资源授权，同时通过注入端口查询渠道、环境、服务与 Key。单渠道成员变更只撤销其本渠道成员索引。

`iam_revocations` 与密码/状态变更同事务提交，Redis 清理在事务外执行。清理失败不回滚已经生效的撤销：账号/成员代次阻断旧会话，退出的摘要意图也在每次认证时检查。补偿只清理意图创建前签发的 Token，不误删随后合法登录的会话。

```bash
uv run creativity-iam reconcile-revocations --limit 100
# 或 make iam-reconcile
```

命令可重复执行，失败意图继续保留。05 对 Key 的撤销可复用 `revocations.enqueue` 与 `RevocationService.complete`，并先将 Key 的当前状态持久化。周期调度及保留清理在后续运维/保留单元接入。

## 授权与事务

`IamAuthorization.check(context, action, resource_type, resource_id)` 返回允许/拒绝及目标上可展示的动作；`require` 实现公共 Authorization 协议；`boundary` 为外部调用、SSE 发送/心跳和下载的统一复核入口。客户端传入的角色、菜单、granted_actions 或渠道头不会进入授权判断。

成员角色定义动作上限，资源授权限定资源、环境及数据域，二者取交集。channel 类型且 resource_id 等于本渠道的授权表示该渠道内的资源范围；其他类型只支持明确资源或该类资源的 `*`。数据域均显式列举，不提供跨渠道通配。`release:publish`、`data:export`、`data:read_sensitive` 必须独立授予。运行内容、会话、记忆、快照另要求敏感原文权限，文件下载同时要求下载、导出及敏感原文权限。平台治理不会自动获得这些读取权。

成员与授权变更复核操作者可授权范围，包括修改角色/数据域后可能激活的已有账号或角色授权。授权对象与资源不可改绑，首次创建按自然键互斥。锁清单在事务前生成，事务内复查数据库中的账号、成员及授权，Redis、口令计算与外部状态调用位于事务外。

IAM 写事务共同使用系统身份策略锁、目标渠道策略锁，并使用登录名、成员自然键、授权自然键和普通记录锁。系统锁与目标渠道锁使撤销与授权写入串行检查；每个事务保持短小。`member_keys`、`provisioning_keys` 供 05 合并到同一工作单元，不得在已有工作单元中另开事务。

审计复用公共 `audit_events`：平台身份事件归 system、环境记为仅用于控制面的 `control`；渠道事件归目标渠道。摘要只收录字段名称及实际影响的环境/数据域，不收录密码、Token、凭据或业务正文。涉及多个范围的权限变更保存 `affected_scopes`，审计查询按实际影响范围过滤。成功事件与写入同事务提交，拒绝操作在回滚后另行记录结果；审计读取权限不能用于读取业务原文。

## 后续模块接入

| 模块 | 交接入口及待完成部分 |
| --- | --- |
| 05 渠道 | 向 `build_iam_services` 注入 ChannelStateReader、WorkspaceDirectory、ServiceIdentityReader；开通渠道的同一 UoW 调用 `provision_first_member`。渠道主档、环境/域引用校验及其锁由 05 负责 |
| 05 首位管理员 | 需要平台 `channel:create` 和 `channel:govern` 权限；默认只初始化 channel_admin 普通动作。独立发布/导出/原文权限须通过 `independent_actions` 明确选择，由获授权的渠道开通操作建立初始可授权范围；没有公开绕过授权的初始化路由 |
| 05 服务认证 | Key 验证成功后调用 `SessionService.issue_service`；客户端/Key 动作与数据域继续作为实时上限。不能直接将 HTTP 正文构造成内部上下文 |
| 06 页面 | 使用 TokenResponse / SessionView / 各管理响应；按导航键登记页面。平台工作区导航与业务工作区导航分别由服务端过滤 |
| 各资源模块 | 注入 ResourceStateReader，按受信 Scope 解析当前对象及可读名称；未知对象返回不可见，不允许跨渠道查找后泄露信息 |
| 11/17 运行 | 受理时调用 `AuthenticationService.identity_source`，保存 IdentitySource 中的 Scope、source_type、principal_id、actor_id/client_id/key_id。恢复后建立 worker_context，并在每次外部调用边界重新授权；不保存浏览器 Token 或冻结旧权限 |
| 17 流连接 | 建连验证 Token，每次发送/心跳调用 boundary；收到 401/403/503 停止业务推送。响应已开始后的鉴权失效事件及关闭连接由 17 实现 |
| 18 委托 | 注入 SubjectAuthorityReader，返回已验签、有效受众与来源的当前主体授权；有效动作是主体、Agent、client、Key 的交集。未注入时服务业务访问拒绝 |

05 已完成真实渠道、环境、数据域、Key 和接入服务状态装配，工作区与 Key 换取 Token 已可用；详见 [渠道交接](channels.md)。暂停治理使用独立管理入口，业务调用不受此豁免。新增数据域的显式管理员授权仍由本模块在渠道事务中维护；数据域授权只覆盖其实际环境和域。原 IAM 单元夹具继续隔离测试身份内核，新的真实渠道夹具位于 tests/integration/channels。18 的主体委托、06 页面、17 的 SSE 传输和 26 全链路联调仍按所属方案交付。

06 已交付管理页面及最小响应组装增量，含返回平台工作区；成员与授权继续复用本模块记录。见 [06 交接](workspace.md)。
