/**
 * The Chat screen, with its thread panel.
 *
 * Every turn is stored as it streams (Phase 8), so a conversation survives a
 * reload and can be found again from the panel. The first event of every
 * stream says which thread the turn went into; a new thread's title arrives
 * last, after the answer.
 *
 * The model still answers each question on its own — earlier turns in a thread
 * are a record, not context it is given — and the screen says so, rather than
 * letting a follow-up look like it was understood as one.
 *
 * The Steward picks the specialist, so there is no picker — the turn is
 * labelled with who answered it once the routing event arrives, which is before
 * the answer starts streaming. Tool calls are shown rather than hidden: on a
 * model this size the interesting question about any figure is where it came
 * from.
 */
import { useEffect, useRef, useState } from 'react'

import { streamChat, type ChatEvent } from './api/chat'
import { reason } from './api/http'
import { threads, type StoredMessage } from './api/threads'
import { withNegativesInRed } from './money'
import { ThreadPanel } from './Threads'

type ToolCall = { name: string; args: Record<string, unknown> }

type Turn = {
  question: string
  routedTo?: string
  confidence?: number
  tools: ToolCall[]
  answer: string
  refusal?: string
  /** Figures the answer stated that no tool returned (rule 1, checked live). */
  ungrounded?: string[]
  error?: string
  /** Read back from storage with no answer: the turn failed when it ran. */
  unanswered?: boolean
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

/** The open thread lives in the URL hash, so a reload lands back in it. */
const THREAD_IN_HASH = /[?&]thread=([0-9a-f-]{36})/

function threadInHash(): string | null {
  return THREAD_IN_HASH.exec(window.location.hash)?.[1] ?? null
}

/** A stored thread, back into the turns the screen draws. */
function toTurns(messages: StoredMessage[]): Turn[] {
  const turns: Turn[] = []
  for (const message of messages) {
    if (message.role === 'user') {
      turns.push({ question: message.content, tools: [], answer: '', unanswered: true, streaming: false })
      continue
    }
    const turn = turns[turns.length - 1]
    if (message.role !== 'assistant' || !turn) continue
    turn.unanswered = false
    turn.routedTo = message.agent ?? undefined
    // A confidence is a probability, not money: a Number is fine here.
    turn.confidence = message.confidence == null ? undefined : Number(message.confidence)
    turn.tools = (message.tool_calls ?? []).map((c) => ({ name: c.name, args: c.args }))
    if (message.refused) turn.refusal = message.content
    else turn.answer = message.content
    turn.ungrounded = message.ungrounded ?? undefined
  }
  return turns
}

export function Chat({ panelOpen, onTogglePanel }: { panelOpen: boolean; onTogglePanel: () => void }) {
  const [threadId, setThreadId] = useState<string | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns])

  const relist = () => setVersion((n) => n + 1)

  // Reopen the thread named in the URL — once, on first render, and never
  // again: after that the URL follows the thread, not the other way round.
  useEffect(() => {
    const id = threadInHash()
    if (id) void open(id)
  }, [])

  // Keep the URL naming the open thread — but only while Chat is the screen
  // showing, or it would pull another tab's hash out from under it. replaceState
  // rather than assigning the hash: no hashchange, no history entry per turn.
  useEffect(() => {
    const hash = window.location.hash
    if (hash && hash !== '#/' && !hash.startsWith('#/?')) return
    window.history.replaceState(null, '', threadId ? `#/?thread=${threadId}` : '#/')
  }, [threadId])

  async function open(id: string) {
    if (busy || id === threadId) return
    try {
      const detail = await threads.get(id)
      setThreadId(id)
      setTurns(toTurns(detail.messages))
      setFailure(null)
    } catch (error) {
      setFailure(reason(error))
    }
  }

  function startNew() {
    if (busy) return
    setThreadId(null)
    setTurns([])
    setFailure(null)
  }

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
      for await (const event of streamChat({ message: question, thread_id: threadId })) {
        if (event.type === 'thread') {
          setThreadId(event.id)
          if (event.created) relist()
        } else if (event.type === 'title') {
          relist()
        } else {
          applyEvent(event, update)
        }
      }
    } catch (error) {
      update((t) => ({ ...t, error: reason(error) }))
    } finally {
      update((t) => ({ ...t, streaming: false }))
      setBusy(false)
      relist()
    }
  }

  return (
    <div className={panelOpen ? 'chat-layout' : 'chat-layout collapsed'}>
      <ThreadPanel
        open={panelOpen}
        onToggle={onTogglePanel}
        current={threadId}
        onOpen={(id) => void open(id)}
        onNew={startNew}
        onDeleted={(id) => id === threadId && startNew()}
        version={version}
      />

      <section className="chat">
        <div className="transcript">
          {failure && <p className="error">{failure}</p>}
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
              {turn.answer && <p className="answer">{withNegativesInRed(turn.answer)}</p>}
              {turn.ungrounded && turn.ungrounded.length > 0 && (
                <p className="ungrounded">
                  Not found in any tool result: {turn.ungrounded.join(', ')}. Treat{' '}
                  {turn.ungrounded.length === 1 ? 'it' : 'them'} as unverified — every figure
                  should come from a tool.
                </p>
              )}
              {turn.refusal && <p className="refusal">{turn.refusal}</p>}
              {turn.streaming && !turn.answer && !turn.refusal && <p className="muted">…</p>}
              {turn.unanswered && <p className="muted small">No answer was stored for this question.</p>}
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
        {turns.length > 0 && (
          <p className="muted small context-note">
            Each question is answered on its own — earlier turns here are not sent to the model.
          </p>
        )}
      </section>
    </div>
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
    case 'ungrounded':
      update((t) => ({ ...t, ungrounded: event.figures }))
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
    case 'thread':
    case 'title':
      // Handled by the component: they concern the thread, not the turn.
      break
  }
}
