/**
 * Signing in and out (api/auth.py). Off — `required: false` — unless the
 * backend has a password, which it must once it answers other machines.
 *
 * A request refused for want of a session announces SIGNED_OUT on `window`
 * (http.ts), so a session that expires mid-use, or is ended by a password
 * change, lands on the sign-in screen rather than on a screen of errors.
 */
import { call } from './http'
import type { components } from './schema'

export { SIGNED_OUT } from './http'

export type Session = components['schemas']['Session']

export const session = {
  read: () => call<Session>('GET', '/api/session'),
  signIn: (password: string) => call<void>('POST', '/api/login', { password }),
  signOut: () => call<void>('POST', '/api/logout'),
}
