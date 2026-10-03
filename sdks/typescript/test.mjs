import assert from 'node:assert/strict'
import { Client, ApiError } from './dist/index.js'
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
console.log('TypeScript SDK 协议验证通过')
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
