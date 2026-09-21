/**
 * Light / dark / follow-the-system. The choice is one localStorage key, applied
 * as `data-theme` on <html>; with none set, the stylesheet's `color-scheme:
 * light dark` follows the OS. It is a convenience like the panel state: if
 * storage is blocked, the theme simply follows the system.
 *
 * index.html applies the stored choice before first paint (same key, same
 * attribute), so a reload does not flash the wrong theme.
 */
export type Theme = 'system' | 'light' | 'dark'

export const THEME_KEY = 'hearth.theme'

export function readTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(THEME_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    // Blocked or private: follow the system.
  }
  return 'system'
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement
  if (theme === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', theme)
  try {
    if (theme === 'system') window.localStorage.removeItem(THEME_KEY)
    else window.localStorage.setItem(THEME_KEY, theme)
  } catch {
    // Not remembered this time; it still applies now.
  }
}
