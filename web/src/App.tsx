/**
 * The shell: five of the Design canvas's seven screens, one per capability
 * that exists behind it — Chat (Phase 5), Data & imports and Manual entry
 * (Phase 2), and the Model log and Settings. Egress audit and Connections land
 * with their phases; a screen built before its backend is a mock with a router
 * in front of it.
 *
 * The screen is in the URL hash, so a reload or a bookmark lands where you
 * were; a screen may carry its own state after a `?` (the open thread, the open
 * run). Every screen stays mounted and the inactive ones are hidden rather than
 * unmounted, so switching tabs mid-answer does not cut the stream off.
 *
 * Side panels collapse — the navigation on the left, and Chat's thread list —
 * and whether each is open is one localStorage key read once on mount, not per
 * route (docs/plan.md). It is a convenience: if storage is blocked or empty,
 * every panel starts open and nothing else changes.
 *
 * The backend probe from Phase 0 stays, reduced to a dot at the foot of the
 * navigation. It is the first thing you want to know when an answer does not
 * arrive.
 *
 * When the backend asks for a password (api/auth.py), the sign-in screen
 * stands in for all of this until there is a session, and comes back whenever
 * a request is refused for want of one. A session probe that fails opens the
 * shell, so an unreachable backend shows as the dot rather than as a sign-in
 * that cannot work.
 */
import { useEffect, useState } from 'react'

import { Chat } from './Chat'
import { Imports } from './Imports'
import { ManualEntry } from './ManualEntry'
import { ModelLog } from './ModelLog'
import { SearchAudit } from './SearchAudit'
import { Settings } from './Settings'
import { Sidebar, type NavItem, type Health as SidebarHealth } from './Sidebar'
import { SignIn } from './SignIn'
import { session, SIGNED_OUT } from './api/session'
import { ToastHost } from './toast'
// Generated from the API's OpenAPI schema by `npm run gen:types`, and committed
// so a fresh clone typechecks without a backend running. Never hand-edited.
import type { components } from './api/schema'
import { applyTheme, readTheme, type Theme } from './theme'

type Health = components['schemas']['Health']

type Probe =
  | { state: 'checking' }
  | { state: 'up'; health: Health }
  | { state: 'down'; detail: string }

const SCREENS = [
  { hash: '#/', name: 'Chat', icon: 'chat' },
  { hash: '#/imports', name: 'Data & imports', icon: 'data' },
  { hash: '#/entry', name: 'Manual entry', icon: 'entry' },
  { hash: '#/audit', name: 'Egress audit', icon: 'log' },
  { hash: '#/log', name: 'Model log', icon: 'log' },
  { hash: '#/settings', name: 'Settings', icon: 'settings' },
] as const satisfies readonly NavItem[]

type Screen = (typeof SCREENS)[number]['hash']

const PANELS_KEY = 'hearth.panels'
type Panels = { nav: boolean; threads: boolean }
const OPEN: Panels = { nav: true, threads: true }

function healthOf(probe: Probe): SidebarHealth {
  switch (probe.state) {
    case 'up':
      return { tone: 'up', text: 'Backend connected', version: probe.health.version }
    case 'checking':
      return { tone: 'down', text: 'Checking backend' }
    case 'down':
      return { tone: 'down', text: 'Backend unreachable', detail: probe.detail }
  }
}

function readPanels(): Panels {
  try {
    const stored: unknown = JSON.parse(window.localStorage.getItem(PANELS_KEY) ?? 'null')
    if (stored && typeof stored === 'object') return { ...OPEN, ...(stored as Partial<Panels>) }
  } catch {
    // Blocked, private, or not JSON: fall through to the default.
  }
  return OPEN
}

function current(): Screen {
  const path = window.location.hash.split('?')[0]
  return SCREENS.find((s) => s.hash === path)?.hash ?? '#/'
}

type Gate = 'checking' | 'open' | 'signed-out'

export function App() {
  const [gate, setGate] = useState<Gate>('checking')

  useEffect(() => {
    let live = true
    session
      .read()
      .then((s) => live && setGate(s.required && !s.signed_in ? 'signed-out' : 'open'))
      .catch(() => live && setGate('open'))
    const signOut = () => setGate('signed-out')
    window.addEventListener(SIGNED_OUT, signOut)
    return () => {
      live = false
      window.removeEventListener(SIGNED_OUT, signOut)
    }
  }, [])

  if (gate === 'checking') return null
  if (gate === 'signed-out') {
    return (
      <>
        <ToastHost />
        <SignIn onSignedIn={() => setGate('open')} />
      </>
    )
  }
  return <Shell />
}

function Shell() {
  const [probe, setProbe] = useState<Probe>({ state: 'checking' })
  const [screen, setScreen] = useState<Screen>(current)
  const [panels, setPanels] = useState<Panels>(readPanels)
  const [theme, setTheme] = useState<Theme>(readTheme)

  useEffect(() => {
    try {
      window.localStorage.setItem(PANELS_KEY, JSON.stringify(panels))
    } catch {
      // Not remembered this time; the panel still works.
    }
  }, [panels])

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
    <>
      <ToastHost />
      <div className="shell">
        <Sidebar
          items={SCREENS}
          current={screen}
          open={panels.nav}
          onToggle={() => setPanels((p) => ({ ...p, nav: !p.nav }))}
          health={healthOf(probe)}
          theme={theme}
          onTheme={(next) => {
            setTheme(next)
            applyTheme(next)
          }}
        />

        <main>
          <div className="view" hidden={screen !== '#/'}>
            <Chat
              active={screen === '#/'}
              panelOpen={panels.threads}
              onTogglePanel={() => setPanels((p) => ({ ...p, threads: !p.threads }))}
            />
          </div>
          <div className="view" hidden={screen !== '#/imports'}>
            <Imports active={screen === '#/imports'} />
          </div>
          <div className="view" hidden={screen !== '#/entry'}>
            <ManualEntry active={screen === '#/entry'} />
          </div>
          <div className="view" hidden={screen !== '#/audit'}>
            <SearchAudit active={screen === '#/audit'} />
          </div>
          <div className="view" hidden={screen !== '#/log'}>
            <ModelLog active={screen === '#/log'} />
          </div>
          <div className="view" hidden={screen !== '#/settings'}>
            <Settings active={screen === '#/settings'} />
          </div>
        </main>
      </div>
    </>
  )
}
