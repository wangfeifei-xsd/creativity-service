import assert from 'node:assert/strict'
import test from 'node:test'
import { Client, ApiError } from './dist/index.js'

await test('保留 202、幂等键、错误和 SSE 游标', async () => {
  const calls = []
  let streams = 0
  const client = new Client('https://platform.test', 'token', () => ({}), async (url, init) => {
    calls.push({ url, init })
    if (url.endsWith('/events')) {
      streams++
      if (streams === 1) return new Response('id: 1\nevent: accepted\ndata: {"ok":true}\n\n')
      assert.equal(init.headers['Last-Event-ID'], '1')
      return new Response('id: 2\nevent: completed\ndata: {"state":"FAILED"}\n\n')
    }
    if (url.endsWith('/cancel')) return Response.json({ error: { code: 'FORBIDDEN', message: '授权失效' }, request_id: 'request-fixture' }, { status: 403 })
    if (init.method === 'GET') return Response.json({ state: 'RUNNING' })
    if (calls.length === 1) return Response.json({}, { status: 503 })
    return Response.json({ run_id: 'run-one', state: 'QUEUED' }, { status: 202 })
  })
  const result = await client.createRun('example', {}, 'stable-key')
  assert.equal(result.status, 202)
  assert.equal(calls[0].init.headers['Idempotency-Key'], calls[1].init.headers['Idempotency-Key'])
  assert.equal(calls[0].init.body, calls[1].init.body)
  const events = []
  for await (const event of client.events('run-one')) events.push(event)
  assert.deepEqual(events.map(event => event.sequence), [1, 2])
  await assert.rejects(client.cancel('run-one'), error => error instanceof ApiError && error.status === 403 && error.requestId === 'request-fixture')
})

await test('识别跨数据块 CRLF 和请求签名上下文', async () => {
  const signed = []
  const framing = new Client('https://platform.test', 'token', request => { signed.push(request); return {} }, async () => {
    const chunks = ['id: 3\r', '\nevent: completed\r\ndata: {"state":"TIMED_OUT"}\r', '\n\r', '\n']
    return new Response(new ReadableStream({ start(controller) { for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk)); controller.close() } }))
  })
  const framed = []
  for await (const event of framing.events('run-three')) framed.push(event)
  assert.equal(framed[0].data.state, 'TIMED_OUT')
  assert.equal(signed[0].path, '/api/v1/runs/run-three/events')
  assert.equal(signed[0].method, 'GET')
})

function eventFrame(sequence, kind, payload) {
  const data = { sequence, event_type: kind, payload }
  return `id: ${sequence}\nevent: ${kind}\ndata: ${JSON.stringify(data)}\n\n`
}

for (const state of ['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT']) {
  for (const received of [1, 2]) {
    await test(`${state} 提前断流后补齐事件，已交付 ${received} 条`, async () => {
      const cursors = []
      const kind = state === 'SUCCEEDED' ? 'result' : 'error'
      const payload = kind === 'result' ? { data: { answer: '完整结果' } } : { code: state }
      const frames = [
        eventFrame(1, 'text_delta', { text: '部分内容' }),
        eventFrame(2, kind, payload),
        eventFrame(3, 'completed', { state }),
      ]
      const client = new Client('https://platform.test', 'token', () => ({}), async (url, init) => {
        if (url.endsWith('/events')) {
          cursors.push(init.headers['Last-Event-ID'])
          // 下一帧只收到一部分，重连再重放最后已交付事件，验证游标和去重。
          return new Response(cursors.length === 1
            ? frames.slice(0, received).join('') + frames[received].slice(0, 12)
            : frames.slice(received - 1).join(''))
        }
        return Response.json({ state, [kind]: payload })
      })
      const events = []
      for await (const event of client.events('run-one', 0, 1)) events.push(event)
      assert.deepEqual(cursors, ['0', String(received)])
      assert.deepEqual(events.map(event => event.sequence), [1, 2, 3])
      assert.deepEqual(events.map(event => event.event), ['text_delta', kind, 'completed'])
      assert.deepEqual(events[1].data.payload, payload)
      assert.deepEqual(events[2].data.payload, { state })
    })
  }
}

for (const reconnects of [0, 1]) {
  await test(`重连 ${reconnects} 次仍缺少完成事件时明确失败`, async () => {
    const cursors = [], received = []
    const client = new Client('https://platform.test', 'token', () => ({}), async (url, init) => {
      if (url.endsWith('/events')) {
        cursors.push(init.headers['Last-Event-ID'])
        return new Response(eventFrame(1, 'result', { data: '完整结果' }))
      }
      return Response.json({ state: 'SUCCEEDED', result: { data: '完整结果' } })
    })
    await assert.rejects(async () => {
      for await (const event of client.events('run-one', 0, reconnects)) received.push(event)
    }, error => error instanceof ApiError && error.status === 503 && error.code === 'STREAM_INTERRUPTED')
    assert.deepEqual(cursors, ['0', ...Array(reconnects).fill('1')])
    assert.deepEqual(received.map(event => event.event), ['result'])
  })
}

for (const failure of ['expired', 'control']) {
  await test(`运行已终结后仍传播补取事件的 ${failure} 错误`, async () => {
    const cursors = []
    const client = new Client('https://platform.test', 'token', () => ({}), async (url, init) => {
      if (url.endsWith('/events')) {
        cursors.push(init.headers['Last-Event-ID'])
        if (cursors.length === 1) return new Response(eventFrame(1, 'text_delta', { text: '部分内容' }))
        if (failure === 'expired') return Response.json({ error: { code: 'EVENTS_EXPIRED', message: '事件已过期' }, request_id: 'request-expired' }, { status: 410 })
        return new Response('event: control\ndata: {"status":401,"code":"AUTH_EXPIRED"}\n\n')
      }
      return Response.json({ state: 'SUCCEEDED' })
    })
    await assert.rejects(async () => {
      for await (const event of client.events('run-one')) assert.equal(event.event, 'text_delta')
    }, error => error instanceof ApiError && (failure === 'expired'
      ? error.status === 410 && error.code === 'EVENTS_EXPIRED' && error.requestId === 'request-expired'
      : error.status === 401 && error.code === 'AUTH_EXPIRED'))
    assert.deepEqual(cursors, ['0', '1'])
  })
}
