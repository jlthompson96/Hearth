/**
 * Toasts: a brief, dismissible confirmation for something that already
 * happened elsewhere on the page — a preference saved, a model switched, a
 * copy that failed. They are a courtesy, never the record: the action that
 * raised one keeps its own inline result too (Settings' status line, the
 * copy button's own checkmark), so missing a toast never costs information.
 *
 * The queue is held outside React — a module-level store read with
 * useSyncExternalStore, the same pattern Sidebar.tsx uses for
 * prefers-reduced-motion — so any file can raise one with a plain function
 * call, no provider to thread through five screens for something this small.
 * `ToastHost` is the one place that queue is drawn; App mounts it once.
 */
import { useSyncExternalStore } from 'react'

export type ToastKind = 'ok' | 'error'
export type ToastItem = { id: number; kind: ToastKind; text: string }

let items: ToastItem[] = []
let nextId = 0
const listeners = new Set<() => void>()

function emit(): void {
  for (const listener of listeners) listener()
}

export function showToast(text: string, kind: ToastKind = 'ok', ms = 4000): void {
  const id = ++nextId
  items = [...items, { id, kind, text }]
  emit()
  window.setTimeout(() => dismissToast(id), ms)
}

export function dismissToast(id: number): void {
  items = items.filter((item) => item.id !== id)
  emit()
}

function useToasts(): ToastItem[] {
  return useSyncExternalStore(
    (notify) => {
      listeners.add(notify)
      return () => listeners.delete(notify)
    },
    () => items,
  )
}

function CheckIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  )
}

function ErrorIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5v5M12 16h.01" />
    </svg>
  )
}

/** Mounted once, in App, fixed to the viewport rather than the content
 *  column — a confirmation from Settings should still be readable if you
 *  switch screens before it clears. */
export function ToastHost() {
  const toasts = useToasts()
  if (toasts.length === 0) return null

  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast ${toast.kind}`}>
          {toast.kind === 'ok' ? <CheckIcon /> : <ErrorIcon />}
          <p>{toast.text}</p>
          <button type="button" onClick={() => dismissToast(toast.id)} aria-label="Dismiss">
            ×
          </button>
        </div>
      ))}
    </div>
  )
}
