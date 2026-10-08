# 受控 MCP 接入样例

`server.py` 是测试服务，按 profile 提供两套不同契约。它们都通过真实 TCP Streamable HTTP 使用连接服务认证，并提供固定的 `access.review-current` 主体复核工具；不加入平台业务注册表，不连接生产数据。结果的 `coverage.verification` 固定为 `controlled_fixture`。

| profile | 远端工具名 | 输入 | 输出 |
| --- | --- | --- | --- |
| archive | archive.find-notes | document 字符串，授权样例 note-a | notes 数组 |
| matrix | matrix.total | 二维数值数组，使用本地 $defs/$ref | total 和 unit |

在服务工程目录复制 `identity.example.json`，替换为测试渠道实际 Scope，并显式配置主体当前权限。示例通配资源仅用于隔离测试；正式源服务按当前资源授权返回子集。

```sh
uv run python examples/mcp/server.py --profile archive --port 18081 --identity-file /absolute/path/identity.json
uv run python examples/mcp/server.py --profile matrix --port 18082 --identity-file /absolute/path/identity.json
```

每条连接单独登记，测试服务令牌为 `fixture-service-only`；此常量仅用于本机受控测试。平台出站策略需显式允许两端口。按 [配置指南](../../docs/integration.md#mcp-business) 完成发现、启用、身份绑定、导入、发布、Agent 绑定及 API 调用。

自动验证使用同一 `serve()` 启动独立随机端口，将 scope 与权限绑定到临时 PostgreSQL/Redis 测试环境。可注入 schema 变化、当前授权撤销、复核过期、范围错误、结果错误、空/缺失/部分结果及真实副作用。真实网络不等于真实业务验收；模型响应为既有替身，不冒充真实供应商能力。

## 租号服务基础连接

[gamerental.connection.json](gamerental.connection.json) 可作为 `POST /admin/v1/mcp-connections` 的创建正文。默认地址为 `http://127.0.0.1:32701/mcp`，连接方式为 `streamable_http`；源端默认返回完整 JSON，也可切换为同端点 SSE 响应，平台现有客户端均支持。

租号服务启用 `MCP_ENABLED=true`，设置环境与完整 `MCP_RESOURCE_URI`。在租号后台「配置 → AI应用接入配置」为实际渠道创建 appId/appSecret；每个渠道、环境在该服务独立维护一套。Creativity 连接选择 streamable_http，在「配置鉴权」中选择服务间鉴权，填写 `/mcp/token` 地址、appId/appSecret，平台直接保存密钥，无需解密主密钥，并自动换取、续期访问 Token。然后手动测试、发现、启用。

API/Worker 的 `CREATIVITY_MCP_DESTINATIONS` 须允许同一渠道、环境的 `purpose=mcp` `/mcp` 路径，以及 `purpose=oauth` `/mcp/token` 路径。升级前执行 Creativity 0044 迁移与租号 `sql/upgrade_mcp_application.sql`、`sql/upgrade_mcp_application_menu.sql`；源端配置见租号 `skill-project/mcp-config.md`。此模式不需要用户 OAuth 授权码流程。

当前源端只提供基础 MCP 能力，发现结果预期为零个工具，尚不能绑定业务 Agent。业务工具、主体复核及权限在后续业务实现时提供，再按 [配置指南](../../docs/integration.md#mcp-business) 导入发布。本样例不预置渠道、令牌或数据库记录。
