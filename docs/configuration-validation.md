# 21 配置交付验证记录

日期：2026-10-03。交付范围和操作方式见 [配置交接](configuration-delivery.md)，对应 SKL 的导入、依赖、权限、加载与版本，以及 AGT 的绑定、候选、调试和发布。

使用工程锁定的 Python 3.12、Node.js 22、pnpm 10.32.1。本次验证包含真实 PostgreSQL、Redis、MinIO 技能回归及本机 TCP MCP。组合配置测试的技能对象存储使用既有内存替身，模型供应商响应使用受控替身；提示词通过统一调试运行与用量证据后冻结。未连接生产业务数据或真实模型供应商，未发布生产 Agent。

## 验收证据

| 范围 | 证据与结论 |
| --- | --- |
| 新用途只改配置 | 直接读取两套交付 JSON 和 ZIP，显式替换目标版本后创建 Agent；纯文本单步与 MCP 三步共用执行器，通用对象组装不增加节点注册项或任务类 |
| SKL-A01/A05，包与导出 | 服务器预览核验文件；源目录与 ZIP 字节一致。携带本地工具绑定的归档与入口元数据拒绝，导出剥离本地映射，外部 schema 引用拒绝；既有路径穿越、符号链接、敏感正文及脚本回归继续通过 |
| SKL-F06/F10，显式绑定 | 缺绑定保存为草稿但不能冻结；即使已有本渠道工具也不自动匹配。错误 schema、实际跨渠道版本 ID 均拒绝。MCP 可移植名称映射到独立的本地资源，具体工具仍须满足版本、来源及契约 |
| SKL-A03/AGT-A01，授权 | 移除 Agent 白名单导致校验失败；Skill 文本不能新增权限。新增用例在对象上传期间经 IAM 服务撤销 run:create，写入事务重新校验并拒绝，未生成技能资源，工具选项显示不可用 |
| SKL-A02/A06，加载 | 始终加载和显式触发的按需技能进入实际模型上下文；所选参考文件、SHA-256、触发原因在运行轨迹可核对。不可解析资源和脚本不进入上下文；按需未触发、省略、预算和冲突继续由原加载器回归覆盖 |
| SKL-A04/AGT-F13，版本与候选 | 参考资料另建并冻结新版本，Agent 改绑后候选摘要变化；旧候选内容与版本引用保持不变。旧定义缺少 tool_bindings 时只恢复原固定引用，包摘要不变，复制草稿能保留这些引用 |
| 调试、评测、环境发布 | 文本 Agent 通过管理调试、冻结评测候选执行与测试环境发布后调用。MCP Agent 在测试环境经真实签名主体委托受理，由共享 Worker 执行入口查询后输出；无主体的管理身份不能绕过源权限 |
| 来源与结果 | MCP 本次 source_version、observed_at 和 evidence_refs 出现在模型上下文；输出引用原文与源端受控 notes 核对，证据引用来自工具结果。配置捕获时间没有充当业务数据时间 |
| 生产发布门禁 | 沿用 AGT 既有证据校验与回归，prod 缺有效报告、内容变化或当前依赖不可用时拒绝；本次测试候选运行不被记作正式业务评测通过 |
| 管理页面 | 文件清单、导入契约与显式绑定可用；错误保留输入；Agent 可选资料、按需触发及加载优先级；窄屏反馈和桌面配置截图已检查，结果沿用通用运行查看器 |

## 检查与日志

服务端 `make check` 通过：Ruff 格式与规则、268 个源文件 mypy strict、**157 项非集成测试**、OpenAPI/全部模块契约、数据模型归档和存储源码审查。最终日志：`creativity-service/.logs/21-check-complete.log`。

相关集成矩阵：

```sh
uv run pytest tests/integration/skills tests/integration/agents \
  tests/integration/runtime tests/integration/prompts -q
```

**65 项通过**，日志为 `.logs/21-integration.log`。补充上传期间权限撤销用例后，配置专项四项全部通过，日志为 `.logs/21-config-evidence.log`；相关唯一集成用例合计 **66 项**。旧绑定恢复和包兼容专项另有 **4 项通过**（3 项静态、1 项集成），日志 `.logs/21-compatibility.log`。MCP 来源与输出证据最终核对见 `.logs/21-mcp-evidence-final.log`。各集成测试使用独立数据库 schema 与认证范围，结束后清理。

前端 `pnpm check` 通过 OpenAPI 类型、TypeScript、ESLint、**19 项 Vitest** 和生产构建；`PLAYWRIGHT_CHANNEL=chrome pnpm exec playwright test tests/skills.spec.ts tests/agents.spec.ts` **12 项通过**。日志为 `creativity-web/.logs/21-check-final.log` 与 `.logs/21-browser-complete.log`。人工检查的截图：`.logs/21/skill-import-mobile.png`（390 像素宽）和 `.logs/21/agent-skill-loading.png`。

初轮测试暴露的是旧样例断言将所有 ZIP 限定为同一种资料布局，以及新增浏览器用例拦截到页面导航、按钮加载图标影响精确名称定位。测试已按各代制品与真实页面行为修正。旧 Agent 金样文件保持原样，新增默认字段从比较对象中明确排除后继续核对旧字段；两份新包有独立源码、归档与清单一致性验证。既有重复 ZIP 测试警告与前端大包提示未影响检查结果。

## 调试记录与重现

[configuration-debug.json](configuration-debug.json) 保存两套样例的实际 run_id、渠道、运行状态、输出、候选及输出 schema 摘要、依赖清单、包摘要和实际加载文件。所有记录标明模型是受控替身、测试数据已清理，不能把已清理的 run_id 当作当前环境可打开的运行。

重新生成同类记录：

```sh
CREATIVITY_CONFIG_EVIDENCE_DIR=.logs/21 uv run pytest \
  tests/integration/runtime/test_configuration.py -q
```

本次 Worker 通过 `execute_message` 驱动真实运行器，没有声称验证独立 Celery 部署。真实供应商、正式业务数据与完整发布证据继续由 24/26 收口；22 使用样例交付统一 API 客户端，23 验证固定平台构建物复用。本单元没有 DDL 变更，无需迁移现有开发库。
