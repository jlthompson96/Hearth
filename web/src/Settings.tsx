/**
 * Settings: what you can change, and what you can only see.
 *
 * The chat model can be switched here among the models LM Studio lists and
 * this card can run; .env's CHAT_MODEL stays the default.
 *
 * Preferences are saved the moment one changes and are in effect on the next
 * question — they live in Postgres and are read where they are used, so there
 * is no "save and restart". The running configuration is shown read-only: it
 * comes from .env at startup and from constants in code, and a page that
 * edited it would either restart the server or show values that were not in
 * effect. A lock marks what code enforces; the page shows the state, it does
 * not offer a way round it.
 */
import { useCallback, useEffect, useState } from 'react'

import { reason } from './api/http'
import { settings, type Models, type Preference, type Settings as SettingsData } from './api/settings'

/** How each value reads on screen. The API sends the raw value. */
function choiceLabel(key: string, value: number | string): string {
  if (key === 'default_detail') {
    return { brief: 'Less', normal: 'Normal', detailed: 'More' }[String(value)] ?? String(value)
  }
  const days = Number(value)
  if (days === 365) return '1 year'
  if (days === 730) return '2 years'
  return `${days} days`
}

function Lock() {
  return (
    <svg
      className="lock"
      viewBox="0 0 16 16"
      width="12"
      height="12"
      aria-label="Enforced in code"
      role="img"
    >
      <rect x="3" y="7" width="10" height="7" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  )
}

/** The context length every budget in Hearth is measured against. */
const CONTEXT = 8192

function gib(bytes: number): string {
  return `${(bytes / 2 ** 30).toFixed(2)} GiB`
}

/** The chat model: every one LM Studio has, the ones this card cannot run
 *  shown with the reason, and a switch that loads before it unloads. */
function ModelSection({
  models,
  onSwitched,
}: {
  models: Models
  onSwitched: (text: string, ok: boolean) => void
}) {
  const [picked, setPicked] = useState(models.active)
  const [switching, setSwitching] = useState<string | null>(null)

  useEffect(() => setPicked(models.active), [models.active])

  const option = models.options.find((o) => o.key === picked)
  const inUse = models.options.find((o) => o.key === models.active)
  // Loaded, but not at the length every budget assumes — LM Studio loaded it on
  // first use with whatever it has saved for it. Merely unloaded after idling
  // is not drift: it reloads with those same saved settings.
  const drifted =
    inUse !== undefined &&
    inUse.loaded_contexts.length > 0 &&
    !inUse.loaded_contexts.includes(CONTEXT)

  async function switchTo(key: string | null) {
    const name = key ?? models.default
    setSwitching(name)
    try {
      await settings.chooseModel(key)
      onSwitched(`Answering with ${name} from the next question.`, true)
    } catch (error) {
      onSwitched(reason(error), false)
    } finally {
      setSwitching(null)
    }
  }

  if (models.unavailable) {
    return (
      <>
        <p className="notice">{models.unavailable}</p>
        <p className="muted small">
          Answering with <code>{models.active}</code>.
        </p>
      </>
    )
  }

  return (
    <div className="models">
      <p className="muted small">
        Answering with <code>{models.active}</code>
        {models.active !== models.default && (
          <>
            {' '}
            — chosen here; <code>.env</code> says <code>{models.default}</code>.{' '}
            <button
              type="button"
              className="quiet"
              disabled={switching !== null}
              onClick={() => void switchTo(null)}
            >
              Use .env&apos;s model
            </button>
          </>
        )}
      </p>
      <fieldset disabled={switching !== null}>
        <legend className="sr-only">Chat model</legend>
        {models.options.map((o) => (
          <label key={o.key} className={`model${o.refused ? ' refused-model' : ''}`}>
            <input
              type="radio"
              name="chat-model"
              value={o.key}
              checked={picked === o.key}
              disabled={o.refused !== null}
              onChange={() => setPicked(o.key)}
            />
            <span className="model-name">
              {o.name}
              {o.key === models.active && <span className="tag">in use</span>}
              {o.key === models.active && o.loaded_contexts.length === 0 && (
                <span className="tag quiet-tag">not loaded</span>
              )}
              {o.loaded_contexts.length > 0 && o.key !== models.active && (
                <span className="tag">loaded</span>
              )}
            </span>
            <span className="muted small model-facts">
              {o.params ?? '?'} · {gib(o.size_bytes)} ·{' '}
              {o.measured ? 'measured by make eval' : 'never measured by make eval'}
            </span>
            {o.refused && <span className="muted small model-why">Not offered: {o.refused}.</span>}
            {o.loaded_contexts.some((n) => n !== CONTEXT) && (
              <span className="small model-warn">
                Loaded at {o.loaded_contexts.filter((n) => n !== CONTEXT).join(', ')} tokens. Every
                budget here assumes {CONTEXT.toLocaleString('en-US')}.
              </span>
            )}
          </label>
        ))}
      </fieldset>
      <p className="model-actions">
        {picked === models.active ? (
          <button
            type="button"
            disabled={switching !== null || !drifted}
            onClick={() => void switchTo(models.active === models.default ? null : models.active)}
          >
            {switching
              ? `Loading ${switching}…`
              : drifted
                ? `Reload at ${CONTEXT.toLocaleString('en-US')} tokens`
                : 'In use'}
          </button>
        ) : (
          <button
            type="button"
            disabled={switching !== null || !option || option.refused !== null}
            onClick={() => void switchTo(picked)}
          >
            {switching ? `Loading ${switching}…` : `Switch to ${option?.name ?? picked}`}
          </button>
        )}
        <span className="muted small">
          Loads it at 8,192 tokens, then unloads the one it replaces. A failed load changes
          nothing.
        </span>
      </p>
    </div>
  )
}

function PreferenceRow({
  preference,
  onChange,
  busy,
}: {
  preference: Preference
  onChange: (value: number | string) => void
  busy: boolean
}) {
  const id = `pref-${preference.key}`
  return (
    <div className="pref">
      <label htmlFor={id}>{preference.label}</label>
      <select
        id={id}
        value={String(preference.value)}
        disabled={busy}
        onChange={(e) => {
          const chosen = preference.allowed.find((v) => String(v) === e.target.value)
          if (chosen !== undefined) onChange(chosen)
        }}
      >
        {preference.allowed.map((value) => (
          <option key={String(value)} value={String(value)}>
            {choiceLabel(preference.key, value)}
            {value === preference.default ? ' (default)' : ''}
          </option>
        ))}
      </select>
      <p className="muted small">{preference.help}</p>
    </div>
  )
}

export function Settings({ active }: { active: boolean }) {
  const [data, setData] = useState<SettingsData | null>(null)
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setData(await settings.read())
    } catch (error) {
      setStatus({ ok: false, text: reason(error) })
    }
  }, [])

  useEffect(() => {
    if (active) void refresh()
  }, [active, refresh])

  async function change(preference: Preference, value: number | string) {
    setBusy(true)
    try {
      await settings.write(preference.key, value)
      setStatus({
        ok: true,
        text: `${preference.label}: ${choiceLabel(preference.key, value)}. In effect on the next question.`,
      })
      await refresh()
    } catch (error) {
      setStatus({ ok: false, text: reason(error) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="screen">
      <h2>Settings</h2>
      <p className="muted">
        The chat model and preferences change on the next question — nothing restarts. The
        configuration below them comes from <code>.env</code> and from code, and is shown, not
        edited.
      </p>
      {status && (
        <p className={status.ok ? 'saved' : 'error'} role="status">
          {status.text}
        </p>
      )}

      <section className="group">
        <h3>Chat model</h3>
        {data && (
          <ModelSection
            models={data.models}
            onSwitched={(text, ok) => {
              setStatus({ ok, text })
              void refresh()
            }}
          />
        )}
      </section>

      <section className="group">
        <h3>Preferences</h3>
        {data?.preferences.map((preference) => (
          <PreferenceRow
            key={preference.key}
            preference={preference}
            busy={busy}
            onChange={(value) => void change(preference, value)}
          />
        ))}
      </section>

      {data?.configuration.map((section) => (
        <section className="group" key={section.title}>
          <h3>{section.title}</h3>
          <table className="table config">
            <tbody>
              {section.items.map((item) => (
                <tr key={item.label}>
                  <th scope="row">{item.label}</th>
                  <td>
                    <code>{item.value}</code>
                    {item.note && <span className="muted small note">{item.note}</span>}
                  </td>
                  <td className="locked">{item.locked && <Lock />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
      {data && (
        <p className="muted small legend">
          <Lock /> Enforced in code. Shown here so you can see it; there is no switch for it.
        </p>
      )}
    </section>
  )
}
