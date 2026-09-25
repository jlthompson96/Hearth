/**
 * Manual entry: accounts, and balances typed in by hand — and below them,
 * training (TrainingEntry.tsx).
 *
 * The other door into the database, and the only place accounts are created —
 * an import names accounts but cannot say what kind each one is. A balance is
 * typed as US currency and read by the same strict parser an import uses; a
 * liability is a negative balance, shown in red like every negative figure.
 */
import { useCallback, useEffect, useState } from 'react'

import { data, reason, type AccountListing, type Entry } from './api/data'
import { Money } from './money'
import { TrainingEntry } from './TrainingEntry'

export function ManualEntry({ active }: { active: boolean }) {
  const [listing, setListing] = useState<AccountListing | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setListing(await data.accounts())
      setFailure(null)
    } catch (error) {
      setFailure(reason(error))
    }
  }, [])

  useEffect(() => {
    if (active) void refresh()
  }, [active, refresh])

  async function remove(entry: Entry) {
    if (!window.confirm(`Remove ${entry.label}'s balance for ${entry.as_of}?`)) return
    try {
      await data.removeBalance(entry.id)
    } catch (error) {
      setFailure(reason(error))
    }
    await refresh()
  }

  return (
    <section className="screen">
      <h2>Manual entry</h2>
      {failure && <p className="error">{failure}</p>}
      {listing?.fixture_loaded && (
        <p className="notice">
          This database holds the golden fixture — invented accounts. Nothing is entered beside
          it. Run <code>make unseed</code> to empty it first.
        </p>
      )}

      <h3>Accounts</h3>
      {listing && listing.accounts.length === 0 && (
        <p className="muted">
          None yet. Accounts in an export are matched to these by label, exactly — create one per
          account, named as the export names it.
        </p>
      )}
      {listing && listing.accounts.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Label</th>
              <th>Kind</th>
              <th>As of</th>
              <th className="num">Latest balance</th>
            </tr>
          </thead>
          <tbody>
            {listing.accounts.map((account) => (
              <tr key={account.id}>
                <td>{account.label}</td>
                <td className="muted">{account.kind}</td>
                <td className="muted">{account.latest_as_of ?? '—'}</td>
                <td className="num">
                  {account.latest_balance == null ? (
                    <span className="muted">none</span>
                  ) : (
                    <Money amount={account.latest_balance} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {listing && (
        <div className="forms">
          <NewAccountForm kinds={listing.kinds} onDone={refresh} />
          <BalanceForm labels={listing.accounts.map((a) => a.label)} onDone={refresh} />
        </div>
      )}

      {listing && listing.recent.length > 0 && (
        <>
          <h3>Entered by hand, most recent first</h3>
          <table className="table">
            <tbody>
              {listing.recent.map((entry) => (
                <tr key={entry.id}>
                  <td>{entry.label}</td>
                  <td className="muted">{entry.as_of}</td>
                  <td className="num">
                    <Money amount={entry.balance} />
                  </td>
                  <td className="num">
                    <button type="button" className="quiet" onClick={() => void remove(entry)}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <TrainingEntry active={active} />
    </section>
  )
}

function NewAccountForm({ kinds, onDone }: { kinds: string[]; onDone: () => Promise<void> }) {
  const [label, setLabel] = useState('')
  const [kind, setKind] = useState('')
  const [openedOn, setOpenedOn] = useState('')
  const [refusal, setRefusal] = useState<string | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    try {
      await data.createAccount({ label, kind, opened_on: openedOn || null })
      setLabel('')
      setKind('')
      setOpenedOn('')
      setRefusal(null)
    } catch (error) {
      setRefusal(reason(error))
    }
    await onDone()
  }

  return (
    <form className="card" onSubmit={submit}>
      <h3>New account</h3>
      <label>
        Label
        <input value={label} onChange={(e) => setLabel(e.target.value)} required maxLength={80} />
      </label>
      <p className="muted small">No account numbers, and no last four — four digits in a row is refused.</p>
      <label>
        Kind
        <select value={kind} onChange={(e) => setKind(e.target.value)} required>
          <option value="" disabled>
            Choose…
          </option>
          {kinds.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </label>
      <label>
        Opened on <span className="muted small">(optional)</span>
        <input type="date" value={openedOn} onChange={(e) => setOpenedOn(e.target.value)} />
      </label>
      {refusal && <p className="error">{refusal}</p>}
      <button type="submit" disabled={!label.trim() || !kind}>
        Create
      </button>
    </form>
  )
}

function BalanceForm({ labels, onDone }: { labels: string[]; onDone: () => Promise<void> }) {
  const [account, setAccount] = useState('')
  // Empty, and required: the date a balance was true is the person's to state.
  const [asOf, setAsOf] = useState('')
  const [balance, setBalance] = useState('')
  const [refusal, setRefusal] = useState<string | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    try {
      await data.recordBalance({ account, as_of: asOf, balance })
      setBalance('')
      setRefusal(null)
    } catch (error) {
      setRefusal(reason(error))
    }
    await onDone()
  }

  return (
    <form className="card" onSubmit={submit}>
      <h3>Record a balance</h3>
      <label>
        Account
        <select value={account} onChange={(e) => setAccount(e.target.value)} required>
          <option value="" disabled>
            {labels.length ? 'Choose…' : 'Create an account first'}
          </option>
          {labels.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </label>
      <label>
        As of
        <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} required />
      </label>
      <label>
        Balance
        <input
          value={balance}
          onChange={(e) => setBalance(e.target.value)}
          placeholder="$1,234.56"
          inputMode="decimal"
          required
        />
      </label>
      <p className="muted small">US dollars. A liability — a card, a loan — is negative: -$950.00.</p>
      {refusal && <p className="error">{refusal}</p>}
      <button type="submit" disabled={!account || !asOf || !balance.trim()}>
        Record
      </button>
    </form>
  )
}
