/**
 * Phase 0's frontend is one thing: proof that the dev server runs and that the
 * proxy reaches the backend. The seven screens of the Design canvas arrive with
 * their phases, starting with Chat in Phase 5.
 */
import { useEffect, useState } from 'react'

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
      <h1>Hearth</h1>
      <p className="subtitle">Local-first. Nothing here leaves the machine.</p>

      <section className="card">
        <h2>Backend</h2>
        {probe.state === 'checking' && <p className="muted">checking /health…</p>}
        {probe.state === 'up' && (
          <p>
            <span className="dot up" /> up — v{probe.health.version}
          </p>
        )}
        {probe.state === 'down' && (
          <>
            <p>
              <span className="dot down" /> unreachable
            </p>
            <p className="muted">{probe.detail}</p>
            <p className="muted">Is uvicorn running? `make dev` starts both halves.</p>
          </>
        )}
      </section>
    </main>
  )
}
