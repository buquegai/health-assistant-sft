import type { ApiErrorBody } from './types'
import { ApiError } from './types'

export { ApiError } from './types'

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
  })
  if (!response.ok) {
    const result = await response.json().catch(() => null) as { error?: ApiErrorBody } | null
    const body = result?.error ?? { code: 'REQUEST_FAILED', message: '暂时无法连接服务，请稍后重试。' }
    throw new ApiError(response.status, body, response.headers.get('X-Request-ID'))
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) => request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

export interface SseEvent {
  event: string
  data: Record<string, unknown>
}

export async function* readEventStream(response: Response): AsyncGenerator<SseEvent> {
  if (!response.body) throw new Error('当前浏览器不支持流式响应。')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let streamEnded = false
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      let boundary = /\r\n\r\n|\n\n|\r\r/.exec(buffer)
      while (boundary) {
        const block = buffer.slice(0, boundary.index)
        buffer = buffer.slice(boundary.index + boundary[0].length)
        const event = parseEvent(block)
        if (event) yield event
        boundary = /\r\n\r\n|\n\n|\r\r/.exec(buffer)
      }
      if (done) {
        streamEnded = true
        break
      }
    }
    if (buffer.trim()) {
      const event = parseEvent(buffer)
      if (event) yield event
    }
  } finally {
    if (!streamEnded) void reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}

function parseEvent(block: string): SseEvent | null {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split(/\r\n|\r|\n/)) {
    if (!line || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon < 0 ? line : line.slice(0, colon)
    const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '')
    if (field === 'event') event = value
    if (field === 'data') data.push(value)
  }
  if (!data.length) return null
  try {
    return { event, data: JSON.parse(data.join('\n')) as Record<string, unknown> }
  } catch {
    return null
  }
}
