# 09 验证记录

日期：2026-10-02。验证使用本地 PostgreSQL、Redis 和独立测试 schema；测试结束清理临时 schema 与 Redis 前缀。未使用真实模型供应商，也未把运行替身标作 17 已联通。

| 需求 | 验证证据 |
| --- | --- |
| PRM-A01、F02/F04 | 有限渲染单测；错误类型、必填、长度、未声明、模板表达式与非法来源在模型调用前拒绝；HTTP 验证中文字段错误 |
| PRM-A02 | 两个并发创建同编码只成功一次；同 revision 并发写只成功一次；浏览器保留冲突输入并展示最新修订 |
| PRM-A03 | 调试后编辑草稿，旧快照、旧 revision 和旧结果保持不变；重复提交得到同一运行；浏览器读取旧配置快照 |
| PRM-A04/A06 的本单元范围 | 公共版本服务创建固定 Agent 依赖与历史快照；新发布及回滚不改变它们；环境和资源引用阻止退役。真实 Agent 执行结果留给 17/26 |
| PRM-A05、F03 | 指令式用户文本保持输入分区且不再次解析；平台身份不能由输入覆盖；系统/输出分区不能插入输入变量 |
| PRM-F05 | 脱敏、预览截断、Token 估算；未知上下文上限和剩余额度为空；Chrome 页面核验 |
| PRM-F06/F07 | 缺少运行器/证据拒绝执行或发布；无账单、错误快照摘要拒绝发布；发布权限独立于编辑权限 |
| PRM-F08/F09/F10 | 类型、来源、长度、必填和输出要求兼容性差异；引用影响与冻结/回滚行为 |
| PRM-F11 | 文本完整往返；真实 IAM 下无导出授权拒绝；独立授权后受控下载成功，导出不含敏感默认值和渠道授权 |
| 范围及删除 | 跨渠道版本不可读，跨数据域样例不可读；删除来源运行后历史测试和发布都被阻断；归档与冻结模型一致 |

执行入口：

```bash
# 服务端
.venv/bin/pytest tests/test_prompts.py tests/integration/prompts tests/integration/channels/test_prompts_http.py -q
.venv/bin/ruff check src/creativity_service/modules/prompts tests/test_prompts.py tests/integration/prompts tests/integration/channels/test_prompts_http.py
.venv/bin/mypy src/creativity_service/modules/prompts --follow-imports=silent
.venv/bin/python -m creativity_service.modules.prompts.export --check
.venv/bin/python scripts/render_data_model.py --check
.venv/bin/python -m creativity_service.core.database.audit

# 前端
pnpm check
PLAYWRIGHT_CHANNEL=chrome pnpm exec playwright test tests/prompts.spec.ts
```

提示词后端 28 项测试（含真实 IAM/HTTP 与 PostgreSQL 集成）通过，模块 Ruff 与严格 mypy 通过。前端 `pnpm check` 已有一轮完整通过（契约、类型、ESLint、14 项单元测试及构建）；收尾时复查整仓类型，10 的 `ToolsPage.tsx` 出现尚在并行修改的 `referenced_agents` 字段类型错误，09 页面无类型错误。Chrome 的 3 项浏览器测试已通过。页面截图已人工核验，无浏览器异常；未安装 Playwright 独立 Chromium 时使用本机 Chrome。完整工程检查随同批 06/07/08/10 改动一起运行，其结果另行记录，不能据本模块通过就声称整批已验收。

开发数据库已执行 `alembic upgrade 0009_prompts`；实际提示词三表的字段、中文注释、普通索引与冻结模型逐项一致，数据库存储限制检查通过。全应用生命周期装配成功，`/health/ready` 返回 200。迁移仅推进到本模块修订，工具模块的后续迁移由其执行单元处理。

整仓非集成测试本次为 **82 passed**。最后一次后端全局检查仍遇到并行模块中的问题：`integrations/tools/http.py` 的网络适配器 timeout 签名/headers 类型，以及 `modules/usage/exports.py` 的格式/可空统计类型。09 的模块检查和本单元集成验证均通过，未改写 08/10 的在途实现；后续整批合并后需重新运行 `make check`。
