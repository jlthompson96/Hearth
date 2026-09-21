/**
 * The shell: three of the Design canvas's seven screens, one per capability
 * that exists behind it — Chat (Phase 5), and Data & imports and Manual entry
 * (Phase 2). The rest land with their phases; a screen built before its backend
 * is a mock with a router in front of it.
 *
 * The screen is in the URL hash, so a reload or a bookmark lands where you
 * were. All three stay mounted and the inactive ones are hidden rather than
 * unmounted: Chat holds its conversation in memory until Phase 8 stores it,
 * and switching tabs should not throw it away.
 *
 * The backend probe from Phase 0 stays, reduced to a dot in the header. It is
 * the first thing you want to know when an answer does not arrive.
 */
import { useEffect, useState } from 'react'

import { Chat } from './Chat'
import { Imports } from './Imports'
import { ManualEntry } from './ManualEntry'
// Generated from the API's OpenAPI schema by `npm run gen:types`, and committed
// so a fresh clone typechecks without a backend running. Never hand-edited.
import type { components } from './api/schema'

type Health = components['schemas']['Health']

type Probe =
  | { state: 'checking' }
  | { state: 'up'; health: Health }
  | { state: 'down'; detail: string }

const SCREENS = [
  { hash: '#/', name: 'Chat' },
  { hash: '#/imports', name: 'Data & imports' },
  { hash: '#/entry', name: 'Manual entry' },
] as const

type Screen = (typeof SCREENS)[number]['hash']

function current(): Screen {
  return SCREENS.find((s) => s.hash === window.location.hash)?.hash ?? '#/'
}

export function App() {
  const [probe, setProbe] = useState<Probe>({ state: 'checking' })
  const [screen, setScreen] = useState<Screen>(current)

  useEffect(() => {
    const follow = () => setScreen(current())
    window.addEventListener('hashchange', follow)
    return () => window.removeEventListener('hashchange', follow)
  }, [])

  useEffect(() => {
    const controller = new AbortController()

    fetch('/health', { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`/health returned ${response.status}`)
        setProbe({ state: 'up', health: (await response.json()) as Health })
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setProbe({ state: 'down', detail: error instanceof Error ? error.message : String(error) })
      })

    return () => controller.abort()
  }, [])

  return (
    <main>
      <header className="masthead">
        <div>
          <h1>Hearth</h1>
          <p className="subtitle">Local-first. Nothing here leaves the machine.</p>
        </div>
        <p className="probe" title={probe.state === 'down' ? probe.detail : undefined}>
          <span className={`dot ${probe.state === 'up' ? 'up' : 'down'}`} />
          {probe.state === 'up' && `v${probe.health.version}`}
          {probe.state === 'checking' && 'checking'}
          {probe.state === 'down' && 'backend unreachable'}
        </p>
      </header>

      <nav className="tabs" aria-label="Screens">
        {SCREENS.map((s) => (
          <a key={s.hash} href={s.hash} aria-current={s.hash === screen ? 'page' : undefined}>
            {s.name}
          </a>
        ))}
      </nav>

      <div className="view" hidden={screen !== '#/'}>
        <Chat />
      </div>
      <div className="view" hidden={screen !== '#/imports'}>
        <Imports active={screen === '#/imports'} />
      </div>
      <div className="view" hidden={screen !== '#/entry'}>
        <ManualEntry active={screen === '#/entry'} />
      </div>
    </main>
  )
}
