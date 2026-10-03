# Creativity SDK

Python 与 TypeScript SDK 调用业务端 `/api/v1`，共享平台的 Token、委托身份、幂等与 SSE 契约。当前为仓库内源码包，未发布到外部包仓库。

## Python

Python 3.12 及以上：`pip install ./sdks/python`。

```python
from creativity_sdk import Client, RequestContext


# 从业务服务的受信会话获取主体，按接入协议生成委托证明；不要由终端传入渠道。
def subject_headers(request: RequestContext) -> dict[str, str]:
    return delegation.sign(
        method=request.method,
        path=request.path,
        body=request.body,
        idempotency_key=request.idempotency_key,
    )


async with Client("https://creativity.example", headers=subject_headers) as client:
    # 平台换取 Token 的请求结构见 /api/v1/auth/token 的 OpenAPI。
    await client.exchange(service_credential)
    response = await client.create_run("report", {"question": "汇总"}, idempotency_key=event_id)
    # 200 是同步完成；202 表示继续查询原运行，不能据此创建第二次运行。
    run_id = response.data["run_id"]
    async for event in client.events(run_id):
        persist_cursor(run_id, event.sequence)
```

`subject_headers` 是应用回调，示例中的 `delegation`、`service_credential`、`event_id`、`persist_cursor` 由业务方提供。非主体接口（如 Token 交换）应由回调返回空委托头。SDK 不接受客户端提供渠道，也不替代业务后端的主体复核。`RequestContext.body` 是实际发送的 UTF-8 字节，回调每次重试/重连重新执行，适合生成短期证明。

## TypeScript

Node.js 22 及以上，在 `sdks/typescript` 内执行 `npm install`、`npm run build`；业务项目可安装这个本地目录，或从构建后的 `dist/index.js` 引用。类型声明在 `dist/index.d.ts`。

```typescript
import { Client } from '@creativity/sdk'

const client = new Client('https://creativity.example', platformToken, request =>
  signSubjectProof(request.method, request.path, new TextEncoder().encode(request.body ?? ''), request.idempotencyKey))
const response = await client.createRun('report', { question: '汇总' }, eventId)
const controller = new AbortController()
for await (const event of client.events(String(response.data.run_id), lastSequence, 3, controller.signal)) {
  saveEvent(event)
}
```

中止订阅只关闭本地事件流；取消服务器运行使用 `cancel(runId)`。Python 取消读取协程也只停止订阅。需要重连时，调用方可持久化最后 `sequence` 后传给 `after`。

## 公共行为

- `create_run` / `createRun` 和 `batch` 自动最多重试两次暂时性网络/502/503/504 错误，正文与幂等键保持不变；409 冲突、401/403 不自动重试。通用 `request` 最多允许五次重试，非 GET 重试必须提供幂等键。
- `run`、`cancel`、`batch` 复用原运行和批次接口。失败任务也是正常可查询的运行结果，需要检查 `state` 和业务结果；不会因状态为 FAILED 自动重建。
- SSE 发送 `Last-Event-ID`，去重已收到游标、识别 completed 和 control 事件；断流后查询终态并有界重连。支持 SUCCEEDED / FAILED / CANCELLED / TIMED_OUT，单事件最大 2 MiB。
- `ApiError` 保留 HTTP 状态、平台错误码与 request_id；不要把内部错误码直接作为终端用户文案。Python `async with` 释放连接；TypeScript 使用标准 fetch，支持调用方 AbortSignal。
- 签名头以请求上下文生成；平台 Token 用于服务器认证。业务委托证明的签发和定期 Token 更新由业务方实现，不能把浏览器缓存身份或外部系统 Token 当作平台 Token。

协议验证：后端 `pytest tests/test_sdk.py`；TypeScript `npm test`。这些测试使用本地协议响应，真实业务委托、身份源与接收方的联调仍需要实际配置。
