/**
 * What the sidebar's dots depend on, part by part: the chat model in LM Studio
 * on the desktop, and the backend's Postgres and SearXNG. /health, which says
 * only that Hearth's own backend answers, stays separate. Typed from the API's
 * schema.
 */
import { call } from './http'
import type { components } from './schema'

export type ModelStatus = components['schemas']['ModelStatus']
export type BackendStatus = components['schemas']['BackendStatus']

export const status = {
  model: () => call<ModelStatus>('GET', '/api/status/model'),
  /** Postgres and SearXNG; the backend itself answering is /health's to say. */
  backend: () => call<BackendStatus>('GET', '/api/status/backend'),
}
