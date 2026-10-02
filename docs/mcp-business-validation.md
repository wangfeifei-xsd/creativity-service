# 20 验证记录

日期：2026-10-02。实现与配置方式见 [MCP 业务工具接入](mcp-business.md)。使用工程锁定的 Python 3.12、Node.js 22、pnpm 10.32.1、本地 PostgreSQL/Redis 和 Chrome。

业务工具来源为 [两套受控 MCP 服务](../examples/mcp/README.md)，通过真实本机 TCP Streamable HTTP 调用；数据库、IAM、委托签名、工具发布、Agent 发布与 Worker 执行使用实际实现。模型响应由既有受控替身提供，发布测试和评测证据也使用测试夹具。没有连接生产业务数据或真实模型供应商。

## 验收证据

| 范围 | 已验证行为 |
| --- | --- |
| INT-F01/F06/F11，发现与发布 | `archive.find-notes` 使用文档参数并返回摘录数组；`matrix.total` 使用带本地 `$defs/$ref` 的二维数组并返回合计与单位。两者仅通过 MCP 连接、凭据、发现、导入、授权、发布和 Agent 绑定接入，无业务专用适配器 |
| INT-F05，当前主体权限 | 专用 `access.review-current` 通过服务凭据独立复核。停用主体、扩大权限、过期、错域、协议错误、超时和缺配置均在业务工具提交前拒绝；源端权限收窄即时生效；身份工具不能导入模型工具目录 |
| TOL-A01、INT-F02 | 模型传入 subject、subject_id、data_scope、environment 或 `_meta` 均被拒绝；管理配置正文不能覆盖 channel_id；跨数据域不能读取复核绑定 |
| INT-F04/F07/F09/F10 | 保留结构化结果、源请求编号、数据版本、观测时间、来源证据和分页。empty、missing、partial 与远端无权限、超时可区分；错域结果、无授权实体、非法输出 schema、实际副作用与只读契约冲突均拒绝 |
| INT-F08，固定版本 | 源 schema 变化阻断旧工具；重新发现或恢复原 schema 不自动恢复已失效的旧绑定。凭据轮换后即使远端 schema 相同，旧发布绑定仍被拒绝 |
| Worker 与统一运行管线 | 一个 Agent 以 sync 提交并在等待窗口后返回 202，另一个以 stream 提交；请求 Token 和十秒声明自然到期后，Worker 按实时权限完成一次 MCP 调用。重复投递不重复调用；重新认证和重新签署 GET 委托后可读取 SSE 最终结果 |
| 授权撤销 | 渠道暂停、平台 Key 撤销、委托密钥撤销、主体撤销或复核配置停用阻断后续调用；缺少 CurrentSubjectReader 的 Worker 仍拒绝执行 |
| 并发与恢复 | 同 revision 的四路复核配置保存仅一次成功。新主体屏障四路初始化仅一条；父域未初始化或被阻断时拒绝；既有 BLOCKED 屏障不被重置；恢复了历史任务、产物或删除记录却缺屏障时要求恢复证明 |
| TOL/MCP 既有边界 | 相关回归覆盖工具白名单、缓存隔离、文件引用、结果体积、凭据隔离、地址限制、无重放、发布和运行边界。最终结果体积包含平台生成的来源证据 |
| 管理页面 | 身份复核从 MCP 唯一目录选择；保存不携带渠道、主体或数据域覆盖。身份工具只能进入复核配置；旧 HTTP 页签保留兼容。工具测试支持 JSON 参数并保留错误输入，受控测试结果明确标注来源 |

主体复核采用方案允许的“专用 MCP 身份工具”路径，没有另建任意 HTTP 身份查询入口。同步、流式提交和恢复执行复用同一个运行与工具执行器；此次 Worker 验证通过 `execute_message` 直接驱动真实执行器，独立 Celery 进程与真实模型全链路验收仍归后续单元。

## 检查命令与结果

服务端 `make check` 通过：Ruff、268 个源文件的 mypy strict、**153 项非集成测试**、OpenAPI/全部模块契约一致性、模型归档及存储源码审查。日志：`.logs/20/check-final.log`。

相关集成矩阵使用独立 PostgreSQL schema 与隔离 Redis 范围，结束后清理：

```sh
uv run pytest tests/integration/mcp tests/integration/integrations \
  tests/integration/tools tests/integration/agents tests/integration/runtime \
  tests/integration/prompts tests/integration/runs/test_identity.py \
  tests/integration/runs/test_migration.py tests/integration/test_migrations.py \
  tests/integration/core -q
```

新增 MCP 业务闭环 **16 项通过**，新增首次主体恢复屏障 **4 项通过**。相关模块完整回归结果与迁移记录在最终运行结束后归档；日志为 `.logs/20/regression-final.log`。

前端 `pnpm check` 验证 OpenAPI 类型、TypeScript、ESLint、**19 项 Vitest** 和生产构建。`PLAYWRIGHT_CHANNEL=chrome pnpm exec playwright test tests/mcp.spec.ts tests/integrations.spec.ts tests/tools.spec.ts` **12 项通过**。移动端宽度 390 像素，主体复核对话框截图已人工检查；截图为 `.logs/20/subject-review-mobile.png`。JSON 参数补充验证及最终前端工程检查日志为 `.logs/20-tools-final.log`、`.logs/20-check-final.log`。

## 联调修正与迁移

闭环联调发现并修复：Agent 发布把 MCP 的 ENABLED 状态误判为 ACTIVE；运行提示词渲染误要求配置读取权限；新委托主体缺少首次内容屏障；凭据修订后的同 schema 重新发现会使旧工具绑定恢复；新增源证据需要计入最终结果体积。SSE 测试按实际 GET 请求去掉 POST 幂等头并重新签名，签名校验规则保持生效。

浏览器初轮发现测试拦截范围过宽和选择下拉关闭动画期间立即截图的问题；调整为精确 API 拦截并等待下拉关闭后检查手机布局，最终用例通过。

新增 `0021_mcp_subject_review` 迁移仅创建主体复核绑定表及普通索引，不改变旧迁移建表入口。模型归档版本为 1.5.0。开发库迁移前只有系统渠道、没有历史运行，开发库升级与实际存储审查结果随最终记录归档。

已知非阻断提示为既有重复 ZIP 文件名测试警告与前端大包体积提示。真实业务 MCP 开发、部署、权限语义与真实模型供应商验证没有被本次受控测试替代；21/22 使用本单元协议与样例继续配置和 API 交付，23 另行证明固定平台构建物可复用。
