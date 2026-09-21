/**
 * The left navigation: a panel with labels, or a rail of icons, from the Design
 * canvas's sidebar. One component and one code path for both — the rail is the
 * same markup with the text hidden, so a link never has two versions to keep in
 * step.
 *
 * Open or collapsed is the reader's choice and lives in App's panel state (one
 * localStorage key, read once). On a phone the panel would take the screen, so
 * below 40rem it is always the rail and the toggle is not offered.
 *
 * An icon alone names nothing to a screen reader, so every link carries an
 * aria-label whether or not its text is showing; the rail adds a tooltip.
 */
import { useSyncExternalStore, type ReactNode } from 'react'

import type { Theme } from './theme'

export type IconName = 'chat' | 'data' | 'entry' | 'log' | 'settings'

export type NavItem = { hash: string; name: string; icon: IconName }

export type Health = { tone: 'up' | 'down'; text: string; version?: string; detail?: string }

const NARROW = '(max-width: 40rem)'

function useNarrow(): boolean {
  return useSyncExternalStore(
    (notify) => {
      const query = window.matchMedia(NARROW)
      query.addEventListener('change', notify)
      return () => query.removeEventListener('change', notify)
    },
    () => window.matchMedia(NARROW).matches,
  )
}

/** 24-unit stroke icons, drawn once and sized by CSS. */
function Icon({ children }: { children: ReactNode }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

const ICONS: Record<IconName | 'menu' | 'back' | 'sun' | 'moon' | 'system', ReactNode> = {
  chat: <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
  data: (
    <>
      <path d="M12 3C7.6 3 4 4.3 4 6s3.6 3 8 3 8-1.3 8-3-3.6-3-8-3z" />
      <path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6" />
    </>
  ),
  entry: (
    <>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
    </>
  ),
  log: (
    <>
      <path d="M4 17l6-6-6-6" />
      <path d="M12 19h8" />
    </>
  ),
  settings: <path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6" />,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  back: <path d="M15 6l-6 6 6 6" />,
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </>
  ),
  moon: <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />,
  system: (
    <>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8M12 16v4" />
    </>
  ),
}

const THEMES: { value: Theme; label: string }[] = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]

const THEME_ICON = { system: 'system', light: 'sun', dark: 'moon' } as const

export function Sidebar({
  items,
  current,
  open,
  onToggle,
  health,
  theme,
  onTheme,
}: {
  items: readonly NavItem[]
  /** The hash of the screen showing. */
  current: string
  open: boolean
  onToggle: () => void
  health: Health
  theme: Theme
  onTheme: (theme: Theme) => void
}) {
  const narrow = useNarrow()
  const expanded = open && !narrow
  const nextTheme = THEMES[(THEMES.findIndex((t) => t.value === theme) + 1) % THEMES.length]

  return (
    <aside className={expanded ? 'sidebar' : 'sidebar collapsed'} aria-label="Hearth">
      <div className="sidebar-head">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            H
          </span>
          {/* Screen-reader-only when collapsed, so the page keeps its h1. */}
          <div className="brand-text">
            <h1>Hearth</h1>
            <p>local{health.version ? ` · v${health.version}` : ''}</p>
          </div>
        </div>
        {!narrow && (
          <button
            type="button"
            className="nav-toggle"
            onClick={onToggle}
            aria-expanded={expanded}
            aria-controls="sidebar-nav"
            aria-label={expanded ? 'Collapse menu' : 'Expand menu'}
            title={expanded ? 'Collapse menu' : 'Expand menu'}
          >
            <Icon>{expanded ? ICONS.back : ICONS.menu}</Icon>
          </button>
        )}
      </div>

      <nav id="sidebar-nav" aria-label="Screens" className="sidebar-nav">
        {items.map((item) => (
          <a
            key={item.hash}
            href={item.hash}
            className="nav-item"
            aria-current={item.hash === current ? 'page' : undefined}
            aria-label={item.name}
            title={expanded ? undefined : item.name}
          >
            <Icon>{ICONS[item.icon]}</Icon>
            <span className="nav-label">{item.name}</span>
          </a>
        ))}
      </nav>

      <div className="sidebar-foot">
        <p className="status" title={health.detail ?? health.text}>
          <span className={`dot ${health.tone}`} />
          <span className={expanded ? 'nav-label' : 'sr-only'}>{health.text}</span>
        </p>

        {expanded ? (
          <>
            <div className="theme" role="group" aria-label="Colour theme">
              {THEMES.map(({ value, label }) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={theme === value}
                  onClick={() => onTheme(value)}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="note">Local-first. Nothing here leaves the machine.</p>
          </>
        ) : (
          <button
            type="button"
            className="nav-toggle"
            onClick={() => onTheme(nextTheme.value)}
            aria-label={`Theme: ${theme}. Switch to ${nextTheme.label.toLowerCase()}`}
            title={`Theme: ${theme}. Switch to ${nextTheme.label.toLowerCase()}`}
          >
            <Icon>{ICONS[THEME_ICON[theme]]}</Icon>
          </button>
        )}
      </div>
    </aside>
  )
}
