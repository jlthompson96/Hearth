/**
 * The chat stream.
 *
 * `EventSource` is not used: it only does GET, and a question belongs in a body
 * rather than in a URL that ends up in history and logs. So this reads the
 * `fetch` response stream and parses SSE frames itself, which is about twenty
 * lines and removes the need to work around EventSource at the other end.
 *
 * Frames are separated by a blank line and can be split across network chunks,
 * so the buffer is only consumed up to the last complete separator.
 */
import type { components } from './schema'

export type ChatRequest = components['schemas']['ChatRequest']

export type ChatEvent =
  | { type: 'token'; text: string; provisional: boolean }
  | { type: 'tool'; name: string; args: Record<string, unknown> }
  | { type: 'tool_result'; name: string; result: string }
  | { type: 'routed'; destination: string; confidence: number; router: string }
  | { type: 'refused'; signal: string; message: string }
  | { type: 'done'; reason: string }
  | { type: 'error'; detail: string }
  // Phase 8: which thread the turn was stored in — first on every stream —
  // and, after a new thread's first answer, the title it was given.
  | { type: 'thread'; id: string; created: boolean }
  | { type: 'title'; id: string; title: string }
  // Rule 1 on the real answer: figures it stated that no tool returned.
  | { type: 'ungrounded'; figures: string[] }

/** One SSE frame: `event: <name>` and `data: <json>`. */
function parseFrame(frame: string): ChatEvent | null {
  let name = ''
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event: ')) name = line.slice(7)
    else if (line.startsWith('data: ')) data = line.slice(6)
  }
  if (!name || !data) return null

  try {
    const payload = JSON.parse(data) as Record<string, unknown>
    switch (name) {
      case 'token':
        return {
          type: 'token',
          text: String(payload.text ?? ''),
          provisional: Boolean(payload.provisional),
        }
      case 'tool':
        return {
          type: 'tool',
          name: String(payload.name ?? ''),
          args: (payload.args ?? {}) as Record<string, unknown>,
        }
      case 'tool_result':
        return {
          type: 'tool_result',
          name: String(payload.name ?? ''),
          result: String(payload.result ?? ''),
        }
      case 'routed':
        return {
          type: 'routed',
          destination: String(payload.destination ?? ''),
          confidence: Number(payload.confidence ?? 0),
          router: String(payload.router ?? ''),
        }
      case 'refused':
        return {
          type: 'refused',
          signal: String(payload.signal ?? 'unknown'),
          message: String(payload.message ?? ''),
        }
      case 'done':
        return { type: 'done', reason: String(payload.reason ?? 'complete') }
      case 'error':
        return { type: 'error', detail: String(payload.detail ?? 'unknown error') }
      case 'thread':
        return { type: 'thread', id: String(payload.id ?? ''), created: Boolean(payload.created) }
      case 'title':
        return { type: 'title', id: String(payload.id ?? ''), title: String(payload.title ?? '') }
      case 'ungrounded':
        return {
          type: 'ungrounded',
          figures: Array.isArray(payload.figures) ? payload.figures.map(String) : [],
        }
      default:
        return null
    }
  } catch {
    // A frame that will not parse is a bug worth seeing rather than dropping
    // silently, but it must not take the rest of the stream down with it.
    return { type: 'error', detail: `unparseable event: ${name}` }
  }
}

export async function* streamChat(
  request: ChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  })

  if (!response.ok || !response.body) {
    yield { type: 'error', detail: `/api/chat returned ${response.status}` }
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // Everything before the final blank line is complete; the remainder may be
    // half a frame still arriving.
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const event = parseFrame(frame)
      if (event) yield event
    }
  }
}
