/**
 * Settings: what you can change, and what you can only see.
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
import { settings, type Preference, type Settings as SettingsData } from './api/settings'

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
        Preferences change on the next question — nothing restarts. The configuration below them
        comes from <code>.env</code> and from code, and is shown, not edited.
      </p>

      <h3>Preferences</h3>
      {data?.preferences.map((preference) => (
        <PreferenceRow
          key={preference.key}
          preference={preference}
          busy={busy}
          onChange={(value) => void change(preference, value)}
        />
      ))}
      {status && (
        <p className={status.ok ? 'saved' : 'error'} role="status">
          {status.text}
        </p>
      )}

      {data?.configuration.map((section) => (
        <div key={section.title}>
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
        </div>
      ))}
      {data && (
        <p className="muted small legend">
          <Lock /> Enforced in code. Shown here so you can see it; there is no switch for it.
        </p>
      )}
    </section>
  )
}
