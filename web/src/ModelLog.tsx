/**
 * The Model log: every exchange with the model, verbatim — for answering "why
 * did it say that".
 *
 * Runs on the left, most recent first; one run on the right as a timeline of
 * what happened in order — the routing call, each of the specialist's model
 * calls and tool runs, the title — with each entry's bar placed where it ran.
 * An entry opens to the exact request LM Studio was sent and what came back.
 *
 * The open run is in the URL hash (#/log?run=<question id>), so the chat can
 * link straight to the run behind an answer. The model's reasoning text is not
 * here, and the screen says so where it would be: LM Studio returns it in a
 * field LangChain's OpenAI client drops, so only its token count survives.
 */
import { useCallback, useEffect, useState } from 'react'

import { reason } from './api/http'
import { modelLog, type LogEntry, type RunDetail, type RunSummary } from './api/modelLog'
import { renderAnswer } from './answer'

const RUN_IN_HASH = /^#\/log\?run=([0-9a-f-]{36})/

function runInHash(): string | null {
  return RUN_IN_HASH.exec(window.location.hash)?.[1] ?? null
}

function duration(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`
}

function count(n: number | null | undefined): string {
  return n == null ? '—' : n.toLocaleString('en-US')
}

function when(iso: string): string {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

/** "steward", "tally", "allocation", ... — what ran, in order. */
function stepName(step: { kind: string; caller: string; tool?: string | null }): string {
  if (step.kind === 'tool') return step.tool ?? 'tool'
  if (step.kind === 'title') return 'title'
  return step.caller
}

function entryName(entry: LogEntry): string {
  if (entry.kind === 'tool') return `tool · ${String(entry.request.name ?? '?')}`
  if (entry.kind === 'route') return 'steward · route'
  if (entry.kind === 'title') return 'titles · title'
  return `${entry.caller} · model call`
}

type Message = { role?: string; content?: unknown; tool_calls?: unknown }

/** Where a request's characters went. Characters, not tokens: the tokenizer
 *  is LM Studio's, and only the total comes back from it. */
function composition(entry: LogEntry): { part: string; chars: number }[] | null {
  const messages = entry.request.messages as Message[] | undefined
  if (!Array.isArray(messages) || entry.kind === 'tool') return null
  const size = (m: Message) =>
    (typeof m.content === 'string' ? m.content : JSON.stringify(m.content ?? '')).length +
    (m.tool_calls ? JSON.stringify(m.tool_calls).length : 0)
  const question = messages.map((m) => m.role).lastIndexOf('user')
  const parts = new Map<string, number>()
  messages.forEach((m, i) => {
    const part =
      m.role === 'system'
        ? 'system prompt'
        : m.role === 'tool'
          ? 'tool results'
          : i === question
            ? 'question'
            : i < question
              ? 'earlier turns'
              : 'tool calls'
    parts.set(part, (parts.get(part) ?? 0) + size(m))
  })
  const tools = entry.request.tools ?? entry.request.response_format
  if (tools) parts.set(entry.request.tools ? 'tool schemas' : 'answer schema', JSON.stringify(tools).length)
  return [...parts].map(([part, chars]) => ({ part, chars }))
}

function reasoningTokens(entry: LogEntry): number | null {
  const usage = entry.response?.usage as { output_token_details?: { reasoning?: number } } | undefined
  return usage?.output_token_details?.reasoning ?? null
}

function Entry({ entry, run }: { entry: LogEntry; run: RunSummary }) {
  const [open, setOpen] = useState(false)
  const [pane, setPane] = useState<'request' | 'response'>('request')
  const [copied, setCopied] = useState(false)

  const start = new Date(run.started_at).getTime()
  const offset = new Date(entry.started_at).getTime() - start
  const total = Math.max(run.duration_ms, 1)
  const parts = composition(entry)
  const reasoning = reasoningTokens(entry)
  const shown = pane === 'request' ? entry.request : entry.response

  async function copy() {
    try {
      await navigator.clipboard.writeText(JSON.stringify(entry, null, 2))
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // No clipboard in this context; the JSON is on screen to select by hand.
    }
  }

  return (
    <li className={`entry ${entry.kind}${entry.error ? ' failed' : ''}`}>
      <button type="button" className="entry-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="entry-name">{entryName(entry)}</span>
        <span className="entry-meta muted small">
          {entry.model ?? 'Python'}
          {entry.input_tokens != null && ` · ${count(entry.input_tokens)} in · ${count(entry.output_tokens)} out`}
        </span>
        <span className="entry-time">{duration(entry.duration_ms)}</span>
        <span className="bar" aria-hidden="true">
          <span
            style={{
              marginLeft: `${(Math.max(offset, 0) / total) * 100}%`,
              width: `${Math.max((entry.duration_ms / total) * 100, 0.8)}%`,
            }}
          />
        </span>
      </button>
      {entry.error && <p className="error small">{entry.error}</p>}
      {open && (
        <div className="entry-body">
          {parts && (
            <p className="muted small composition">
              Sent: {parts.map((p) => `${p.part} ${count(p.chars)}`).join(' · ')} characters
              {entry.input_tokens != null && ` — ${count(entry.input_tokens)} tokens as LM Studio counted them`}
            </p>
          )}
          {reasoning != null && (
            <p className="muted small">
              {count(reasoning)} of its output tokens were reasoning. The reasoning itself is not
              kept: LM Studio returns it in a field LangChain drops.
            </p>
          )}
          <div className="panes">
            <div className="detail" role="group" aria-label="Request or response">
              <button type="button" aria-pressed={pane === 'request'} onClick={() => setPane('request')}>
                {entry.kind === 'tool' ? 'Arguments' : 'Request'}
              </button>
              <button type="button" aria-pressed={pane === 'response'} onClick={() => setPane('response')}>
                {entry.kind === 'tool' ? 'Result' : 'Response'}
              </button>
            </div>
            <button type="button" className="quiet" onClick={() => void copy()}>
              {copied ? 'Copied' : 'Copy JSON'}
            </button>
          </div>
          <pre className="json">{shown == null ? '(nothing came back)' : JSON.stringify(shown, null, 2)}</pre>
        </div>
      )}
    </li>
  )
}

function Chain({ run }: { run: RunSummary }) {
  return (
    <span className="chain">
      {run.chain.map((step, i) => (
        <span key={i} className={step.failed ? 'failed' : undefined}>
          {i > 0 && ' → '}
          {stepName(step)}
        </span>
      ))}
    </span>
  )
}

export function ModelLog({ active }: { active: boolean }) {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [retention, setRetention] = useState<number | null>(null)
  const [more, setMore] = useState(false)
  const [open, setOpen] = useState<RunDetail | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const listing = await modelLog.runs()
      setRuns(listing.runs)
      setRetention(listing.retention_days)
      setMore(listing.runs.length === 50)
      setFailure(null)
    } catch (error) {
      setFailure(reason(error))
    }
  }, [])

  const show = useCallback(async (questionId: string) => {
    try {
      setOpen(await modelLog.run(questionId))
      setFailure(null)
      window.history.replaceState(null, '', `#/log?run=${questionId}`)
    } catch (error) {
      setOpen(null)
      setFailure(reason(error))
    }
  }, [])

  // Re-read on every visit: every question asked since adds a run.
  useEffect(() => {
    if (!active) return
    void refresh()
    const id = runInHash()
    if (id) void show(id)
  }, [active, refresh, show])

  async function older() {
    const last = runs[runs.length - 1]
    if (!last) return
    const page = await modelLog.runs(last.started_at)
    setRuns([...runs, ...page.runs])
    setMore(page.runs.length === 50)
  }

  return (
    <section className="log-screen">
      <div className="log-intro">
        <h2>Model log</h2>
        <p className="muted">
          Every exchange with the model and every tool run, exactly as sent and received.
          {retention != null && ` Kept for ${retention} days, and deleted with its thread.`}
        </p>
      </div>
      {failure && <p className="error">{failure}</p>}

      <div className="log-layout">
        <ol className="runs" aria-label="Runs, most recent first">
          {runs.length === 0 && <li className="muted small">No runs yet. Ask a question in Chat.</li>}
          {runs.map((run) => (
            <li key={run.question_id}>
              <button
                type="button"
                className="run"
                aria-current={open?.summary.question_id === run.question_id ? 'true' : undefined}
                onClick={() => void show(run.question_id)}
              >
                <span className="run-question">{run.question}</span>
                <span className="muted small">
                  {when(run.started_at)} · {duration(run.duration_ms)}
                  {run.failed && <span className="run-failed"> · failed</span>}
                </span>
                <span className="muted small">
                  <Chain run={run} />
                </span>
              </button>
            </li>
          ))}
          {more && (
            <li>
              <button type="button" className="quiet" onClick={() => void older()}>
                Older runs
              </button>
            </li>
          )}
        </ol>

        <div className="run-detail">
          {!open && <p className="muted">Choose a run to see what the model was sent and what it said.</p>}
          {open && (
            <>
              <p className="run-title">{open.summary.question}</p>
              <p className="muted small">
                {open.summary.thread_title ?? 'Untitled thread'} · {when(open.summary.started_at)}
              </p>
              <dl className="totals">
                <div>
                  <dt>Chain</dt>
                  <dd>
                    <Chain run={open.summary} />
                  </dd>
                </div>
                <div>
                  <dt>Latency</dt>
                  <dd>{duration(open.summary.duration_ms)}</dd>
                </div>
                <div>
                  <dt>Tokens</dt>
                  <dd>
                    {count(open.summary.input_tokens)} in · {count(open.summary.output_tokens)} out
                  </dd>
                </div>
              </dl>
              <ol className="entries">
                {open.entries.map((entry) => (
                  <Entry key={entry.id} entry={entry} run={open.summary} />
                ))}
              </ol>
              {open.answer ? (
                <div className="run-answer">
                  <p className="muted small">Answered by {open.answered_by ?? 'nobody'}:</p>
                  <p className="answer">{renderAnswer(open.answer)}</p>
                </div>
              ) : (
                <p className="muted small">No answer was stored for this question.</p>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  )
}
