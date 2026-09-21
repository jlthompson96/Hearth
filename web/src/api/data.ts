/**
 * Data & imports and Manual entry: the calls, typed from the API's schema.
 *
 * A refused import or entry comes back as `{ kind, message }`, and the message
 * is written to be shown — row numbers, column names and the shapes of cells,
 * never a figure. `Refusal` carries it to the screen unchanged. A request the
 * API could not even parse comes back in FastAPI's own shape and is flattened
 * into the same thing, so a screen handles one kind of failure, not two.
 */
import type { components } from './schema'

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
    const body = payload as Partial<Schemas['Refusal']> & Partial<Invalid>
    if (typeof body.message === 'string') return new Refusal(body.kind ?? 'refused', body.message)
    if (Array.isArray(body.detail)) {
      const reasons = body.detail.map((d) => `${(d.loc ?? []).slice(1).join('.')}: ${d.msg}`)
      return new Refusal('invalid', reasons.join('; '))
    }
  }
  return new Refusal('error', `${url} returned ${status}`)
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
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
}

/** A failure as a sentence, whatever threw it. */
export function reason(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
