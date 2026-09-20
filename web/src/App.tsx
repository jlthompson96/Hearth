/**
 * Phase 5: the Chat screen, the first of the Design canvas's seven.
 *
 * The backend probe from Phase 0 stays, reduced to a dot in the header. It is
 * the first thing you want to know when an answer does not arrive, and it costs
 * one request on mount.
 */
import { useEffect, useState } from 'react'

import { Chat } from './Chat'
// Generated from the API's OpenAPI schema by `npm run gen:types`, and committed
// so a fresh clone typechecks without a backend running. Never hand-edited.
import type { components } from './api/schema'

type Health = components['schemas']['Health']

type Probe =
  | { state: 'checking' }
  | { state: 'up'; health: Health }
  | { state: 'down'; detail: string }

export function App() {
  const [probe, setProbe] = useState<Probe>({ state: 'checking' })

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

      <Chat />
    </main>
  )
}
