# 业务无关接入验证样例

对应 [方案 23](../../../代码编写执行方案/23-业务无关接入验证.md)。[scenarios.json](scenarios.json) 定义三组隔离配置；[prepare.py](prepare.py) 将 MCP 契约制成可导入 Skill ZIP 和 Agent JSON。领域数据与矩阵计算仅存在于 [受控 MCP 服务](../mcp/server.py) 和本目录配置，不装入平台模块。

| 配置 | 源系统示例 | 远端工具 | 结果 data |
| --- | --- | --- | --- |
| 文档查询验证 | document_space | archive.find-notes | notes 数组，含 heading/text |
| 矩阵计算验证 | calculation_workspace | matrix.total | total 数值与 unit 字符串；输入为内联的二维数值数组 |
| 第三渠道验证 | research_collection | archive.find-notes | 同名工具返回该渠道独立的 notes |

Agent 流程定义沿用现有内联 schema 契约，矩阵配置把 MCP 原样例的本地引用展开为等价内联结构；原 MCP 对 $defs/$ref 的支持由 20 的回归继续验证。

第三渠道在前两组完成运行、完成一次构建比对后才创建。三组均使用 `shared-user-001`、外部编号 `shared-001`、Agent 编码 `shared_agent`、技能编码 `shared_skill` 和相同幂等键；额外用相同本地工具编码 `shared_lookup` 完成独立调用。

在服务工程目录按需生成配置：

```sh
uv run python -m examples.onboarding.prepare
```

默认写入 `.local/examples/onboarding/`，可用 `--output` 指定其他目录；生成产物不提交 Git。`documents/`、`matrix/`、`third/` 各含可导入的 `skill.zip` 与 `agent.json`，根目录 `manifest.json` 记录文件摘要。生成过程只读取本地契约，不启动 MCP 或连接数据库。

Agent 的输入输出、步骤和流转内容可填入管理向导，`bind_*` 必须替换为目标渠道选择的资源版本。运行限时为 180 秒，其余运行限制采用通用向导默认值。

## 固定版本复现

需要项目 README 所列 MySQL/Redis、Python 3.12、uv 0.10.12、Node.js 22 和 pnpm 10.32.1，以及 Playwright 浏览器。复制开发 `.env` 或通过环境提供连接参数。验证会创建独立 MySQL 数据库、Redis 前缀和本机临时 HTTP/MCP 端口，结束后清理；不修改开发库数据和结构。

在 `creativity-service` 中运行：

```sh
# 已安装 Google Chrome 时可以使用 chrome；否则先安装锁定 Playwright 的 Chromium。
PLAYWRIGHT_CHANNEL=chrome uv run python scripts/verify_business_independence.py --checks
```

脚本默认固定服务端 `a28b59f` 和前端 `305a077`，在新的 `.logs/23-replay-时间/` 下创建独立 Git worktree，仅带入本单元的测试和配置材料，构建后再开始接入。`--service-ref`、`--web-ref` 可指定其他待测版本；变更版本必须重新取得整套证据。`--output` 可指定一个尚不存在的目录。脚本保留检出、构建及证据，不自动删除本地文件。

已有固定检出及构建时，也可直接运行：

```sh
uv build --wheel
# 先在相邻 creativity-web 中执行 pnpm build，再运行验收；验收期间不重新构建。
PLAYWRIGHT_CHANNEL=chrome CREATIVITY_INDEPENDENCE_EVIDENCE_DIR=.logs/23 \
  uv run pytest tests/integration/runtime/test_business_independence.py -q
```

页面驱动为前端的 `tests/support/business-independence.mjs`，由上述 pytest 自动启动。页面使用编译后的前端，并把管理请求原样转发至临时真实 HTTP 服务。渠道、接入服务、Key、委托、MCP 发现/发布、主体复核、Skill 导入/绑定/冻结、Agent 配置/校验/调试/测试环境发布均经过真实页面。模型和提示词由既有配置服务准备为前置夹具；业务调用使用 22 的原始 `examples/backend/client.py`。

固定构建组合用例仅在设置 `CREATIVITY_INDEPENDENCE_EVIDENCE_DIR` 后运行；普通 `make integration` 会明确跳过它，避免依赖未准备的前端构建、wheel 和浏览器。使用新证据目录保留每次独立执行的记录。每个渠道只登录一次，后续页面阶段复用真实会话，不调整登录限流。

如需手工配置页面，可用相同清单独立启动 MCP，身份文件格式沿用 `examples/mcp/identity.example.json`：

```sh
uv run python -m examples.onboarding.server --scenario documents --port 18081 --identity-file /secure/documents.json
uv run python -m examples.onboarding.server --scenario matrix --port 18082 --identity-file /secure/matrix.json
uv run python -m examples.onboarding.server --scenario third --port 18083 --identity-file /secure/third.json
```

## 证据与范围

输出目录包含三组可导入配置、页面截图、HTTP 请求标识、MCP 协议方法、源调用关联、结果、SSE 游标、用量和故障记录。`baseline.json`、`after-two.json`、`after-three.json`、`final.json` 对比平台源码、锁文件、构建、路由、OpenAPI、迁移、实际数据库结构和后端客户端；任何差异均使验证失败。Wheel 中的源码也须与待测源码逐文件一致。

API/MCP 使用真实 TCP，存储与鉴权使用 MySQL/Redis。模型输出及 Token 数量为受控替身，对象存储为内存夹具，Worker 通过正式 `execute_message` 入口在测试进程执行。这些记录只证明通用接入、隔离和版本边界；真实供应商组合、独立 Celery 部署、生产对象存储及业务效果由后续验收覆盖。渠道均为测试环境，不绕过生产评测门禁。

一次性 API Key、Token 和委托秘密只经进程管道传递；不录制浏览器 trace，不写入交付证据。持久证据中的运行、渠道和来源编号属于已清理的测试数据，不是生产运行引用。
