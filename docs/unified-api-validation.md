# 22 统一 AI 接口验证记录

完成日期：2026-10-03。依据 [方案 22](../../代码编写执行方案/22-统一AI接口与调用交付.md)；接入方法见 [统一 API 指南](unified-api.md)，运行摘录见 [脱敏证据](unified-api-evidence.json)。

## 交付范围

独立后端客户端只依赖 httpx，通过真实 TCP HTTP 入口完成服务 Token 交换、主体委托、运行创建/查询/取消、SSE、会话与产物下载。两个 Agent 从 21 的文本要点整理、档案摘录解读制品配置而来；后者使用 20 的 `archive.find-notes` 远程 MCP 测试服务。模型路由显式选择已验证的 streaming 能力，以验证中间文本和最终结果的分离。

实际使用 PostgreSQL、Redis、FastAPI/uvicorn HTTP 服务和本机 TCP MCP。模型响应使用受控 fixture；对象存储使用内存 fixture，产物元数据、鉴权与下载 HTTP 路由为真实服务。组合用例通过 `execute_message` 驱动正式运行器，没有声称完整 API → Celery → 真实供应商部署已经联通。隔离数据库 schema、Redis 前缀和临时 HTTP/MCP 服务在用例结束后清理。

已有 11/17/18/20/21 服务继续是唯一实现。22 修补了渠道服务可选动作遗漏的 `conversation:write/content:derive`，补齐运行 200/202、SSE 游标/410 及会话文件的 OpenAPI 描述；没有新增业务路由、表或迁移。业务后端 OpenAPI 从正式路由裁剪，包含 24 个路径和 45 个引用组件，管理路径不纳入该制品。

## 验收映射

| 验收项 | 已取得证据 |
| --- | --- |
| INT-A01 | 同一客户端对两套配置完成 sync 超时转查询、已终结同步 200、async 取消、stream 断开后游标恢复；原 run_id 不变，最终结果一次交付 |
| 同步窗口 200 | 补充通用对象组装 Agent，通过配置的 compute/object 步骤在 15 秒窗口内完成并返回 200；执行器使用正式受理和 Worker 入口 |
| INT-A02 | 每次用新 nonce 签署伪造渠道、环境、主体或外部数据域的请求，均被拒绝；两套自定义输入输出 schema 完整交付，未知输入被 422 拒绝 |
| INT-A08 | API Key 直接作 Bearer 返回 401；删除 Redis Token 后 Worker 仍可按当前权限完成原任务，客户端重新认证查询；Key 轮换重发返回原 run，吊销后拒绝访问 |
| INT-A09 | 当前源权限撤销后，查询、幂等命中、旧事件地址及旧产物下载均被拒绝；SSE 已连接时撤权也立即控制关闭；缺失源身份复核的后台拒绝另由已有委托回归覆盖 |
| RUN 同步/异步/取消 | 202 返回原引用，200 返回完整结果；排队取消阻止执行，成功后取消保留成功；在途取消与迟到用量由运行回归覆盖 |
| RUN-A01/A04/A05/A09 | 同幂等键重发及 Key 轮换不重复受理；游标递增并去重，结果事件唯一；事件过期回查原快照；已有并发、取消竞争及重复投递回归通过 |
| 会话/产物 | 创建会话、消息幂等只产生一个 run、执行后导出并下载；当前权限撤销后旧产物标识不能绕过鉴权 |
| 错误与契约 | 独立签名实现匹配平台公开向量；失联重发保留原正文/幂等键并重签；HTTP 错误保留 request_id；OpenAPI 与真实路由及生成前端类型一致 |
| 领域解耦 | 客户端不导入平台执行代码或任何业务工程，无固定渠道、业务地址、匹配/风险/指标分支；纯文本和 MCP 引用结果分别匹配自己的 schema |

SSE 中 `text_delta.payload.validated=false`；只有 `result` payload 为校验后的业务结果。客户端半帧不推进游标，401 有界换 Token 后重签，403 停止，410/序号缺口回查当前授权下的原运行。最终快照与事件结果由业务后端按 run_id 幂等落地。

## 检查结果

- 服务端 `make check` 通过：ruff 格式及检查、268 个源文件的严格 mypy、**164 项单元测试**、全部契约一致性与模型档案/存储静态审查。日志 `.logs/22-check-final.log`。
- 新增客户端单元验证 **7 项**，已计入上述 164 项；可运行命令行 `python -m examples.backend --help` 通过。
- 新增真实 HTTP 组合验证 **4 个独立用例通过**：首批三项记录在 `.logs/22-delivery.log`，新增同步窗口和流中撤权专项见 `.logs/22-window-final.log`。范围伪造使用新 nonce 的补充复核通过；重复运行不重复计数。
- 相关既有集成回归 **103 项通过**，覆盖运行、委托、渠道、会话、预算边界、输出校验、恢复、取消及跨渠道拒绝；日志 `.logs/22-regression.log`。与新增组合用例合计 **107 项独立集成用例**。
- 前端 `pnpm check` 通过：OpenAPI 类型一致性、TypeScript、ESLint、**19 项 Vitest** 和生产构建。日志 `creativity-web/.logs/22-check-final.log`。本单元未修改页面，不增加浏览器界面用例。

现有 ZIP 重复条目测试警告、uvicorn/websockets 弃用提示及前端大包提示不影响上述结果。

## 复现与交接

先按项目 README 启动基础设施并安装锁定依赖。在服务端目录运行：

```sh
make check
CREATIVITY_API_EVIDENCE_DIR=.logs/22 uv run pytest \
  tests/integration/runtime/test_backend_delivery.py -q
uv run pytest tests/integration/runs \
  tests/integration/runtime/test_boundaries.py \
  tests/integration/runtime/test_execution.py \
  tests/integration/integrations \
  tests/integration/channels/test_channels.py \
  tests/integration/conversations -q
```

证据文件保留测试 run_id、发布快照、结果、用量、HTTP 请求标识和工具调用的 source_request_id，不保留 API Key、Token、委托或源服务凭据。测试数据已清理，记录不是当前生产环境的可查询运行。

23 使用同一 `examples/backend/client.py` 更换工具、技能和 Agent 配置，继续验证固定平台构建物复用。至少两种真实模型兼容、正式业务数据、真实对象存储组合链路和部署状态由 26 汇总；本记录不能替代这些证据，也不绕过 24 的正式发布门禁。
