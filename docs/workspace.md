# 06 管理工作区与权限页面交接

完成日期：2026-10-02。依据 [方案 06](../../代码编写执行方案/06-管理工作区与权限页面.md)、[渠道需求](../../需求文档/01-渠道管理.md)、[账号权限需求](../../需求文档/02-账号与权限管理.md)。全局规则引用 [rule.md](../../rule.md)。页面登记与公共组件使用见 [前端协议](../../creativity-web/docs/workspace.md)。

## 已交付

前端 `src/app/workspace/` 提供会话初始化、当前渠道/环境/数据域、服务端工作区选择、返回平台与退出。`features/auth` 实现登录及首次改密；账号、渠道、成员、资源授权和审计使用 04/05 的真实接口。渠道详情提供概览、环境、业务数据域、接入服务、Key、成员与权限、资源、用量和审计入口。

渠道成员独立页与渠道详情共用 `MembersPage`，资源授权共用同一组件与 IAM 原始记录。首位管理员通过服务端账号选项选择，接入 Key 明文只存在创建/轮换结果弹窗；关闭、切换页面或工作区后释放，列表仅显示服务端掩码。浏览器只在 sessionStorage 保存不透明管理 Token，不保存密码、Key 或业务缓存。

初始化无条件请求会话接口，首次凭据按服务端 `PASSWORD_CHANGE_REQUIRED` 进入改密。导航、操作、角色、账号选项与授权范围由服务端组装。路由登记仅映射页面组件；直接访问仍调用受保护接口。切换前撤销页面请求代次，取消旧请求并卸载页面树；响应头、响应正文、下载完成时均复查请求代次，迟到 401 不会清空新会话。普通请求不计算本地过期时间。

编辑共用 `EditorDialog`，单次在途提交互斥；409 保留输入，显式读取最新 revision 后再次确认，失败不关闭表单。渠道状态操作使用 `ImpactDialog`，读取实际有效 Key、接入服务、未完成任务和阻塞原因；真正变更仍由 05 在事务内重新检查。字段选项加载结束后才允许打开依赖它们的配置表单。

## 响应与会话增量

| 接口 | 责任 |
| --- | --- |
| `GET /admin/v1/channel-create-options` | 平台开通权限下的可选账号、业务类型、环境和初始独立授权动作 |
| `GET /admin/v1/channels/{channel_id}/page` | 渠道详情可见标签与具体操作；平台治理不提供签发或轮换 Key 入口 |
| `GET /admin/v1/channels/{channel_id}/access-options` | 当前渠道的成员/授权入口、可授权角色、成员名称、资源选项、环境/数据域及动作 |
| `POST /admin/v1/auth/platform-context` | 经当前身份复核签发系统登录 Token、原子撤销旧工作区 Token、记录平台切换审计；系统身份不扩大业务授权 |
| `GET /admin/v1/channels` | 平台治理返回渠道目录；业务工作区只返回当前获授权渠道 |

响应组装分别位于 `modules/iam/presentation.py`、`modules/channels/presentation.py`。资源选项包含获授权渠道、数据域、可管理资源类型及已有明确授权对象；未登记或无法解析名称的对象不以编号补展示。写入、并发检查和撤销仍调用 04/05 原服务。没有新增数据库表、字段或迁移，模型基线无需变化。

07–10 在各自 `features/<module>/registration.ts` 导出 `registration` 或 `registrations` 自动接入统一入口；服务端导航已包含 models/prompts/tools/usage。其协议适配、计量账本、提示词和工具业务仍由对应单元交付。资源引用或用量端口未装配时，页面明确显示不可用或服务故障，不把缺失当零。

## 验证记录

| 验证 | 结果与范围 |
| --- | --- |
| `pytest tests/integration/iam tests/integration/channels -q` | **49 项通过**，真实 PostgreSQL/Redis；包含新增 3 项页面选项、平台切换、双入口与直接访问校验 |
| IAM/渠道模块 Ruff、format、mypy | 通过；含新响应组装与测试支持服务 |
| 06 前端 `pnpm check` | 在独立验证副本通过 API 类型一致性、TypeScript、lint、**14 项单元测试**及生产构建 |
| 浏览器异常与响应式用例 | 登录前会话查询、503 重试、本地过期信息不参与鉴权、409 输入保留与重复提交、403 直接访问、375px 页面通过 |
| 真实浏览器闭环 | Chrome + 真实 PostgreSQL/Redis，通过首次改密、账号创建、开通渠道、进入工作区、接入服务、创建/复制 Key、归档阻塞、成员双入口、资源授权、暂停/恢复、零重叠轮换、吊销、归档、返回平台与退出 |
| 切换隔离 | 单元验证响应头/正文阶段的迟到数据与 401；浏览器验证切换清空筛选并拒绝旧渠道响应，对应 IAM-A14/CHN-A10 |

验证副本在 `/tmp/creativity-ui06-check/`，使用同一锁定依赖，排除当时仍在写入的 07–10 页面。共享工程阶段性出现未完成组件导入和类型错误，因此上述通过口径只覆盖 06；最后一次共享 `pnpm check` 在 07 的 `features/models/ProvidersPage.tsx:21` 报 `template_content` 可能为空（TS18048），没有改写并行单元文件。配置批次合并后的全量检查由整合过程重新执行。构建有 antd 主包大小提示，不影响构建通过。

真实浏览器测试使用 `tests/support/workspace_server.py` 创建独立 PostgreSQL schema 和 Redis 前缀，测试仅迁移到 05 基线，退出时自动清理；不创建默认生产管理员。复现步骤：

```bash
# 服务端终端
uv run python tests/support/workspace_server.py
# 前端终端；每次完整重跑前先重启上述临时服务
PLAYWRIGHT_CHANNEL=chrome WORKSPACE_LIVE_API=http://127.0.0.1:18006 pnpm exec playwright test tests/workspace-live.spec.ts
# 不依赖真实服务的页面验证
PLAYWRIGHT_CHANNEL=chrome pnpm exec playwright test tests/shell.spec.ts
```

浏览器真实 Key 用例关闭追踪文件，测试口令只属于隔离实例。首批真实业务凭据、供应商验证和全部模块全链路仍归 17/26/27，不属于本单元完成证据。
