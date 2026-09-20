/**
 * Phase 5's chat: one thread, no history, no persistence.
 *
 * Tool calls are shown rather than hidden. On an 8B-class model the interesting
 * question about any figure is where it came from, and a line saying which tool
 * ran with which dates answers it without a tracing UI. The model log drawer of
 * the Design canvas is the grown-up version of this.
 */
import { useEffect, useRef, useState } from 'react'

import { streamChat, type ChatEvent } from './api/chat'

type ToolCall = { name: string; args: Record<string, unknown> }

type Turn = {
  question: string
  tools: ToolCall[]
  answer: string
  error?: string
  streaming: boolean
}

function ToolLine({ call }: { call: ToolCall }) {
  const args = Object.entries(call.args)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(' ')
  return (
    <div className="tool">
      <span className="tool-name">{call.name}</span>
      {args && <span className="tool-args">{args}</span>}
    </div>
  )
}

export function Chat() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns])

  async function send(event: React.FormEvent) {
    event.preventDefault()
    const question = draft.trim()
    if (!question || busy) return

    setDraft('')
    setBusy(true)
    const index = turns.length
    setTurns((previous) => [
      ...previous,
      { question, tools: [], answer: '', streaming: true },
    ])

    const update = (change: (turn: Turn) => Turn) =>
      setTurns((previous) => previous.map((t, i) => (i === index ? change(t) : t)))

    try {
      for await (const event of streamChat({ message: question })) {
        applyEvent(event, update)
      }
    } catch (error) {
      update((t) => ({ ...t, error: error instanceof Error ? error.message : String(error) }))
    } finally {
      update((t) => ({ ...t, streaming: false }))
      setBusy(false)
    }
  }

  return (
    <section className="chat">
      <div className="transcript">
        {turns.length === 0 && (
          <p className="muted empty">
            Ask about net worth, an account balance, or how your holdings are allocated.
          </p>
        )}

        {turns.map((turn, i) => (
          <article key={i} className="turn">
            <p className="question">{turn.question}</p>
            {turn.tools.map((call, j) => (
              <ToolLine key={j} call={call} />
            ))}
            {turn.answer && <p className="answer">{turn.answer}</p>}
            {turn.streaming && !turn.answer && <p className="muted">…</p>}
            {turn.error && <p className="error">{turn.error}</p>}
          </article>
        ))}
        <div ref={endRef} />
      </div>

      <form className="composer" onSubmit={send}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="How has my net worth moved this year?"
          aria-label="Ask Tally a question"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          {busy ? '…' : 'Ask'}
        </button>
      </form>
    </section>
  )
}

/** Kept out of the component so the event handling reads as one list. */
function applyEvent(event: ChatEvent, update: (change: (turn: Turn) => Turn) => void) {
  switch (event.type) {
    case 'token':
      // Text streamed during a step that turned out to be a tool call is not
      // the answer. The backend flags it; dropping it is the whole point.
      if (!event.provisional) update((t) => ({ ...t, answer: t.answer + event.text }))
      break
    case 'tool':
      update((t) => ({ ...t, tools: [...t.tools, { name: event.name, args: event.args }] }))
      break
    case 'tool_result':
      // Deliberately not rendered. The result is already reflected in the
      // answer, and showing both invites reading the raw figures instead.
      break
    case 'error':
      update((t) => ({ ...t, error: event.detail }))
      break
    case 'done':
      if (event.reason !== 'complete') update((t) => ({ ...t, error: event.reason }))
      break
  }
}
