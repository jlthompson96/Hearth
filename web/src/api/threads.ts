/**
 * Thread history: the list, search, one thread, pin, delete. Typed from the
 * API's schema; storing turns happens in the chat stream, not here.
 */
import { call } from './http'
import type { components } from './schema'

type Schemas = components['schemas']

export type ThreadListing = Schemas['ThreadListing']
export type ThreadSummary = Schemas['ThreadSummary']
export type ThreadDetail = Schemas['ThreadDetail']
export type StoredMessage = Schemas['StoredMessage']

export const threads = {
  list: (q?: string) =>
    call<ThreadListing>('GET', q ? `/api/threads?q=${encodeURIComponent(q)}` : '/api/threads'),
  get: (id: string) => call<ThreadDetail>('GET', `/api/threads/${id}`),
  pin: (id: string, pinned: boolean) => call<void>('PATCH', `/api/threads/${id}`, { pinned }),
  remove: (id: string) => call<void>('DELETE', `/api/threads/${id}`),
}
