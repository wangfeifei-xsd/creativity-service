# 19 验证记录

日期：2026-10-02。交付与迁移清单见 [19 交接](access-decoupling.md)。使用工程锁定的 Python 3.12、Node.js 22、pnpm 10.32.1、本地 PostgreSQL/Redis 与 Chrome。业务工具、模型和发布证据使用已有受控夹具，不代表真实业务 MCP 或真实供应商验收。

| 验收范围 | 证据 |
| --- | --- |
| CHN-F16、CHN-A01、INT-A03 | 真实 HTTP 开通省略分类、自由文本分类及历史两分类的渠道，均可使用 `源系统/workspace` 和 `研发:001/甲` 显式映射；登记服务、签发 Key、换 Token 成功 |
| CHN-A15、隔离与拒绝 | 自定义映射 6 路并发只成功一次；相同编号在另一渠道/环境独立保存；缺少、空白、控制字符及 PATCH 改绑均拒绝；委托找不到映射不落默认域 |
| 独立委托与来源 | 未创建 HTTP 连接即可创建 HMAC 凭据和解析主体；同号主体、同 nonce、同映射在两个渠道不串用；跨渠道签名、伪造环境、未知映射、改变主体以及停用域均拒绝 |
| 历史渠道 / Key / 委托 | 旧两分类、原映射和中文显示保留；迁移往返后 Key、client、旧 HTTP 连接、HMAC 及 nonce 全行摘要不变，同委托安全重发保持原 delegation_id |
| 通用 Agent | `workflow.v1` 通过配置执行两个模型步骤；三个旧入口仍能执行、重复投递不重复模型调用；运行快照不变，迁移后已受理旧任务仍成功 |
| 旧定义契约 | 三份 legacy-v1 固定样本与修改前 Git 内容产生的定义逐字段一致；当前通用模板和样例不需要按业务注册入口 |
| 存储迁移 | 全链升级、重复升级、降级、离线 SQL 与实际 schema 审查通过；`0020_access_decoupling` 不改已有迁移、索引或数据 |
| 前端 | 显式映射、分类留空、新建数据域、旧 Agent 编辑保留入口、旧 HTTP 目录故障时独立委托仍可配置；长表单标题和确认按钮固定，正文可滚动 |
| CHN 管理页面真实联调 | 真实 IAM 与独立数据库/Redis 下完成首次改密、开通自定义 workspace 域、服务登记、Key 创建/轮换/吊销、成员与授权、暂停、恢复和归档；测试实例退出后清理 |
| INT-A07 的 19 边界 | 通过配置创建任意类型渠道和通用流程。完整 MCP/Skills/Agent/API 与平台构建不变的证明仍归 20–23，不以本单元夹具替代 |

## 检查命令与结果

后端工程运行 `make check`，最终通过 Ruff、262 个源文件的 mypy strict、**152 项非集成测试**、全部契约一致性、模型归档及存储源码审查。日志在本地 `.logs/19/service-check.log`。

相关集成矩阵：

```sh
uv run pytest -q tests/integration/channels tests/integration/integrations \
  tests/integration/agents tests/integration/runtime \
  tests/integration/core/test_storage.py tests/integration/test_migrations.py
```

初轮 101 项通过，1 项旧渠道归档测试失败：已安装任务表，但夹具仍假定未装配任务模块。修正用例为先断言缺少检查器返回 503，再登记明确无任务的测试检查器验证状态/审计流程，生产拒绝行为不变。另补充旧 Key/HTTP/nonce 的迁移盘点测试；修正用例、新增用例及消除测试载荷序列化告警后的委托用例最终 **3 项通过**。合计 **103 个相关集成用例完成验证**。首次矩阵和最终重测日志分别在 `.logs/19/integration-initial.log`、`.logs/19/integration-recheck.log`。

前端 `pnpm check` 最终通过：OpenAPI 类型一致性、TypeScript、ESLint、**19 项 Vitest** 及生产构建。`PLAYWRIGHT_CHANNEL=chrome pnpm exec playwright test tests/channels.spec.ts tests/agents.spec.ts tests/integrations.spec.ts` 最终 **13 项通过**。`WORKSPACE_LIVE_API=http://127.0.0.1:18006 PLAYWRIGHT_CHANNEL=chrome pnpm exec playwright test tests/workspace-live.spec.ts` **1 项真实联调通过**。浏览器样例覆盖 1280 像素和 390 像素页面，截图已人工查看；日志与截图保存于前端 `.logs/19/`。

真实浏览器测试复用 `tests/support/workspace_server.py` 的隔离实例，专门验证渠道/IAM 协议；实例安装 05 表基线，不代表任务模块已完成生产归档装配。全量最新迁移由上述服务端集成和开发库审查覆盖。

## 本地配置盘点与迁移结果

开发库从 `0016_agents` 升级至 `0020_access_decoupling`，`audit --database` 通过。迁移前后对 87 张已实现表在明确枚举的渠道范围内计算记录摘要，结果一致。当前只有系统渠道记录，没有业务渠道、Key、旧 HTTP 连接或 Agent 运行需要迁移；不会为测试凭空创建生产配置。结果见 [盘点记录](access-decoupling-inventory.json)。有历史配置的路径由隔离测试数据库验证，不能把当前空库当成唯一兼容证据。

已知非阻断提示：原技能重复 ZIP 文件名用例的预期警告；前端既有大包体积提示。剩余实现边界、应用回退条件及生产任务检查器装配缺口见交接文档。
