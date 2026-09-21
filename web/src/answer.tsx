/**
 * An answer's text, as the model wrote it, made readable.
 *
 * The model writes plain text with occasional markdown bold — `**17.500 kg**` —
 * whatever the prompt says; a 4B model asked to avoid markdown mostly does and
 * sometimes does not. Rendering the one construct it actually uses is more
 * reliable than asking it to stop. Everything else stays as text: React escapes
 * it, so nothing the model writes can become markup.
 *
 * Negative amounts are coloured inside bold runs as well as outside them.
 */
import type { ReactNode } from 'react'

import { withNegativesInRed } from './money'

const BOLD = /\*\*([^*\n]+)\*\*/g

export function renderAnswer(text: string): ReactNode[] {
  const parts: ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(BOLD)) {
    const start = match.index
    if (start > last) parts.push(...withNegativesInRed(text.slice(last, start)))
    parts.push(<strong key={`b${start}`}>{withNegativesInRed(match[1])}</strong>)
    last = start + match[0].length
  }
  if (last < text.length) parts.push(...withNegativesInRed(text.slice(last)))
  return parts
}
