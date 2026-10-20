# Creativity SDK

Python 与 TypeScript SDK 调用业务端 `/api/v1`，共享渠道 Token、可选用户标识、幂等与 SSE 契约。当前为仓库内源码包，未发布到外部包仓库。渠道 Key 与密钥只由业务后端保存。

## Python

Python 3.12 及以上：`pip install ./sdks/python`。

```python
from creativity_sdk import Client, RequestContext


# 当前用户标识来自业务后端已验证的登录会话。
def subject_headers(request: RequestContext) -> dict[str, str]:
    if request.path == "/api/v1/auth/token":
        return {}
    return {"X-Subject-Type": "user", "X-Subject-ID": current_user_id}


async with Client("https://creativity.example", headers=subject_headers) as client:
    # 平台换取 Token 的请求结构见 /api/v1/auth/token 的 OpenAPI。
    await client.exchange({"key_id": channel_key, "secret": channel_secret})
    response = await client.create_run("report", {"question": "汇总"}, idempotency_key=event_id)
    # 200 是同步完成；202 表示继续查询原运行，不能据此创建第二次运行。
    run_id = response.data["run_id"]
    async for event in client.events(run_id):
        persist_cursor(run_id, event.sequence)
```

`subject_headers` 是应用回调；用户标识由业务后端提供，只用于内容归属。可省略回调以使用渠道服务身份。Token 交换不需要主体头。SDK 不接受客户端选择渠道，业务后端负责自己的登录和业务权限。

## TypeScript

Node.js 22 及以上，在 `sdks/typescript` 内执行 `npm install`、`npm run build`；业务项目可安装这个本地目录，或从构建后的 `dist/index.js` 引用。类型声明在 `dist/index.d.ts`。

```typescript
import { Client } from '@creativity/sdk'

const client = new Client('https://creativity.example', platformToken, () =>
  ({ 'X-Subject-Type': 'user', 'X-Subject-ID': currentUserId }))
const response = await client.createRun('report', { question: '汇总' }, eventId)
const controller = new AbortController()
for await (const event of client.events(String(response.data.run_id), lastSequence, 3, controller.signal)) {
  saveEvent(event)
}
```

中止订阅只关闭本地事件流；取消服务器运行使用 `cancel(runId)`。Python 取消读取协程也只停止订阅。需要重连时，调用方可持久化最后 `sequence` 后传给 `after`，并保存是否已处理 `completed`；已经完成的订阅无需再次建立。

## 公共行为

- `create_run` / `createRun` 和 `batch` 自动最多重试两次暂时性网络/502/503/504 错误，正文与幂等键保持不变；409 冲突、401/403 不自动重试。通用 `request` 最多允许五次重试，非 GET 重试必须提供幂等键。
- `run`、`cancel`、`batch` 复用原运行和批次接口。失败任务也是正常可查询的运行结果，需要检查 `state` 和业务结果；不会因状态为 FAILED 自动重建。
- SSE 发送 `Last-Event-ID`，去重已收到游标、识别 completed 和 control 事件；正常结束以收到 `completed` 为准，查询到运行终态不能证明最终事件已交付。提前断流时按最后完整事件游标有界重连，补取 `result` / `error` 和 `completed`；耗尽重连次数仍未完成则报 `STREAM_INTERRUPTED`，不会静默结束。事件过期、鉴权或 control 错误直接交给调用方处理，可另行调用 `run` 查询原运行。支持 SUCCEEDED / FAILED / CANCELLED / TIMED_OUT，单事件最大 2 MiB。
- `ApiError` 保留 HTTP 状态、平台错误码与 request_id；不要把内部错误码直接作为终端用户文案。Python `async with` 释放连接；TypeScript 使用标准 fetch，支持调用方 AbortSignal。
- 用渠道 Key 与密钥交换平台 Token，业务后端负责到期后刷新。可选用户标识来自后端验证的登录会话，无需委托签名或资源名单。

协议验证：后端 `pytest tests/test_sdk.py`；TypeScript `npm test`。这些测试使用本地协议响应，真实业务后端、MCP 源与接收方的联调仍需要实际配置。
