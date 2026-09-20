/**
 * The Chat screen. One thread, no history, no persistence.
 *
 * The Steward picks the specialist, so there is no picker any more — the turn
 * is labelled with who answered it once the routing event arrives, which is
 * before the answer starts streaming. Attribution after the fact would be a
 * caption; attribution first is the UI telling you where your question went.
 *
 * Tool calls are shown rather than hidden. On a model this size the interesting
 * question about any figure is where it came from, and a line saying which tool
 * ran with which arguments answers it without a tracing UI.
 */
import { useEffect, useRef, useState } from 'react'

import { streamChat, type ChatEvent } from './api/chat'

type ToolCall = { name: string; args: Record<string, unknown> }

type Turn = {
  question: string
  routedTo?: string
  confidence?: number
  tools: ToolCall[]
  answer: string
  refusal?: string
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

/** Who answered, and how sure the router was. A low number is worth seeing:
 *  it is the difference between a decision and a coin flip. */
function Attribution({ to, confidence }: { to: string; confidence?: number }) {
  const unsure = confidence !== undefined && confidence < 0.6
  return (
    <p className="attribution">
      <span className={`who ${to}`}>{to}</span>
      {unsure && <span className="unsure">low confidence</span>}
    </p>
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
    setTurns((previous) => [...previous, { question, tools: [], answer: '', streaming: true }])

    const update = (change: (turn: Turn) => Turn) =>
      setTurns((previous) => previous.map((t, i) => (i === index ? change(t) : t)))

    try {
      // No agent named: the Steward decides.
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
            Ask about your accounts and net worth, or about your lifts and body
            measurements. You do not have to say which — the Steward works it out.
          </p>
        )}

        {turns.map((turn, i) => (
          <article key={i} className="turn">
            <p className="question">{turn.question}</p>
            {turn.routedTo && <Attribution to={turn.routedTo} confidence={turn.confidence} />}
            {turn.tools.map((call, j) => (
              <ToolLine key={j} call={call} />
            ))}
            {turn.answer && <p className="answer">{turn.answer}</p>}
            {turn.refusal && <p className="refusal">{turn.refusal}</p>}
            {turn.streaming && !turn.answer && !turn.refusal && <p className="muted">…</p>}
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
          aria-label="Ask a question"
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
    case 'routed':
      update((t) => ({ ...t, routedTo: event.destination, confidence: event.confidence }))
      break
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
    case 'refused':
      // Styled apart from an answer and apart from an error. It is neither: the
      // turn stopped deliberately, before the model was called.
      update((t) => ({ ...t, refusal: event.message }))
      break
    case 'error':
      update((t) => ({ ...t, error: event.detail }))
      break
    case 'done':
      if (event.reason !== 'complete' && event.reason !== 'unsupported') {
        update((t) => ({ ...t, error: event.reason }))
      }
      break
  }
}
