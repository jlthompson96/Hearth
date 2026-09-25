/**
 * Search egress audit: what the local web tool tried, and whether it was allowed.
 */
import { call } from './http'

export type SearchAuditEntry = {
  id: number
  query: string
  allowed: boolean
  violation: string | null
  result_count: number | null
  thread_id: string | null
  created_at: string
}

export type SearchAuditListing = {
  entries: SearchAuditEntry[]
}

export const searchAudit = {
  list: (before?: string) =>
    call<SearchAuditListing>(
      'GET',
      before ? `/api/search-audit?before=${encodeURIComponent(before)}` : '/api/search-audit',
    ),
}
