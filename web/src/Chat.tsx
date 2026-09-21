/**
 * The Chat screen, with its thread panel.
 *
 * Every turn is stored as it streams (Phase 8), so a conversation survives a
 * reload and can be found again from the panel. The first event of every
 * stream says which thread the turn went into; a new thread's title arrives
 * last, after the answer.
 *
 * A follow-up is understood as one: the backend hands the specialist its own
 * last few answers in the thread, so "yes" can accept what an answer offered.
 * Asking an earlier question again (Less · Normal · More) names that question,
 * so it is answered with the context it first had, not with what came after.
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
import { settings } from './api/settings'
import { Answer } from './answer'
import { formatArgs, type ToolRun } from './provenance'
import { ThreadPanel } from './Threads'

type ToolCall = ToolRun

/** How much a specialist says. "Less · Normal · More" under an answer re-asks
 *  the same question at another level, of the same specialist. */
type Detail = 'brief' | 'normal' | 'detailed'
const LEVELS: { detail: Detail; label: string }[] = [
  { detail: 'brief', label: 'Less' },
  { detail: 'normal', label: 'Normal' },
  { detail: 'detailed', label: 'More' },
]
const SPECIALISTS = new Set(['tally', 'forge'])

type Turn = {
  /** The stored question's id, once the stream has said it; absent only if
   *  the question was never stored. */
  id?: string
  question: string
  routedTo?: string
  confidence?: number
  tools: ToolCall[]
  answer: string
  refusal?: string
  /** Figures the answer stated that no tool returned (rule 1, checked live). */
  ungrounded?: string[]
  error?: string
  /** The reader stopped it. What streamed is kept, but it was never checked
   *  against the tools (rule 1) and the backend never stored it. */
  stopped?: boolean
  /** Read back from storage with no answer: the turn failed when it ran. */
  unanswered?: boolean
  /** The level the answer was asked for; absent on answers from before the
   *  control existed, which read as normal. */
  detail?: Detail
  streaming: boolean
}

/** The control under an answer. The level it was given at is marked; the other
 *  two re-ask. Only a specialist's answer has one: a refusal or a decline is
 *  not a matter of length. */
function DetailControl({
  current,
  disabled,
  onPick,
}: {
  current: Detail
  disabled: boolean
  onPick: (detail: Detail) => void
}) {
  return (
    <div className="detail" role="group" aria-label="Ask again with more or less detail">
      {LEVELS.map(({ detail, label }) => (
        <button
          key={detail}
          type="button"
          aria-pressed={detail === current}
          disabled={disabled || detail === current}
          onClick={() => onPick(detail)}
          title={detail === current ? 'This answer' : `Ask again: ${label.toLowerCase()} detail`}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

/** Not colour alone: the shape says "warning" to anyone who cannot see amber. */
function WarnIcon() {
  return (
    <svg className="warn-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M8 1.5 15 14H1L8 1.5Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path d="M8 6.25v3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="8" cy="11.75" r="0.9" fill="currentColor" />
    </svg>
  )
}

/** What to try first, one per specialist's ground. Each is a question the
 *  Steward routes without help and a tool can answer from recorded data. */
const EXAMPLES: { agent: 'tally' | 'forge'; question: string }[] = [
  { agent: 'tally', question: 'How has my net worth moved this year?' },
  { agent: 'tally', question: 'What are my current positions?' },
  { agent: 'forge', question: 'How has my body weight changed?' },
  { agent: 'forge', question: 'How has my squat progressed?' },
]

function ToolLine({ call }: { call: ToolCall }) {
  const args = formatArgs(call.args)
  return (
    <div className="tool">
      <span className="tool-name">{call.name}</span>
      {args && <span className="tool-args">{args}</span>}
    </div>
  )
}

/** Who answered, and how sure the router was. A low number is worth seeing:
 *  it is the difference between a decision and a coin flip. */
function Attribution({
  to,
  confidence,
  detail,
  run,
}: {
  to: string
  confidence?: number
  detail?: Detail
  /** The question's id: its run in the Model log, once the turn is over. */
  run?: string
}) {
  const unsure = confidence !== undefined && confidence < 0.6
  return (
    <p className="attribution">
      <span className={`who ${to}`}>{to}</span>
      {unsure && <span className="unsure">low confidence</span>}
      {detail && detail !== 'normal' && (
        <span className="level">{detail === 'brief' ? 'less detail' : 'more detail'}</span>
      )}
      {run && (
        <a className="log-link" href={`#/log?run=${run}`} title="Everything the model was sent and said">
          log
        </a>
      )}
    </p>
  )
}

/** New questions start at the length chosen in Settings, read when asked so a
 *  change there is in effect on the next question. Normal if it can't be read. */
async function defaultDetail(): Promise<Detail> {
  try {
    const chosen = (await settings.read()).preferences.find((p) => p.key === 'default_detail')
    const value = String(chosen?.value ?? 'normal')
    return LEVELS.some((l) => l.detail === value) ? (value as Detail) : 'normal'
  } catch {
    return 'normal'
  }
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
      turns.push({
        id: message.id,
        question: message.content,
        tools: [],
        answer: '',
        unanswered: true,
        streaming: false,
      })
      continue
    }
    const turn = turns[turns.length - 1]
    if (message.role !== 'assistant' || !turn) continue
    turn.unanswered = false
    turn.routedTo = message.agent ?? undefined
    // A confidence is a probability, not money: a Number is fine here.
    turn.confidence = message.confidence == null ? undefined : Number(message.confidence)
    turn.tools = (message.tool_calls ?? []).map((c) => ({
      name: c.name,
      args: c.args,
      result: c.result ?? undefined,
    }))
    if (message.refused) turn.refusal = message.content
    else turn.answer = message.content
    turn.ungrounded = message.ungrounded ?? undefined
    turn.detail = (message.detail as Detail | null) ?? undefined
  }
  return turns
}

export function Chat({
  active,
  panelOpen,
  onTogglePanel,
}: {
  active: boolean
  panelOpen: boolean
  onTogglePanel: () => void
}) {
  const [threadId, setThreadId] = useState<string | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const endRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  /** The running turn's request, so Stop can end it. */
  const abortRef = useRef<AbortController | null>(null)
  /** Where Up/Down have walked back to through earlier questions; -1 is none. */
  const recallAt = useRef(-1)
  const wasBusy = useRef(false)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns])

  const relist = () => setVersion((n) => n + 1)

  const stop = () => abortRef.current?.abort()

  // Esc stops a turn; "/" jumps to the composer from anywhere that is not
  // already a text field. Only while Chat is the screen showing.
  useEffect(() => {
    if (!active) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && busy) {
        abortRef.current?.abort()
      } else if (event.key === '/' && !event.metaKey && !event.ctrlKey && !event.altKey && !busy) {
        const el = document.activeElement
        const typing =
          el instanceof HTMLElement &&
          (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))
        if (!typing) {
          event.preventDefault()
          inputRef.current?.focus()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, busy])

  // The composer is disabled while a turn runs and loses focus with it: give it
  // back when the turn ends, so the next question needs no click.
  useEffect(() => {
    if (wasBusy.current && !busy && active) inputRef.current?.focus()
    wasBusy.current = busy
  }, [busy, active])

  /** Up and Down walk back through what was asked in this thread, from an empty
   *  composer only — never over a half-typed question. */
  function recall(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
    if (draft !== '' && recallAt.current < 0) return
    const asked = [...new Set(turns.map((t) => t.question).reverse())]
    if (asked.length === 0) return
    event.preventDefault()
    const next =
      event.key === 'ArrowUp' ? Math.min(recallAt.current + 1, asked.length - 1) : recallAt.current - 1
    recallAt.current = next
    setDraft(next < 0 ? '' : asked[next])
  }

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

  async function submit(text: string) {
    const question = text.trim()
    if (!question || busy) return
    setDraft('')
    recallAt.current = -1
    // No agent named: the Steward decides.
    await ask(question, await defaultDetail())
  }

  function send(event: React.FormEvent) {
    event.preventDefault()
    void submit(draft)
  }

  /** The same question again at another level, of the specialist that answered
   *  it — named, so it cannot be routed somewhere else the second time — and
   *  with the context it had when it was first asked. */
  async function rerun(turn: Turn, detail: Detail) {
    if (busy || !turn.id || !turn.routedTo || !SPECIALISTS.has(turn.routedTo)) return
    await ask(turn.question, detail, turn.routedTo as 'tally' | 'forge', turn.id)
  }

  async function ask(
    question: string,
    detail: Detail,
    agent?: 'tally' | 'forge',
    rerunOf?: string,
  ) {
    setBusy(true)
    const controller = new AbortController()
    abortRef.current = controller
    const index = turns.length
    setTurns((previous) => [
      ...previous,
      { question, tools: [], answer: '', streaming: true, detail, routedTo: agent },
    ])

    const update = (change: (turn: Turn) => Turn) =>
      setTurns((previous) => previous.map((t, i) => (i === index ? change(t) : t)))

    try {
      const request = { message: question, thread_id: threadId, agent, detail, rerun_of: rerunOf }
      for await (const event of streamChat(request, controller.signal)) {
        if (event.type === 'thread') {
          setThreadId(event.id)
          update((t) => ({ ...t, id: event.question_id }))
          if (event.created) relist()
        } else if (event.type === 'title') {
          relist()
        } else {
          applyEvent(event, update)
        }
      }
    } catch (error) {
      // Stopping rejects the read; that is the reader's choice, not a failure.
      if (controller.signal.aborted) update((t) => ({ ...t, stopped: true }))
      else update((t) => ({ ...t, error: reason(error) }))
    } finally {
      abortRef.current = null
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
            <div className="empty">
              <p className="muted">
                Ask about your accounts and net worth, or about your lifts and body
                measurements. You do not have to say which — the Steward works it out.
              </p>
              <ul className="examples" aria-label="Example questions">
                {EXAMPLES.map(({ agent, question }) => (
                  <li key={question}>
                    <button type="button" disabled={busy} onClick={() => void submit(question)}>
                      <span className={`who ${agent}`}>{agent}</span>
                      {question}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {turns.map((turn, i) => (
            <article key={i} className="turn">
              <p className="question">{turn.question}</p>
              {turn.routedTo && (
                <Attribution
                  to={turn.routedTo}
                  confidence={turn.confidence}
                  detail={turn.detail}
                  run={turn.streaming ? undefined : turn.id}
                />
              )}
              {turn.tools.map((call, j) => (
                <ToolLine key={j} call={call} />
              ))}
              {turn.answer && <Answer text={turn.answer} ungrounded={turn.ungrounded} tools={turn.tools} />}
              {turn.ungrounded && turn.ungrounded.length > 0 && (
                <div className="ungrounded" role="status">
                  <WarnIcon />
                  <p>
                    <strong>Unverified {turn.ungrounded.length === 1 ? 'figure' : 'figures'}:</strong>{' '}
                    {turn.ungrounded.join(', ')} {turn.ungrounded.length === 1 ? 'was' : 'were'} not
                    returned by any tool. Check {turn.ungrounded.length === 1 ? 'it' : 'them'} against
                    your data before relying on {turn.ungrounded.length === 1 ? 'it' : 'them'}.
                  </p>
                </div>
              )}
              {turn.refusal && <p className="refusal">{turn.refusal}</p>}
              {turn.answer &&
                !turn.streaming &&
                !turn.stopped &&
                turn.routedTo &&
                SPECIALISTS.has(turn.routedTo) && (
                <DetailControl
                  current={turn.detail ?? 'normal'}
                  disabled={busy}
                  onPick={(detail) => void rerun(turn, detail)}
                />
              )}
              {turn.stopped && (
                <div className="stopped" role="status">
                  <p>
                    Stopped.{' '}
                    {turn.answer
                      ? 'What is shown is incomplete, its figures were not checked against the tools, and it was not saved.'
                      : 'No answer was produced or saved.'}
                  </p>
                  {turn.id && turn.routedTo && SPECIALISTS.has(turn.routedTo) && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void rerun(turn, turn.detail ?? 'normal')}
                    >
                      Ask again
                    </button>
                  )}
                </div>
              )}
              {turn.streaming && !turn.answer && !turn.refusal && <p className="muted">…</p>}
              {turn.unanswered && <p className="muted small">No answer was stored for this question.</p>}
              {turn.error && <p className="error">{turn.error}</p>}
            </article>
          ))}
          <div ref={endRef} />
        </div>

        <form className="composer" onSubmit={send}>
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => {
              recallAt.current = -1
              setDraft(e.target.value)
            }}
            onKeyDown={recall}
            placeholder="How has my net worth moved this year?"
            aria-label="Ask a question"
            disabled={busy}
          />
          {busy ? (
            <button key="stop" type="button" className="stop" onClick={stop} title="Stop (Esc)">
              Stop
            </button>
          ) : (
            <button key="ask" type="submit" disabled={!draft.trim()}>
              Ask
            </button>
          )}
        </form>
        <p className="muted small hint">
          <kbd>Enter</kbd> ask · <kbd>Esc</kbd> stop · <kbd>/</kbd> focus · <kbd>↑</kbd> earlier
          question
        </p>
        {turns.length > 0 && (
          <p className="muted small context-note">
            Follow-ups work: whoever answers sees their own last few answers in this thread.
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
      // Kept with its call but never laid out: showing the raw result beside the
      // answer invites reading it instead. A clicked figure shows the one line of
      // it that states that figure (provenance.ts).
      update((t) => {
        const tools = [...t.tools]
        for (let i = tools.length - 1; i >= 0; i--) {
          if (tools[i].name === event.name && tools[i].result === undefined) {
            tools[i] = { ...tools[i], result: event.result }
            break
          }
        }
        return { ...t, tools }
      })
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
