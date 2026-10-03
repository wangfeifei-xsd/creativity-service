# 第 28 号增强验收记录

2026-10-03，E1 → E4 按用户选择连续实施；独立方案、后端、前端、新增迁移、模型档案与 SDK 已落地。本记录仅归档第 28 号工作，不覆盖或重记 `docs/acceptance` 的既有验收。第 27 项部署与上线保持跳过，P2 保持暂缓。

## 环境与证据口径

- Python 3.12 虚拟环境；前端使用工作区 Node 22、pnpm 10.32.1。
- PostgreSQL 17、Redis、对象存储为本地依赖；数据库测试创建独立 schema 并迁移至 `0031_identity_operations`，结束后清理测试数据。主 public schema 未升级。
- 本地数据库启用 pgvector 0.8.2；Docker 隔离验收镜像固定为 `sha256:612421b9ff0e627431d04ce9d86008ffa84350618356475efeff6d99befd6b88`（本地验收 Python 3.14.8 镜像）。正式构建材料使用 `deploy/Dockerfile.sandbox`，正式镜像与主机仍需按实际环境验证。
- 浏览器使用本机 Chrome 和 Playwright，本次页面 API 采用 route fixture；服务层集成测试实际访问数据库/Redis，模型、OAuth 提供方、外部身份、Webhook 接收器使用受控夹具。没有真实第三方账号联调或正式部署。

## 最终检查

| 检查 | 结果 | 记录 |
| --- | --- | --- |
| 后端 `make check` | 格式、ruff、mypy（325 个源文件）、185 项单元/契约测试、全部契约快照、数据模型及存储审查通过 | [日志](logs/service-check.log) |
| 后端 `uv build` | sdist 与 wheel 构建成功 | [日志](logs/service-build.log) |
| 前端 `pnpm check` | API 生成一致、类型、lint、19 项单元测试、构建通过 | [日志](logs/web-check.log) |
| 前端完整 Playwright | 62 通过、1 跳过 | [日志](logs/browser.log) |
| 批次错误后的同键重试 | 修复弹窗字段标识和按钮可访问名称后，连续三次浏览器验证通过 | [日志](logs/browser-retry.log) |
| IAM + 身份扩展 + 存储/迁移 | 31 通过，包含旧 Token 兼容与 platform_admin 标识保护 | [日志](logs/iam-storage.log) |
| 容器隔离 + 告警/账单边界 | 11 通过，含真实 stdio、超期回收、并发停用保留事件、精确差额/迟报 | [日志](logs/boundaries.log) |
| Skills 实际计算 + OAuth + 账单 | 4 通过 | [日志](logs/script-oauth-statements.log) |
| OAuth 刷新丢失/过期清理 + Token + Python SDK | 7 通过 | [日志](logs/identity-sdk.log) |
| Python SDK + 工具/分析源 | 29 通过 | [日志](logs/sdk-analysis.log) |
| 暂停恢复 + 写工具 | 8 通过；另在专项集成中增加无效成功回执不重放场景通过 | [日志](logs/write-interruptions.log)、[专项首轮](logs/focused-first-pass.log) |
| TypeScript SDK | 编译、202 同键重试、SSE 游标/跨块 CRLF、失败/超时终态、错误信息与签名上下文通过 | `sdks/typescript/test.mjs`，输出“TypeScript SDK 协议验证通过” |

各行存在重复测试，不合并相加。完整收集列表见 [collection.log](logs/collection.log)。前端构建仍提示主 bundle 超过 500 kB，属于构建性能提示，本次构建成功。

## 全量集成与收尾修正

收尾修正前启动的 `pytest tests/integration -q` 持续约 31 分钟，结果为 **379 通过、2 跳过、1 个旧测试断言失败**（[原始日志](logs/integration-first-pass.log)）。失败来自脚本测试按旧结果层级读取 `sum`；实际统一 run 已成功。修正为读取 ToolResult 的 `data` 后，真实容器单项及后续脚本/OAuth/账单组均通过。该全量进程在修改前已收集测试，所以原始 traceback 的源代码行与后来文件行号存在差异。

随后针对改动运行了上表中的专项回归，没有把这些结果写成“最终源文件又跑过一次全量集成”。专项首轮另有新 Token 兼容测试夹具遗漏 credential_version，补齐后 IAM 全套 31 项通过。写执行模块在收尾时发生的误覆盖已恢复为 WriteExecution，并通过类型检查、8 项暂停/写入测试和含无效回执的后续专项。

两个后端跳过项分别要求独立运行性能测量和固定构建业务浏览器脚本；前端跳过项要求独立真实 IAM/渠道服务环境。这些跳过不记为通过，也不借用其他会话的验收记录。

## 可审阅页面

- [流程编辑](screenshots/flow.png)：新增审批节点、连线与草稿保存。
- [账单核查](screenshots/statement.png)：币种差异、金额缺失、迟报与原运行链接。

## 交付与启用

配置步骤及实际限制见 [configuration.md](configuration.md)，SDK 接入见 [SDK README](../../sdks/README.md)，分组说明见 [E1](../../../代码编写执行方案/28-E1-检索与配置体验.md)、[E2](../../../代码编写执行方案/28-E2-受控执行扩展.md)、[E3](../../../代码编写执行方案/28-E3-分析与集成.md)、[E4](../../../代码编写执行方案/28-E4-身份与运营.md)。外部凭据、固定镜像、出站白名单和真实数据源按使用环境配置后启用；本次没有公开 SDK 包、切换主数据库或执行第 27 项上线操作。
