/**
 * Money on screen: US currency, and a negative amount in red.
 *
 * Amounts arrive from the API as decimal strings — Postgres NUMERIC, serialised
 * without ever passing through a float — and they are formatted here as strings
 * too. `Intl.NumberFormat` would need a `Number` first, which is a binary float:
 * exact for any balance anyone here has, but "exact enough" is the phrase
 * CLAUDE.md's NUMERIC rule exists to keep away from money. Grouping digits and
 * padding cents is string work, and nothing below does arithmetic — with one
 * deliberate, narrow exception: when a displayed balance changes, `Money`
 * counts between the old and new figure instead of jumping. Every *intermediate*
 * frame of that count is a `Number`, rounded to cents for display only, and
 * thrown away; the frame that finally lands always comes back through
 * `formatUSD` on the exact string the API sent, never through the animation's
 * own arithmetic. Nothing this component tweens is ever the value shown as
 * settled, stored, or read back — see CLAUDE.md rule 1.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'

const DECIMAL = /^([+-])?(\d+)(?:\.(\d+))?$/

export function formatUSD(amount: string): { text: string; negative: boolean } {
  const match = DECIMAL.exec(amount.trim())
  // Not a decimal string: show it as it came rather than inventing a figure.
  if (!match) return { text: amount, negative: false }

  const [, sign, whole, fraction = ''] = match
  const grouped = whole.replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  // Cents always; any further places only when they carry something. A price
  // stored to six places reads $230.00, not $230.000000, and $72.1234 keeps
  // the digits it actually has — trimmed, never rounded.
  const cents = fraction.replace(/0+$/, '').padEnd(2, '0')
  // Negative zero is zero. A balance of nothing is not in the red.
  const negative = sign === '-' && /[1-9]/.test(whole + fraction)

  return { text: `${negative ? '-' : ''}$${grouped}.${cents}`, negative }
}

/** Read once per animation, not subscribed: this only needs to know at the
 *  moment a count starts whether motion is wanted, not to react live to the OS
 *  setting changing mid-session. */
function reducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

const COUNT_MS = 500

export function Money({ amount }: { amount: string }) {
  const previous = useRef<string | null>(null)
  const [shown, setShown] = useState(amount)
  const frame = useRef<number | undefined>(undefined)

  useEffect(() => {
    const from = previous.current
    previous.current = amount
    // First render, no real change, or motion declined: show it plainly —
    // an account list mounting is not "every balance rising from zero".
    if (from === null || from === amount || reducedMotion()) {
      setShown(amount)
      return
    }
    const start = Number(from)
    const end = Number(amount)
    if (!Number.isFinite(start) || !Number.isFinite(end)) {
      setShown(amount)
      return
    }
    const startedAt = performance.now()
    const tick = (now: number) => {
      const t = Math.min(1, (now - startedAt) / COUNT_MS)
      if (t >= 1) {
        setShown(amount) // the exact string the API sent, not the tween
        return
      }
      // Ease-out: quick at first, settling in — the same restraint as the
      // streaming caret and the Thinking shimmer, not a linear count.
      const eased = 1 - (1 - t) ** 3
      setShown((start + (end - start) * eased).toFixed(2))
      frame.current = requestAnimationFrame(tick)
    }
    frame.current = requestAnimationFrame(tick)
    return () => {
      if (frame.current !== undefined) cancelAnimationFrame(frame.current)
    }
  }, [amount])

  const { text, negative } = formatUSD(shown)
  return <span className={negative ? 'money negative' : 'money'}>{text}</span>
}

/**
 * Negative dollar amounts inside a model's answer, in red.
 *
 * Matches the form the tools write — `-$1,800.00` — and the Unicode minus a
 * model sometimes substitutes for the hyphen. A fall described in words ("down
 * $50.00") is a positive amount and stays uncoloured: colouring by meaning would
 * mean guessing at it, and a guess in red is worse than no colour.
 */
const NEGATIVE_AMOUNT = /[-\u2212]\$\d[\d,]*(?:\.\d+)?/g

export function withNegativesInRed(text: string): ReactNode[] {
  const parts: ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(NEGATIVE_AMOUNT)) {
    const start = match.index
    if (start > last) parts.push(text.slice(last, start))
    parts.push(
      <span key={start} className="negative">
        {match[0]}
      </span>,
    )
    last = start + match[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}
