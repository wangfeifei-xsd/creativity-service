# 23 业务无关接入验证记录

完成日期：2026-10-03。依据 [方案 23](../../代码编写执行方案/23-业务无关接入验证.md)、INT-A07 与 GLO-A09。三渠道完整组合验收通过，四阶段平台快照完全相同。接入职责见 [交接清单](business-independence.md)，配置与复现命令见 [样例说明](../examples/onboarding/README.md)。

## 固定版本与范围

服务端固定在 `a28b59f`，前端固定在 `305a077`；使用独立 Git worktree，避免其他单元正在进行的实现进入本轮验证。迁移头为 `0021_mcp_subject_review`，实际数据库为 89 张表、1305 个字段，OpenAPI 展开得到 203 个路径。本单元只增加测试夹具、配置、验证脚本和证据；没有新增平台业务适配器、模块、路由、表或迁移。

前端从既有生产构建启动，Playwright 操作真实管理页面，管理请求原样转发到临时 FastAPI/uvicorn 服务。MCP 的初始化、工具目录、主体复核和调用经过真实 TCP，配置、运行及权限使用真实 PostgreSQL 和 Redis。每渠道登录一次，后续配置阶段复用真实会话，继续遵守 IAM 登录限流。

模型响应、模型能力验证结果和 Token 数量来自受控替身；对象存储使用内存夹具。Worker 由测试进程调用正式 `execute_message`，不代表独立 Celery 部署已完成验收。数据为人工测试资料，渠道仅发布到测试环境。两种真实供应商组合、生产对象存储、完整部署和实际业务效果仍由后续单元验收。

## 配置与调用

| 渠道 | 外部数据域类型 | MCP 工具 | 输入与结果 |
| --- | --- | --- | --- |
| 文档查询验证 | `document_space` | `archive.find-notes` | 文档编号 → `notes` 数组 |
| 矩阵计算验证 | `calculation_workspace` | `matrix.total` | 二维数值数组 → `total` 数值及 `unit` 字符串 |
| 第三渠道验证 | `research_collection` | `archive.find-notes` | 同名工具、同输入 → 第三渠道独立资料 |

前两组完成调用和第一次构建比对后，才新增第三渠道。三组都使用外部数据域编号 `shared-001`、主体 `shared-user-001`、Agent 编码 `shared_agent`、Skill 编码 `shared_skill` 和同一幂等键。另通过现有通用工具管理服务配置三个同编码 `shared_lookup` 的工具及验证 Agent，检查渠道内独立解析。

渠道、数据域、调用服务、Key、委托密钥、MCP 连接与发现/发布、主体复核、Skill 导入/绑定/冻结、Agent 配置/校验/调试/测试发布均经过管理页面。模型和提示词由既有配置服务准备为前置夹具。所有后端调用使用 22 原有 `examples/backend/client.py`，其目录摘要也参加每次比对。

管理工作区没有业务主体委托。管理调试返回 `TOOL_FORBIDDEN`，并验证远端业务工具没有被执行；随后用正式的服务 Key、签名委托和当前主体复核调用测试环境发布的 Agent。该拒绝是可信身份边界的预期行为，不通过伪造管理员业务身份或取消工具主体要求让调试成功。

矩阵配置使用现有 Agent 流程支持的内联 schema，将测试 MCP 原有的 `$defs/$ref` 展开为等价结构。原 MCP 本地引用支持仍由 20 的相关回归验证，本轮不声称 Agent 流程新增了引用型 schema 能力。

## 验收对应关系

| 验收项 | 验证方式 |
| --- | --- |
| INT-A07 / GLO-A09 | 前两组及后加第三渠道只写入配置；四阶段对比源码、构建、锁文件、路由、OpenAPI、迁移、数据库结构和原客户端 |
| 结果与来源 | 三组结果分别匹配各自 schema；来源包含当前渠道 Scope；工具调用关联实际 `source_request_id` |
| SSE 与幂等 | 每组 14 条事件，序号递增、结果事件唯一，游标恢复只返回后续事件；最终事件与查询结果相同，同幂等键重发返回原运行 |
| 用量与错误 | 每组输入 10、输出 20 Token，`complete=true`；未配置价格时 `unpriced_count=1`、`amounts={}`；错误输入返回 422 和请求标识 |
| 渠道隔离 | 同用户、同工具/Agent/Skill 编码及同幂等键在三渠道独立；跨渠道读取运行和 SSE 均返回 404 |
| Skill 内容变更 | 冻结版本拒绝原地编辑；新资料产生不同包摘要和候选摘要；旧修订校验拒绝，已发布 Agent 继续使用原 Skill |
| 源工具 schema 变化 | 排队后改变源契约，Worker 返回 `MCP_TOOL_CHANGED`，未向源业务工具发送调用 |
| 当前权限撤销 | 排队后撤权，Worker 返回 `SUBJECT_REVIEW_DENIED` 且未派发业务工具；旧运行查询、幂等命中和 SSE 均返回 403；第三渠道继续成功 |
| 无旧业务模块 | 检查不存在旧匹配/风险/分析表和专用 API 前缀，管理导航没有旧场景入口，运行注册表没有场景执行器 |

完整调用记录见 [脱敏证据](business-independence-evidence.json)。运行、渠道及来源编号均为已清理的测试记录，不能当作生产运行查询。API Key、Token、委托秘密及源服务凭据不写入交付证据。

真实页面发布截图：[文档渠道](business-independence/documents-published.png)、[矩阵渠道](business-independence/matrix-published.png)、[第三渠道](business-independence/third-published.png)。

## 构建对比

在开始接入、完成前两组、完成第三渠道和结束边界测试四个阶段取得完整结构快照；每次要求与基线严格相等。服务端 wheel 内源码还须逐文件匹配待测源码。完整文件清单、数据库列/索引/约束、路由清单及摘要见 [构建证据](business-independence-builds.json)。

| 比对对象 | SHA-256 摘要前 16 位 | 四阶段结果 |
| --- | --- | --- |
| 服务端源码、配置及锁文件清单 | `4ea62df78c9758d29` | 完全相同 |
| 前端源码、配置及锁文件清单 | `b285cc09b8466d72` | 完全相同 |
| 服务端 wheel 文件 | `354f1eabf8665a57` | 完全相同且源码逐文件一致 |
| 前端构建文件清单 | `85e1c7da190eb5a4` | 完全相同 |
| 迁移文件清单 | `d6b60d2df84d8cb1` | 完全相同 |
| 实际数据库列、索引和约束 | `8975be9b39946e04` | 完全相同 |
| 路由清单 | `f17d7ef013ac7c6f` | 完全相同 |
| 22 后端客户端目录 | `9acae165d7e5abf6` | 完全相同 |

这里除 wheel 一项外均为清单摘要；完整文件哈希与 OpenAPI 摘要保留在 JSON 中，不能把清单摘要当作单个制品的下载校验值。

## 检查记录

- 服务端最终 `make check` 通过：格式、ruff、268 个源文件的 mypy、164 项单元测试、契约一致性、模型档案和存储静态审查，见 [完整日志](business-independence/service-check.txt)。
- 前端 `pnpm check` 通过：契约、TypeScript、ESLint、19 项 Vitest 和生产构建，见 [完整日志](business-independence/web-check.txt)；会话复用驱动补充通过 ESLint。
- 相关集成回归 24 项通过，覆盖 `test_business_access.py`、`test_configuration.py` 和 `test_backend_delivery.py`，使用本次固定基线，见 [回归日志](business-independence/regression.txt)。
- 三渠道完整组合用例 1 项通过，耗时 470.71 秒，包含上述全部页面/API/MCP/版本/权限和构建比较断言，见 [验收日志](business-independence/acceptance.txt) 与 [Worker 回执](business-independence/worker.jsonl)。与回归合计 25 个独立集成用例，历史调试重跑不重复计数。
- 三套分发配置通过正式 Agent 契约解析；MCP 服务与独立复现脚本的命令行入口通过帮助页检查。组合验收在手工准备的固定 worktree 中完整执行，独立复现脚本的整段编排未另行重复运行。

现有 ZIP 重复条目测试警告、uvicorn/websockets 弃用提示及前端大包提示未导致检查失败。

## 复现

先安装项目锁定工具及依赖、启动 PostgreSQL/Redis，并提供本地连接配置。在服务端目录运行：

```sh
PLAYWRIGHT_CHANNEL=chrome uv run python scripts/verify_business_independence.py --checks
```

脚本在新目录固定两个 Git 提交、仅复制测试/配置材料、构建并检查，再执行完整组合用例。保留独立检出和证据；测试 schema、Redis 前缀及临时服务由夹具清理。更换平台提交须重新验证，不能直接沿用本记录。
