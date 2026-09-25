/**
 * Data & imports: the exports waiting in the data folder, importing one, and
 * what has been imported.
 *
 * Nothing is uploaded. The API reads exports from $HEARTH_DATA_DIR, a folder
 * outside the repository, and this screen only ever names a file in it — the
 * export itself never passes through the browser.
 */
import { useCallback, useEffect, useState } from 'react'

import { data, reason, Refusal, type Batch, type ExportFile, type ExportListing, type ImportOutcome } from './api/data'
import { Money } from './money'

type Outcome =
  | { state: 'imported'; outcome: ImportOutcome }
  | { state: 'refused'; file: string; kind: string; message: string }

export function Imports({ active }: { active: boolean }) {
  const [listing, setListing] = useState<ExportListing | null>(null)
  const [history, setHistory] = useState<Batch[]>([])
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [files, batches] = await Promise.all([data.files(), data.imports()])
      setListing(files)
      setHistory(batches)
      setFailure(null)
    } catch (error) {
      setFailure(reason(error))
    }
  }, [])

  // Re-read on every visit: files arrive in the folder while the app is open.
  useEffect(() => {
    if (active) void refresh()
  }, [active, refresh])

  async function run(file: ExportFile, asOf: string) {
    try {
      setOutcome({ state: 'imported', outcome: await data.importFile(file.name, asOf) })
    } catch (error) {
      const kind = error instanceof Refusal ? error.kind : 'error'
      setOutcome({ state: 'refused', file: file.name, kind, message: reason(error) })
    }
    await refresh()
  }

  async function remove(batch: Batch) {
    const sure = window.confirm(
      `Remove the import of ${batch.filename} (as of ${batch.as_of})? Its balances and ` +
        'holdings go with it. The file in the data folder is not touched.',
    )
    if (!sure) return
    try {
      await data.removeImport(batch.id)
      setOutcome(null)
    } catch (error) {
      setFailure(reason(error))
    }
    await refresh()
  }

  return (
    <section className="screen">
      <h2>Data &amp; imports</h2>
      <p className="muted">
        Exports are read from the folder named by <code>HEARTH_DATA_DIR</code>, outside the
        repository
        {listing?.data_dir && (
          <>
            {' '}— <code>{listing.data_dir}</code>
          </>
        )}
        . Nothing is uploaded; a file never passes through the browser.
      </p>

      {failure && <p className="error">{failure}</p>}
      {listing?.fixture_loaded && (
        <p className="notice">
          This database holds the golden fixture — invented accounts. Real data does not go in
          beside it. Run <code>make unseed</code> to empty it before a first import.
        </p>
      )}
      {listing?.problem && <p className="notice">{listing.problem}</p>}

      <h3>In the folder</h3>
      {listing && !listing.problem && listing.files.length === 0 && (
        <p className="muted">No .csv files there yet.</p>
      )}
      {listing && listing.files.length > 0 && (
        <ul className="files">
          {listing.files.map((file) => (
            <FileRow key={file.name} file={file} onImport={run} />
          ))}
        </ul>
      )}

      {outcome?.state === 'imported' && <Imported outcome={outcome.outcome} />}
      {outcome?.state === 'refused' && (
        <div className="refused">
          <p className="refused-title">
            {outcome.file} was not imported. Nothing was written.
          </p>
          <p>{outcome.message}</p>
        </div>
      )}

      <h3>Imported</h3>
      {history.length === 0 ? (
        <p className="muted">Nothing yet.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>As of</th>
              <th>File</th>
              <th>Source</th>
              <th className="num">Rows</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {history.map((batch) => (
              <tr key={batch.id}>
                <td>{batch.as_of}</td>
                <td className="filename">{batch.filename}</td>
                <td className="muted">{batch.source_label}</td>
                <td className="num">{batch.rows}</td>
                <td className="num">
                  <button type="button" className="quiet" onClick={() => void remove(batch)}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

/**
 * One file, with the date to import it as. The date is offered from the
 * filename when it carries one — the importer refuses any other date for such
 * a file anyway — but it is never today's by default, and nothing is sent
 * until a date is there and the button is pressed.
 */
function FileRow({
  file,
  onImport,
}: {
  file: ExportFile
  onImport: (file: ExportFile, asOf: string) => Promise<void>
}) {
  const [asOf, setAsOf] = useState(file.imported_as_of ?? file.date_in_name ?? '')
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    await onImport(file, asOf)
    setBusy(false)
  }

  return (
    <li className="file">
      <div>
        <p className="filename">{file.name}</p>
        <p className="muted small">
          {file.imported_batch ? `Imported as of ${file.imported_as_of}` : 'Not imported'}
          {' · '}
          {Math.max(1, Math.round(file.size_bytes / 1024))} KB
        </p>
      </div>
      {!file.imported_batch && (
        <form className="inline" onSubmit={submit}>
          <label>
            <span className="sr-only">As of</span>
            <input
              type="date"
              required
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
              aria-label={`Date ${file.name} describes`}
            />
          </label>
          <button type="submit" disabled={busy || !asOf}>
            {busy ? '…' : 'Import'}
          </button>
        </form>
      )}
    </li>
  )
}

function Imported({ outcome }: { outcome: ImportOutcome }) {
  if (outcome.accounts.length === 0) {
    // A weight history: no accounts, one weigh-in per day that had one.
    const skipped = outcome.rows - outcome.body_weights
    return (
      <div className="imported">
        <p className="imported-title">
          {outcome.already_imported
            ? `${outcome.filename} was already imported — nothing was written again.`
            : `Imported ${outcome.body_weights} weigh-ins from ${outcome.filename}.`}
        </p>
        {skipped > 0 && (
          <p className="muted small">
            {skipped} {skipped === 1 ? 'day' : 'days'} had no weigh-in recorded and{' '}
            {skipped === 1 ? 'was' : 'were'} skipped.
          </p>
        )}
        <p className="muted small">
          In pounds, as recorded. The moving average is kept with the raw rows and not used — ask
          Forge about your body weight and it works from the weigh-ins.
        </p>
      </div>
    )
  }
  return (
    <div className="imported">
      <p className="imported-title">
        {outcome.already_imported
          ? `${outcome.filename} was already imported — nothing was written again.`
          : `Imported ${outcome.rows} rows from ${outcome.filename} as of ${outcome.as_of}.`}
      </p>
      <table className="table">
        <thead>
          <tr>
            <th>Account</th>
            <th className="num">Positions</th>
            <th className="num">Balance</th>
          </tr>
        </thead>
        <tbody>
          {outcome.accounts.map((account) => (
            <tr key={account.label}>
              <td>{account.label}</td>
              <td className="num">{account.holdings}</td>
              <td className="num">
                <Money amount={account.balance} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        Each balance is the sum of that account&apos;s positions. Check it against the total the
        institution shows before relying on it.
      </p>
    </div>
  )
}
