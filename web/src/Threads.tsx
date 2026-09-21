/**
 * The thread panel: every stored conversation, pinned first, and a search that
 * finds one by a word you remember typing.
 *
 * Search is the API's Postgres full-text — English stemming, so "squat" finds
 * the thread where you typed "squats" — and each hit shows the words that
 * matched in the message they matched in. The retention rule is printed under
 * the list, where it applies.
 */
import { useEffect, useState } from 'react'

import { reason } from './api/http'
import { threads, type ThreadListing, type ThreadSummary } from './api/threads'

const DAY_MS = 86_400_000

function when(iso: string): string {
  const date = new Date(iso)
  const today = new Date()
  if (date.toDateString() === today.toDateString()) {
    return date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  }
  const sameYear = date.getFullYear() === today.getFullYear()
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  })
}

export function ThreadPanel({
  open,
  onToggle,
  current,
  onOpen,
  onNew,
  onDeleted,
  version,
}: {
  open: boolean
  onToggle: () => void
  current: string | null
  onOpen: (id: string) => void
  onNew: () => void
  onDeleted: (id: string) => void
  /** Bumped by the chat whenever a turn lands, so the list re-reads. */
  version: number
}) {
  const [query, setQuery] = useState('')
  const [listing, setListing] = useState<ThreadListing | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)

  useEffect(() => {
    if (!open) return
    let stale = false
    // Wait for typing to pause; every keystroke is not a query worth running.
    const timer = window.setTimeout(
      () => {
        threads
          .list(query.trim() || undefined)
          .then((found) => {
            if (stale) return
            setListing(found)
            setFailure(null)
          })
          .catch((error: unknown) => !stale && setFailure(reason(error)))
      },
      query ? 250 : 0,
    )
    return () => {
      stale = true
      window.clearTimeout(timer)
    }
  }, [open, query, version, refresh])

  async function pin(thread: ThreadSummary) {
    if (thread.pinned && listing) {
      const idle = (Date.now() - new Date(thread.updated_at).getTime()) / DAY_MS
      if (
        idle > listing.retention_days &&
        !window.confirm(
          `Nothing has been added to this thread for over ${listing.retention_days} days. ` +
            'Unpinned, it will be deleted the next time a thread is started. Unpin it?',
        )
      ) {
        return
      }
    }
    try {
      await threads.pin(thread.id, !thread.pinned)
    } catch (error) {
      setFailure(reason(error))
    }
    setRefresh((n) => n + 1)
  }

  async function remove(thread: ThreadSummary) {
    if (!window.confirm(`Delete "${thread.title ?? 'Untitled'}" and every message in it?`)) return
    try {
      await threads.remove(thread.id)
      onDeleted(thread.id)
    } catch (error) {
      setFailure(reason(error))
    }
    setRefresh((n) => n + 1)
  }

  if (!open) {
    return (
      <aside className="panel closed" aria-label="Threads">
        <button
          type="button"
          className="panel-toggle"
          onClick={onToggle}
          title="Show threads"
          aria-label="Show threads"
        >
          ›
        </button>
      </aside>
    )
  }

  return (
    <aside className="panel" aria-label="Threads">
      <div className="panel-head">
        <button type="button" className="new-thread" onClick={onNew}>
          New thread
        </button>
        <button
          type="button"
          className="panel-toggle"
          onClick={onToggle}
          title="Hide threads"
          aria-label="Hide threads"
        >
          ‹
        </button>
      </div>

      <input
        type="search"
        className="thread-search"
        placeholder="Search threads"
        aria-label="Search threads"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      {failure && <p className="error small">{failure}</p>}

      <ul className="threads">
        {listing?.threads.map((thread) => (
          <li key={thread.id} className={thread.id === current ? 'thread active' : 'thread'}>
            <button type="button" className="thread-open" onClick={() => onOpen(thread.id)}>
              <span className="thread-title">
                {thread.pinned && <span className="pin-mark" title="Pinned — kept past the year" />}
                {thread.title ?? <em>Untitled</em>}
              </span>
              <span className="thread-date">{when(thread.updated_at)}</span>
              {thread.snippet && (
                <span className="snippet">
                  {thread.snippet.map((part, i) =>
                    part.match ? <mark key={i}>{part.text}</mark> : <span key={i}>{part.text}</span>,
                  )}
                </span>
              )}
            </button>
            <span className="thread-actions">
              <button type="button" className="quiet" onClick={() => void pin(thread)}>
                {thread.pinned ? 'Unpin' : 'Pin'}
              </button>
              <button type="button" className="quiet" onClick={() => void remove(thread)}>
                Delete
              </button>
            </span>
          </li>
        ))}
      </ul>

      {listing && listing.threads.length === 0 && (
        <p className="muted small">{query.trim() ? 'Nothing matches.' : 'No threads yet.'}</p>
      )}

      {listing && (
        <p className="muted small retention">
          Kept for {listing.retention_days} days after the last message. Pinned threads are kept.
        </p>
      )}
    </aside>
  )
}
