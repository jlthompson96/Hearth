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

export const settings = {
  read: () => call<Settings>('GET', '/api/settings'),
  write: (key: string, value: number | string) =>
    call<void>('PUT', `/api/settings/${key}`, { value }),
}
