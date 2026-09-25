/**
 * Egress audit: what the local web search tool tried, and whether it was allowed.
 */
import { useCallback, useEffect, useState } from 'react'

import { reason } from './api/http'
import { searchAudit, type SearchAuditEntry } from './api/searchAudit'

function when(iso: string): string {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function SearchAudit({ active }: { active: boolean }) {
  const [entries, setEntries] = useState<SearchAuditEntry[]>([])
  const [failure, setFailure] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const listing = await searchAudit.list()
      setEntries(listing.entries)
      setFailure(null)
    } catch (error) {
      setFailure(reason(error))
    }
  }, [])

  useEffect(() => {
    if (!active) return
    void refresh()
  }, [active, refresh])

  return (
    <section className="audit-screen">
      <div className="audit-intro">
        <h2>Egress audit</h2>
        <p className="muted">
          Every search request the Errand tool attempted, including blocked ones, so the local-only
          policy is visible and reviewable.
        </p>
      </div>

      {failure && <p className="error">{failure}</p>}

      {entries.length === 0 ? (
        <p className="muted">No outgoing searches have been logged yet.</p>
      ) : (
        <div className="audit-table-wrap">
          <table className="table audit-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Query</th>
                <th>Status</th>
                <th>Results</th>
                <th>Violation</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td>{when(entry.created_at)}</td>
                  <td>{entry.query}</td>
                  <td>
                    <span className={`status-mark ${entry.allowed ? 'allowed' : 'blocked'}`}>
                      {entry.allowed ? 'Allowed' : 'Blocked'}
                    </span>
                  </td>
                  <td>{entry.result_count == null ? '—' : entry.result_count.toLocaleString('en-US')}</td>
                  <td>{entry.violation ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
