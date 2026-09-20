/**
 * Phase 5's chat, with Phase 11's second specialist beside it. One thread, no
 * history, no persistence.
 *
 * The specialist is picked by hand. Phase 6 puts the Steward in front of this
 * and chooses for you; until it exists, a control that says which agent you are
 * talking to is honest where a silent guess would not be.
 *
 * Tool calls are shown rather than hidden. On a model this size the interesting
 * question about any figure is where it came from, and a line saying which tool
 * ran with which arguments answers it without a tracing UI.
 */
import { useEffect, useRef, useState } from 'react'

import { streamChat, type ChatEvent, type ChatRequest } from './api/chat'

type Agent = NonNullable<ChatRequest['agent']>

const AGENTS: { id: Agent; label: string; placeholder: string }[] = [
  { id: 'tally', label: 'Tally', placeholder: 'How has my net worth moved this year?' },
  { id: 'forge', label: 'Forge', placeholder: 'How has my back squat progressed?' },
]

type ToolCall = { name: string; args: Record<string, unknown> }

type Turn = {
  agent: Agent
  question: string
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

export function Chat() {
  const [agent, setAgent] = useState<Agent>('tally')
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns])

  const active = AGENTS.find((a) => a.id === agent) ?? AGENTS[0]

  async function send(event: React.FormEvent) {
    event.preventDefault()
    const question = draft.trim()
    if (!question || busy) return

    setDraft('')
    setBusy(true)
    const index = turns.length
    setTurns((previous) => [
      ...previous,
      { agent, question, tools: [], answer: '', streaming: true },
    ])

    const update = (change: (turn: Turn) => Turn) =>
      setTurns((previous) => previous.map((t, i) => (i === index ? change(t) : t)))

    try {
      for await (const event of streamChat({ message: question, agent })) {
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
      <div className="agents" role="group" aria-label="Specialist">
        {AGENTS.map((option) => (
          <button
            key={option.id}
            type="button"
            className={option.id === agent ? 'agent selected' : 'agent'}
            onClick={() => setAgent(option.id)}
            disabled={busy}
            aria-pressed={option.id === agent}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="transcript">
        {turns.length === 0 && (
          <p className="muted empty">
            {agent === 'tally'
              ? 'Ask about net worth, an account balance, or how your holdings are allocated.'
              : 'Ask about a lift’s progression or a body measurement you have logged.'}
          </p>
        )}

        {turns.map((turn, i) => (
          <article key={i} className="turn">
            <p className="question">
              <span className="who">{turn.agent}</span>
              {turn.question}
            </p>
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
          placeholder={active.placeholder}
          aria-label={`Ask ${active.label} a question`}
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
    case 'refused':
      // Styled apart from an answer and apart from an error. It is neither: the
      // turn stopped deliberately, before the model was called.
      update((t) => ({ ...t, refusal: event.message }))
      break
    case 'error':
      update((t) => ({ ...t, error: event.detail }))
      break
    case 'done':
      if (event.reason !== 'complete') update((t) => ({ ...t, error: event.reason }))
      break
  }
}
