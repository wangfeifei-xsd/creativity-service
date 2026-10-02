# 运行 SSE 契约 v1

服务路径为 `GET /api/v1/runs/{run_id}/events`，管理路径为 `GET /admin/v1/runs/{run_id}/events`。请求使用 `Authorization: Bearer ...`；业务请求还需 18 的 `X-Business-Delegation`，绑定实际 GET 路径及查询字符串。Token 不得写入 URL，`token`、`access_token` 查询参数直接拒绝。

## 受理与返回

`POST /api/v1/runs` 的 delivery 支持 async、stream、sync。async/stream 返回 202、run_id、state、created_at、deadline、status_url、events_url；不会把 POST 的 HTTP 连接当作执行生命周期。sync 默认最多等待 15 秒，终态返回 200 的 ResultEnvelope，等待超时返回同一运行的 202，不取消或复制任务。

## 帧与顺序

数据库为每个 run 分配从 1 开始的连续 sequence。SSE 的 id 为该 sequence，data 是完整 RunEvent，包括 scope、event_id、run_id、sequence、event_type、payload、occurred_at、expires_at。例如：

```text
id: 7
event: text_delta
data: {"run_id":"run_example","sequence":7,"event_type":"text_delta","payload":{"text":"部分文字","attempt_id":"attempt_example","label":"部分内容","validated":false},"scope":{},"event_id":"event_example","occurred_at":"2026-10-02T00:00:00Z","expires_at":"2026-10-03T00:00:00Z"}

```

示例 scope 简写；实际响应始终携带原始完整范围。accepted、step_started、text_delta、tool_status、result、error、completed 使用相同帧结构。模型路由声明并验证 streaming 能力时才生成 text_delta，delivery 不改变已冻结的模型能力。text_delta 仅是未验证内容；只有 result 事件包含通过 schema 的 BusinessResult，completed 标记技术终态。步骤/尝试标识用于关联，不作为页面名称。

## 重连与失效

客户端保存最后收到的 sequence，重连发送 `Last-Event-ID: 7`；也可使用 `?after_sequence=7`，请求头优先。重复序号忽略，发现缺口时查询快照。默认保留 24 小时；缺失或过期事件返回 410、错误码 EVENTS_EXPIRED，调用 GET 原 status_url 获取结果。超过现有进度、负数或非数字游标返回 422。

每条事件发送前重新读取及授权。默认每 15 秒发送 `: heartbeat` 注释帧，空闲期间仍复核身份；默认每 0.5 秒检查新事件。HTTP 建连前失败使用通常的 401/403/410/503 错误响应。响应头发出后身份或存储失效时发送无 id 的控制帧并关闭：

```text
event: control
data: {"code":"AUTH_EXPIRED","message":"请重新认证","status":401,"snapshot_path":"/api/v1/runs/run_example"}

```

Redis 认证故障同样控制关闭，status=503；权限撤销或删除返回对应错误类别。控制事件不是可重放的运行事件，不占用序号。重新认证后可查询或重连原运行。HTTP/SSE 断线和访问 Token 自然到期都不会触发取消；取消必须显式调用 cancel。

响应使用 `Content-Type: text/event-stream`、`Cache-Control: no-store`、`X-Accel-Buffering: no`。客户端实现位于 `src/api/event-stream.ts`，覆盖 UTF-8 分片、CRLF、多行 data、去重、断线游标和控制关闭。

22 的独立后端客户端见 [examples/backend](../examples/backend/README.md)，支持换 Token、重新签名、逐帧解析、游标去重和过期快照回查；接入流程见 [统一 API 指南](unified-api.md)。
