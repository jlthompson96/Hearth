/**
 * Whether the chat model can answer — LM Studio, on the desktop — as opposed to
 * /health, which says only that Hearth's own backend answers. Typed from the
 * API's schema.
 */
import { call } from './http'
import type { components } from './schema'

export type ModelStatus = components['schemas']['ModelStatus']

export const status = {
  model: () => call<ModelStatus>('GET', '/api/status/model'),
}
