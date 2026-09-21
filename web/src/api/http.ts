/**
 * One way to call the API and one kind of failure.
 *
 * A refused request comes back as `{ kind, message }`, and the message is
 * written to be shown — row numbers, column names, the shapes of cells, never a
 * figure. `Refusal` carries it to the screen unchanged. A request the API could
 * not even parse comes back in FastAPI's own shape and is flattened into the
 * same thing, so a screen handles one kind of failure, not two.
 */
import type { components } from './schema'

export class Refusal extends Error {
  constructor(
    readonly kind: string,
    message: string,
  ) {
    super(message)
  }
}

type Invalid = { detail: { loc?: (string | number)[]; msg: string }[] }

function refusal(url: string, status: number, payload: unknown): Refusal {
  if (payload && typeof payload === 'object') {
    const body = payload as Partial<components['schemas']['Refusal']> & Partial<Invalid>
    if (typeof body.message === 'string') return new Refusal(body.kind ?? 'refused', body.message)
    if (Array.isArray(body.detail)) {
      const reasons = body.detail.map((d) => `${(d.loc ?? []).slice(1).join('.')}: ${d.msg}`)
      return new Refusal('invalid', reasons.join('; '))
    }
  }
  return new Refusal('error', `${url} returned ${status}`)
}

export async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (response.status === 204) return undefined as T
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) throw refusal(url, response.status, payload)
  return payload as T
}

/** A failure as a sentence, whatever threw it. */
export function reason(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
