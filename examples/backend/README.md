# 通用后端调用示例

此目录可复制到任意 Python 3.12 后端，唯一第三方依赖为 `httpx>=0.28,<1`。客户端不导入 Creativity 服务代码或任何业务仓库，Agent 编码、输入、平台地址与当前主体全部由调用方提供。业务前端只调用自己的后端；API Key、委托密钥和服务 Token 留在后端。

平台准备、权限清单、错误处理见 [接入指南](../../docs/integration.md#unified-api)，原理与向量见 [委托协议](../../docs/integration.md#integrations)。可交付契约为 [OpenAPI](../../contracts/backend/openapi-v1.json)，仓库内客户端另见 [Python 与 TypeScript SDK](../../sdks/README.md)。

## 配置

由服务端秘密管理设施注入以下变量；不要把实际值写进代码、命令行参数或提交到仓库：

| 变量 | 内容 |
| --- | --- |
| `CREATIVITY_API_URL` | 平台 HTTP(S) 源站地址，不含 `/api/v1`，部署环境使用 HTTPS |
| `CREATIVITY_API_KEY` | 本渠道接入服务的 API Key |
| `CREATIVITY_DELEGATION_KID` | 独立主体委托密钥编号 |
| `CREATIVITY_DELEGATION_SECRET` | 创建或轮换时返回的 Base64 签名密钥，非 API Key、非加密主密钥 |
| `CREATIVITY_DELEGATION_ISSUER` | 委托密钥配置的签发者 |
| `CREATIVITY_DELEGATION_AUDIENCE` | 委托密钥配置的受众 |

将 [principal.example.json](principal.example.json) 复制为仅后端可读的身份文件，替换为当前源系统验证的主体及权限。文件仅用于联调，不能接受浏览器上传。生产集成实现 `PrincipalService.current()`，从当前后端登录会话与权限服务读取身份；该回调每次发送和重连都会执行。MCP 当前主体复核仍由平台独立执行。

`resources.agent` 使用管理端返回的 Agent 资源标识；创建运行的 `agent_code` 使用已发布 Agent 编码，两者含义不同。其他依赖资源也应替换为本渠道已授权资源。`run:content` 需要 `data:read_sensitive`；只授予 `run:read` 不能读取结果和 SSE。

## 执行

在 `creativity-service` 目录执行，已有 uv 环境直接使用如下命令。外部后端保留 `examples/backend` 目录结构即可用自己的 Python 运行相同模块；若复制为顶层 `backend` 目录，则使用 `python -m backend`。

```sh
uv run python -m examples.backend --help
uv run python -m examples.backend --principal /secure/current-principal.json \
  create --agent "$AGENT_CODE" --input /secure/agent-input.json \
  --idempotency-key "$BUSINESS_REQUEST_KEY" --delivery sync --wait
uv run python -m examples.backend --principal /secure/current-principal.json query "$RUN_ID"
uv run python -m examples.backend --principal /secure/current-principal.json events "$RUN_ID" --after 0
uv run python -m examples.backend --principal /secure/current-principal.json cancel "$RUN_ID"
uv run python -m examples.backend --principal /secure/current-principal.json \
  download "$ARTIFACT_ID" --output /secure/result.json
```

`/secure/agent-input.json` 只包含 `input` 对象，结构由该 Agent 的 schema 决定。21 的两套输入可从 `examples/agents/cases/*.json` 取 `input` 字段；23 更换 Agent、输入与服务端身份配置即可复用客户端。

同一逻辑请求应由业务后端先持久化幂等键，重发必须保持原键和原输入。命令不会自动生成新键；创建响应或等待超时后持久化 `run_id`，后续使用 query/wait/events。CLI 的 `--wait` 最多等待 60 秒，超时不会取消原运行。

会话示例：先给接入服务和 Key 显式授权 `conversation:read/conversation:write`，Agent 配置开启会话，再执行：

```sh
uv run python -m examples.backend --principal /secure/current-principal.json \
  conversation --agent "$AGENT_CODE" --title "资料整理"
uv run python -m examples.backend --principal /secure/current-principal.json \
  message "$CONVERSATION_ID" --message-id "$CLIENT_MESSAGE_ID" \
  --content "请处理本次资料" --input /secure/agent-input.json
```

`client_message_id` 是同一会话内的持久消息幂等键。会话创建、导出等没有幂等契约的写操作在响应丢失时不会自动重发。

## 后端嵌入

```python
async with BusinessBackendClient(
    base_url, api_key, kid, signing_secret, issuer, audience, current_principals
) as client:
    receipt = await client.submit(agent_code, validated_input, saved_request_key, "async")
    # 业务存储应同时保留原输入、原幂等键和返回的 run_id。
    result = await client.wait(receipt["run_id"], wait_seconds=60)
```

`query/wait` 返回完整运行封装；检查 `state` 后再解释 `result.business_status` 与自定义 `result.data`。`FAILED/CANCELLED/TIMED_OUT` 都是终态，并不作为 HTTP 异常抛出。HTTP 错误抛出 `PlatformError`，包含状态、错误码及请求标识。

`subscribe` 产出 `Event(type, data, sequence)`。业务后端按 run_id 保存最后已处理序号并去重；`text_delta.data.payload` 是未校验片段，`result.data.payload` 才是通过输出 schema 的结果。事件缺口或 410 时，示例重新鉴权查询原运行，产出无序号的 `snapshot`，随后结束订阅；快照不是终态时继续 query/wait。无序号 `control` 帧不推进游标。业务前端使用自己后端代理的流，不在 URL 中放 Token。

客户端对安全请求的网络失败最多重发一次；401 最多重新认证一次，并重新读取主体、生成 nonce、签署实际请求。SSE 最多重连三次，半帧不推进游标，403 立即停止。权限撤销、幂等冲突、模型/工具失败不会自动新建运行。Key 或委托密钥轮换由后端秘密配置负责更新，新 API Key 需清空客户端 Token 缓存；原运行引用和原幂等键继续保留。
