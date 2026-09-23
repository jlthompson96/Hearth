/**
 * An answer's text, as the model wrote it, made readable.
 *
 * The model writes plain text with occasional markdown bold — `**17.500 kg**` —
 * whatever the prompt says; a 4B model asked to avoid markdown mostly does and
 * sometimes does not. Rendering the one construct it actually uses is more
 * reliable than asking it to stop. Everything else stays as text: React escapes
 * it, so nothing the model writes can become markup.
 *
 * Negative amounts are coloured inside bold runs as well as outside them, and
 * so are figures the backend flagged as ungrounded (rule 1): the warning sits on
 * the number it is about, not only in a note beneath the answer.
 */
import { Fragment, useState, type ReactNode } from 'react'

import { withNegativesInRed } from './money'
import { canon, findSource, formatArgs, FIGURE, type ToolRun } from './provenance'
import { showToast } from './toast'

const BOLD = /\*\*([^*\n]+)\*\*/g

/** The answer's markdown bold markers, stripped. A reader copying an answer
 *  wants the prose, not the `**...**` a 4B model sometimes leaves in (see the
 *  module comment above) — the one place this file's text output differs from
 *  what renderAnswer puts on screen. */
export function plainText(text: string): string {
  return text.replace(BOLD, '$1')
}

/** Longest first, so "$1,234.50" is one match rather than "$1,234" and a tail. */
export function figurePattern(figures: string[]): RegExp | null {
  const parts = [...new Set(figures)]
    .filter((f) => f.length > 0)
    .sort((a, b) => b.length - a.length)
    .map((f) => f.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  return parts.length ? new RegExp(parts.join('|'), 'g') : null
}

/** What lets a figure be clicked: which have a source, and which is open. */
export type FigureLink = {
  has: (value: string) => boolean
  picked: string | null
  pick: (value: string) => void
}

/** Plain text with each traceable figure made a button; negatives stay red. */
function linked(text: string, link: FigureLink | undefined, key: string): ReactNode {
  if (!link) return <Fragment key={key}>{withNegativesInRed(text)}</Fragment>
  const parts: ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(FIGURE)) {
    const value = canon(match[1] ?? match[2])
    if (!link.has(value)) continue
    const start = match.index
    // The digit pattern takes a following comma ("$38,250, then"); it is
    // punctuation, not part of the figure, so it stays outside the button.
    const shown = match[0].replace(/,+$/, '')
    if (start > last) parts.push(<Fragment key={`g${start}`}>{withNegativesInRed(text.slice(last, start))}</Fragment>)
    parts.push(
      <button
        key={`f${start}`}
        type="button"
        className="figure"
        aria-expanded={link.picked === value}
        title="Where did this come from?"
        onClick={() => link.pick(value)}
      >
        {withNegativesInRed(shown)}
      </button>,
    )
    last = start + shown.length
  }
  if (last < text.length) parts.push(<Fragment key={`g${last}`}>{withNegativesInRed(text.slice(last))}</Fragment>)
  return <Fragment key={key}>{parts}</Fragment>
}

/** One run of plain text: flagged figures marked, traceable ones linked,
 *  negatives in red around them. */
function inline(
  text: string,
  pattern: RegExp | null,
  link: FigureLink | undefined,
  key: string,
): ReactNode {
  if (!pattern) return linked(text, link, key)
  const parts: ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(pattern)) {
    const start = match.index
    if (start > last) parts.push(linked(text.slice(last, start), link, `t${start}`))
    parts.push(
      <mark key={`m${start}`} className="unverified" title="No tool returned this figure">
        <span className="sr-only">Unverified: </span>
        {withNegativesInRed(match[0])}
      </mark>,
    )
    last = start + match[0].length
  }
  if (last < text.length) parts.push(linked(text.slice(last), link, `t${last}`))
  return <Fragment key={key}>{parts}</Fragment>
}

export function renderAnswer(
  text: string,
  ungrounded: string[] = [],
  link?: FigureLink,
): ReactNode[] {
  const pattern = figurePattern(ungrounded)
  const parts: ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(BOLD)) {
    const start = match.index
    if (start > last) parts.push(inline(text.slice(last, start), pattern, link, `p${start}`))
    parts.push(<strong key={`b${start}`}>{inline(match[1], pattern, link, 'in')}</strong>)
    last = start + match[0].length
  }
  if (last < text.length) parts.push(inline(text.slice(last), pattern, link, `p${last}`))
  return parts
}

/**
 * An answer with its figures traceable. A figure a tool returned is a button;
 * clicking it opens the line of the tool's result that states it, beneath the
 * answer. Only that line, on request: the whole result beside the answer would
 * invite reading it instead of the answer (see the Chat screen's comments).
 */
export function Answer({
  text,
  ungrounded,
  tools,
  streaming,
}: {
  text: string
  ungrounded?: string[]
  tools: ToolRun[]
  /** Tokens are still arriving: a caret marks the point they will resume, the
   *  same idea as the tool-fetched Thinking indicator above the answer, moved
   *  to where the text itself is growing. */
  streaming?: boolean
}) {
  const [picked, setPicked] = useState<string | null>(null)
  const link: FigureLink = {
    has: (value) => findSource(value, tools) !== null,
    picked,
    pick: (value) => setPicked((now) => (now === value ? null : value)),
  }
  const source = picked ? findSource(picked, tools) : null

  return (
    <>
      <p className={streaming ? 'answer streaming' : 'answer'}>{renderAnswer(text, ungrounded, link)}</p>
      {source && (
        <div className="source" role="region" aria-label="Where this figure came from">
          <p className="source-head">
            <span className="tool-name">{source.tool}</span>
            <span className="tool-args">{formatArgs(source.args)}</span>
            <button type="button" className="source-close" onClick={() => setPicked(null)}>
              Close
            </button>
          </p>
          {source.lines.map((line, i) => (
            <code key={i} className="source-line">
              {line}
            </code>
          ))}
        </div>
      )}
    </>
  )
}

function CopyIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  )
}

/**
 * Copies an answer's plain text (markdown stripped) to the clipboard. The
 * confirmation is a quick inline swap to a checkmark, not a toast — it
 * belongs right where the click happened. A toast is for the one way this can
 * still fail silently otherwise: the Clipboard API refused (no secure
 * context, no permission granted).
 */
export function CopyAnswerButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(plainText(text))
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      showToast('Could not copy — the browser blocked clipboard access.', 'error')
    }
  }

  return (
    <button
      type="button"
      className={copied ? 'copy-btn copied' : 'copy-btn'}
      onClick={() => void copy()}
      aria-label={copied ? 'Copied' : 'Copy answer'}
      title={copied ? 'Copied' : 'Copy answer'}
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}
