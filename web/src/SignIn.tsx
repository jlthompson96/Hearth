/**
 * The sign-in screen, shown in place of everything else while the backend asks
 * for a password and this device has no session (api/auth.py). Nothing behind
 * it is mounted, so no screen fetches anything until the gate opens.
 */
import { useState, type FormEvent } from 'react'

import { reason } from './api/http'
import { session } from './api/session'

export function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setProblem(null)
    try {
      await session.signIn(password)
      setPassword('')
      onSignedIn()
    } catch (error) {
      setProblem(reason(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="signin">
      <form className="screen card" onSubmit={(event) => void submit(event)}>
        <h2>Hearth</h2>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {problem && (
          <p className="error" role="alert">
            {problem}
          </p>
        )}
        <button type="submit" disabled={busy || password.length === 0}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  )
}
