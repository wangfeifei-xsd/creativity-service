# 17 验证记录

日期：2026-10-02。范围见 [方案 17](../../代码编写执行方案/17-运行编排与流式交付.md)，实现见 [运行编排交接](runtime.md)。

自动集成使用真实 PostgreSQL、Redis、IAM、完整迁移和生产运行执行器；模型响应为可控夹具，工具使用平台只读内置适配器，浏览器使用管理 HTTP 夹具。每项测试在隔离 schema 中执行，不向生产配置授予能力。

## 自动检查

- 服务端 `make check`：ruff、mypy、150 项非集成测试、OpenAPI/各模块契约、数据模型归档和存储审查通过。
- 前端 `pnpm check`：生成契约校验、TypeScript、ESLint、19 项单元测试与生产构建通过。公共入口仍有既有的 500 kB 体积提示。
- Chrome 25 项通过，覆盖执行中心、结果与部分内容、费用完整性、无原文权限和窄屏布局，以及 Agent、模型、工具、提示词、技能、会话页面。异常响应在组件内展示错误；原文权限收紧后隐藏已收到的部分内容，仍持续查询执行状态。
- 运行集成与跨模块回归的最终数量见本文件末尾记录。

## 覆盖与边界

| 需求/场景 | 自动验证内容 |
| --- | --- |
| RUN-A01/A03/A09 | 两 Worker 重复消息只产生一次模型执行；独立尝试和事件序号；旧租约禁止提交 |
| RUN-A04、IAM-A12 | SSE 顺序、游标补发、24 小时过期后快照可读；真实 Redis Token 删除和 Redis 连接故障控制关闭；任务继续执行 |
| RUN-A05 | 执行中取消阻止成功结果，已捕获用量继续保留 |
| RUN-A06/A07 | 已保存响应恢复不重发；未保存的已发送模型响应保持 UNKNOWN；LangGraph 崩溃恢复；删除恢复点后禁止迟到写入 |
| RUN-A08 | debug rerun 保持用途、产生新 run、关联 parent_run_id；模块测试 rerun 不覆盖旧认证结果 |
| RUN-A11 | 错误渠道消息无法启动实际模型，11 的多渠道补偿与用量隔离用例同步回归 |
| AGT-A02/A03/A06 | 16 的旧发布快照用例回归；受约束工具循环到次数上限停止；会话目录只列出当前环境已发布且启用会话的 Agent；会话互斥、历史上下文与无会话执行 |
| MOD/USG | 失败重试独立计数；总轮次限制；结构修复次数；流正文后失败不拼接回退；未知用量不显示为零；步骤超时保留已捕获用量 |
| PRM/TOL/SKL | 没有正式 Agent 时，提示词、只读工具、技能均生成 debug run；工具结果证据持久化；技能保留加载文件；模型验证生成统一用量 |
| 计算/部分结果 | 注册步骤有限重试；失败策略产生的 PARTIAL 必须通过冻结最终 schema |
| 输入/产物 | 敏感内容权限单独检查；实际输入来源可追溯；产物按原范围读取和独立下载授权 |
| 记忆降级 | 读取服务返回的降级提示写入实际加载记录，并进入最终业务 warnings；不依赖模型复述提示 |

## 未完成的外部验收

[供应商兼容记录](runtime-providers.json) 的 Chat Completions、Anthropic Messages 两个组合均为 UNVERIFIED，未配置供应商及模型凭据，没有真实调用编号、价格或用量结论。至少两种真实组合通过 MOD-A01—A06 后才能宣布方案 17 全部验收通过。

已只读核对当前开发数据库：供应商、模型连接、模型、路由和模型验证记录均为 0；检查没有读取或输出任何凭据内容。

业务服务身份的 Worker 还需接入源系统 `CurrentSubjectReader`；当前缺少该实现时明确拒绝执行。管理身份的自动集成不冒充源业务当前授权联调。后续 19/20 整改通用接入边界与主体复核，22–27 完成 API、业务无关验证、通用评测及上线。原场景适配任务已撤销，真实业务效果另由接入方验收；本文历史验证不代表新边界已实现。

## 最终回归

| 检查 | 结果 |
| --- | --- |
| 服务端 `make check` | 通过；150 项非集成测试，262 个源文件类型检查，全部契约、数据模型与存储审查通过 |
| `pytest tests/integration/runtime tests/integration/runs tests/integration/agents tests/integration/tools tests/integration/models tests/integration/prompts tests/integration/skills tests/integration/memory tests/integration/conversations -q` | 136 项通过；包含当时已加入的 22 项运行专项 |
| 最终运行专项 | 28 个不同用例已通过；最后整组回归中 27 项通过，记忆降级用例补齐新主体恢复屏障初始化后，使用 `pytest tests/integration/runtime/test_entries.py -k memory_omission -q` 单项复验通过 |
| 前端 `pnpm check` | 通过；19 项单元测试、生成类型、类型检查、lint 和构建通过 |
| `PLAYWRIGHT_CHANNEL=chrome pnpm test:e2e tests/runs.spec.ts tests/agents.spec.ts tests/models.spec.ts tests/tools.spec.ts tests/prompts.spec.ts tests/skills.spec.ts tests/conversations.spec.ts` | 25 项通过 |

专项与跨模块数量存在交集，不相加冒充独立用例总数。最终补充修改的 ruff、格式、mypy、`git diff --check` 已通过。自动测试没有访问真实模型供应商；供应商验收状态仍以兼容记录为准。
