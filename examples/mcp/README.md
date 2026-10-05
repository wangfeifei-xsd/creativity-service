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
