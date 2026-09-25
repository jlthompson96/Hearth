/**
 * Data & imports and Manual entry: the calls, typed from the API's schema.
 * Failures arrive as `Refusal`s — see ./http.
 */
import { call } from './http'
import type { components } from './schema'

export { reason, Refusal } from './http'

type Schemas = components['schemas']

export type ExportListing = Schemas['ExportListing']
export type ExportFile = Schemas['ExportFile']
export type ImportOutcome = Schemas['ImportOutcome']
export type Batch = Schemas['Batch']
export type AccountListing = Schemas['AccountListing']
export type Account = Schemas['Account']
export type Entry = Schemas['Entry']
export type NewAccount = Schemas['NewAccount']
export type NewBalance = Schemas['NewBalance']
export type TrainingListing = Schemas['TrainingListing']
export type WorkoutOut = Schemas['WorkoutOut']
export type BodyWeightOut = Schemas['BodyWeightOut']
export type NewWorkout = Schemas['NewWorkout']
export type NewSet = Schemas['NewSet']
export type NewBodyWeight = Schemas['NewBodyWeight']

export const data = {
  files: () => call<ExportListing>('GET', '/api/imports/files'),
  imports: () => call<Batch[]>('GET', '/api/imports'),
  importFile: (filename: string, as_of: string) =>
    call<ImportOutcome>('POST', '/api/imports', { filename, as_of }),
  removeImport: (id: string) => call<void>('DELETE', `/api/imports/${id}`),

  accounts: () => call<AccountListing>('GET', '/api/accounts'),
  createAccount: (account: NewAccount) => call<Account>('POST', '/api/accounts', account),
  recordBalance: (balance: NewBalance) => call<Entry>('POST', '/api/balances', balance),
  removeBalance: (id: number) => call<void>('DELETE', `/api/balances/${id}`),

  training: () => call<TrainingListing>('GET', '/api/training'),
  recordWorkout: (workout: NewWorkout) => call<WorkoutOut>('POST', '/api/workouts', workout),
  removeWorkout: (id: string) => call<void>('DELETE', `/api/workouts/${id}`),
  recordBodyWeight: (entry: NewBodyWeight) =>
    call<BodyWeightOut>('POST', '/api/body-weights', entry),
  removeBodyWeight: (id: number) => call<void>('DELETE', `/api/body-weights/${id}`),
}
