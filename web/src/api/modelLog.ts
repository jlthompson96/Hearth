/**
 * The Model log: runs, and one run in full. Typed from the API's schema.
 */
import { call } from './http'
import type { components } from './schema'

type Schemas = components['schemas']

export type RunListing = Schemas['RunListing']
export type RunSummary = Schemas['RunSummaryOut']
export type RunDetail = Schemas['RunDetailOut']
export type LogEntry = Schemas['EntryOut']

export const modelLog = {
  runs: (before?: string) =>
    call<RunListing>(
      'GET',
      before ? `/api/model-log?before=${encodeURIComponent(before)}` : '/api/model-log',
    ),
  run: (questionId: string) => call<RunDetail>('GET', `/api/model-log/${questionId}`),
}
