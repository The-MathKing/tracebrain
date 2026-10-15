import type { ChatEvent, GEdge, GNode, Health, MemoryResponse, Overview, Task } from './types'

const BASE = '/api'

async function getJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return (await res.json()) as T
}

export const api = {
  health: () => getJSON<Health>('/health'),
  overview: () => getJSON<Overview>('/graph/overview'),
  node: (key: string, depth = 1) =>
    getJSON<{ nodes: GNode[]; edges: GEdge[] }>(`/graph/node/${encodeURIComponent(key)}?depth=${depth}`),
  memory: (userId: string) => getJSON<MemoryResponse>(`/memory/${encodeURIComponent(userId)}`),
  deleteFact: (userId: string, key: string) =>
    getJSON<unknown>(`/memory/${encodeURIComponent(userId)}/facts/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  tasks: (limit = 50) => getJSON<Task[]>(`/tasks?limit=${limit}`),
}

/**
 * POST /api/chat and parse the text/event-stream body incrementally.
 * EventSource can't POST, so this reads fetch's ReadableStream and splits on blank lines.
 */
export async function streamChat(
  body: { user_id: string; session_id?: string | null; message: string },
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
    body: JSON.stringify({ ...body, session_id: body.session_id ?? undefined }),
    signal,
  })
  if (!res.ok || !res.body) {
    let detail = `${res.status} ${res.statusText}`
    try {
      detail = (await res.text()) || detail
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ''
  const flush = (block: string) => {
    const data = block
      .split('\n')
      .filter((l) => l.startsWith('data:'))
      .map((l) => l.slice(5).replace(/^ /, ''))
      .join('\n')
    if (!data) return
    try {
      onEvent(JSON.parse(data) as ChatEvent)
    } catch {
      console.warn('unparseable SSE data', data)
    }
  }
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += value.replace(/\r\n/g, '\n')
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      flush(buf.slice(0, idx))
      buf = buf.slice(idx + 2)
    }
  }
  if (buf.trim()) flush(buf)
}
