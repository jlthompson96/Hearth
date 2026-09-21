/**
 * Settings: preferences, which change on the next question, and the running
 * configuration, which is shown and never edited. Typed from the API's schema.
 */
import { call } from './http'
import type { components } from './schema'

type Schemas = components['schemas']

export type Settings = Schemas['SettingsOut']
export type Preference = Schemas['PreferenceOut']
export type ConfigSection = Schemas['ConfigSection']
export type Models = Schemas['ModelsOut']
export type ModelOption = Schemas['ModelOption']

export const settings = {
  read: () => call<Settings>('GET', '/api/settings'),
  write: (key: string, value: number | string) =>
    call<void>('PUT', `/api/settings/${key}`, { value }),
  /** A model LM Studio lists, or null for .env's. Loads it before answering. */
  chooseModel: (model: string | null) => call<void>('PUT', '/api/settings/chat-model', { model }),
}
