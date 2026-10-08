# 接口与内部契约

Python 类型和正式路由为唯一来源。运行 `make contracts` 生成全部契约，`make check` 检查过期输出；前端继续读取 `openapi.json`，在前端工程执行 `pnpm api:generate` 更新类型。

| 内容 | 入口 | 用途 |
| --- | --- | --- |
| 完整 HTTP API | [openapi.json](openapi.json) | 管理接口、业务接口与前端类型 |
| 业务 HTTP API | [backend/openapi-v1.json](backend/openapi-v1.json) | 后端调用、Token、运行/SSE、会话与产物 |
| 身份委托 | [integrations](integrations/) | v2 载荷、请求绑定、错误表、签名向量及接入接口 |
| MCP | [mcp](mcp/) | 发现、导入、身份、主体复核与结果元数据 |
| 内部交接 | [internal](internal/) | 每个模块一份 JSON，按类型名查找独立 schema |
| 公共契约样例 | [examples.json](examples.json) | 按类型名查找成功、缺失及拒绝样例 |
| 模型用量样例 | [models/usage-examples.json](models/usage-examples.json) | 供应商原始用量、缓存子集及完整性 |

## 内部交接

`internal/<模块>.json` 是 `类型名 → 独立 JSON Schema` 的映射，**整个文件不是一个 JSON Schema**。取出某个类型后，以该对象为根解析其 `$defs` 和 `$ref`；不要跨类型合并定义。例如：

```python
schemas = json.loads(Path("contracts/internal/core.json").read_text())
sample = json.loads(Path("contracts/examples.json").read_text())["UsageEvent"]["success"]
Draft202012Validator(schemas["UsageEvent"]).validate(sample)
```

公共类型来自 `core/context`、`core/auth/types`、`core/contracts` 和 `core/primitives`，公共契约版本为 1.2.0。`core` 的成功样例可直接按同名 schema 校验；`missing` 和 `failure` 使用统一错误响应。跨字段关系、摘要和授权仍由 Pydantic 与服务层验证。内部身份、冻结定义和运行授权结构不作为客户端认证或提交入口。

模块文件包括 `core`、`channels`、`usage`、`agents`、`skills`、`tools`、`runs`、`memory`、`conversations`、`prompts`、`models`、`evaluations`。HTTP 请求以完整或业务 OpenAPI 为准；运行模块不再重复保存一份 OpenAPI。旧 Agent 定义的兼容回归样本位于 `tests/fixtures/agents/legacy-v1/`。

单模块生成及检查仍使用原命令：

```sh
uv run python -m creativity_service.modules.runs.export
uv run python -m creativity_service.modules.runs.export --check
```

## 协议约定

委托使用 `business-delegation-v2`，绑定渠道、环境、主体及实际请求；旧数据域声明不再接受。Money 以十进制字符串传输，时间使用带时区 ISO 8601；缺失用量保持空值，不能按零累计。事件游标失效后查询运行快照。消费者遇到未知错误码使用服务端 message，未知业务状态不能映射为成功。

公共契约 1.x 允许增加不影响执行语义的可选展示字段；新增必填字段、改变状态语义、金额精度、摘要算法或事件顺序需提升主版本。

使用说明见 [接入指南](../docs/integration.md)、[配置指南](../docs/configuration.md)、[运行指南](../docs/runtime.md) 和 [运维指南](../docs/operations.md)。
