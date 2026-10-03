/** Creativity 业务客户端；调用方为同一逻辑请求保存同一幂等键。 */
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string) { super(message) }
}
export type RunResponse = { status: number; data: Record<string, unknown> }
export type RunEvent = { sequence?: number; event: string; data: Record<string, unknown> }
export type RequestContext = { method: string; path: string; body?: string; idempotencyKey?: string }
const delay = (attempt: number) => new Promise(resolve => setTimeout(resolve, Math.min(2000, 200 * 2 ** attempt)))

export class Client {
  constructor(private baseUrl: string, public token?: string,
    private headers: (request: RequestContext) => Record<string, string> = () => ({}), private fetcher: typeof fetch = fetch) {}
  private auth(request: RequestContext): Record<string, string> { return { ...this.headers(request), ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}) } }
  private async checked(response: Response): Promise<void> {
    if (response.ok) return
    const body = await response.json().catch(() => ({}))
    throw new ApiError(response.status, body.error?.code ?? 'HTTP_ERROR', body.error?.message ?? '平台请求失败', body.request_id ?? response.headers.get('X-Request-ID'))
  }
  async request(method: string, path: string, body?: unknown, key?: string, retries = 0): Promise<RunResponse> {
    if (retries && method !== 'GET' && !key) throw new Error('重试写请求必须沿用幂等键')
    if (!Number.isInteger(retries) || retries < 0 || retries > 5) throw new Error('重试次数须在零至五次之间')
    const request = { method, path, body: body === undefined ? undefined : JSON.stringify(body), idempotencyKey: key }
    for (let attempt = 0; attempt <= retries; attempt++) {
      let response: Response
      try {
        response = await this.fetcher(`${this.baseUrl.replace(/\/$/, '')}${path}`, { method,
          headers: { ...this.auth(request), 'Content-Type': 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) },
          body: request.body, redirect: 'error', signal: AbortSignal.timeout(30000) })
      } catch (error) { if (attempt === retries) throw error; await delay(attempt); continue }
      if ([502, 503, 504].includes(response.status) && attempt < retries) { await response.body?.cancel(); await delay(attempt); continue }
      await this.checked(response)
      return { status: response.status, data: await response.json() }
    }
    throw new Error('请求重试分支未返回')
  }
  async exchange(credential: Record<string, unknown>) {
    const result = await this.request('POST', '/api/v1/auth/token', credential)
    this.token = String(result.data.access_token)
    return result.data
  }
  createRun(agentCode: string, input: Record<string, unknown>, idempotencyKey: string, delivery = 'sync') {
    if (!idempotencyKey || idempotencyKey.length > 128) throw new Error('请提供稳定幂等键')
    return this.request('POST', '/api/v1/runs', { agent_code: agentCode, input, delivery }, idempotencyKey, 2)
  }
  async run(runId: string) { return (await this.request('GET', `/api/v1/runs/${encodeURIComponent(runId)}`, undefined, undefined, 2)).data }
  async cancel(runId: string) { return (await this.request('POST', `/api/v1/runs/${encodeURIComponent(runId)}/cancel`)).data }
  batch(body: Record<string, unknown>, idempotencyKey: string) { return this.request('POST', '/api/v1/batches', body, idempotencyKey, 2) }
  async *events(runId: string, after = 0, reconnects = 3, signal?: AbortSignal): AsyncGenerator<RunEvent> {
    if (!Number.isSafeInteger(after) || after < 0 || !Number.isInteger(reconnects) || reconnects < 0 || reconnects > 20) throw new Error('游标或重连次数不正确')
    const path = `/api/v1/runs/${encodeURIComponent(runId)}/events`
    let cursor = after
    for (let attempt = 0; attempt <= reconnects; attempt++) {
      let complete = false
      try {
        const response = await this.fetcher(`${this.baseUrl.replace(/\/$/, '')}/api/v1/runs/${encodeURIComponent(runId)}/events`, {
          headers: { ...this.auth({ method: 'GET', path }), Accept: 'text/event-stream', 'Last-Event-ID': String(cursor) }, redirect: 'error', signal })
        await this.checked(response)
        if (!response.body) throw new ApiError(502, 'STREAM_MISSING', '响应缺少事件流')
        const reader = response.body.getReader(), decoder = new TextDecoder()
        let buffer = ''
        try {
          while (true) {
            const { done, value } = await reader.read()
            if (done) break
            buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, '\n')
            if (new TextEncoder().encode(buffer).length > 2097152) throw new ApiError(502, 'EVENT_TOO_LARGE', '事件超过体积上限')
            let end: number
            while ((end = buffer.indexOf('\n\n')) >= 0) {
              const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2)
              let sequence: number | undefined, event = 'message'; const data: string[] = []
              for (const line of frame.split('\n')) {
                if (line.startsWith('id:')) sequence = Number(line.slice(3).trim())
                if (line.startsWith('event:')) event = line.slice(6).trim()
                if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
              }
              if (!data.length) continue
              const payload = JSON.parse(data.join('\n'))
              if (event === 'control') throw new ApiError(payload.status ?? 403, payload.code ?? 'STREAM_STOPPED', payload.message ?? '事件流已停止')
              if (sequence !== undefined && (!Number.isSafeInteger(sequence) || sequence < 0)) throw new ApiError(502, 'CURSOR_INVALID', '事件游标不正确')
              if (sequence === undefined || sequence > cursor) {
                if (sequence !== undefined) cursor = sequence
                yield { sequence, event, data: payload }
              }
              if (event === 'completed') { complete = true; return }
            }
          }
        } finally { await reader.cancel(); reader.releaseLock() }
        const snapshot = await this.run(runId)
        if (['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT'].includes(String(snapshot.state))) return
      } catch (error) { if (signal?.aborted || error instanceof ApiError || attempt === reconnects) throw error }
      if (complete) return
      if (attempt < reconnects) await delay(attempt)
    }
    throw new ApiError(503, 'STREAM_INTERRUPTED', '事件连接已中断，可使用最后游标恢复')
  }
}
