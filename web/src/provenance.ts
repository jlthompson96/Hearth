/**
 * Where a figure in an answer came from: the line of a tool result that holds it.
 *
 * This LOCATES a source; it does not judge one. Whether a figure is grounded is
 * decided by the backend (`agents/grounding.py`, rule 7) and arrives as the
 * `ungrounded` list. The page never links a figure the backend flagged, and a
 * figure it cannot find in this turn's results — one quoted from the question, or
 * from an earlier answer's data — simply has no link, rather than a guess.
 *
 * No arithmetic: figures are compared as strings reduced to a canonical form
 * (commas, padding zeros and a redundant ".00" removed), so "$38,250" finds a
 * tool's "38,250.00" exactly as the backend's by-value comparison would.
 */

/** A tool call as the page holds it: what was asked, and what came back. */
export type ToolRun = {
  name: string
  args: Record<string, unknown>
  result?: string
}

export type Source = { tool: string; args: Record<string, unknown>; lines: string[] }

/** The figures the backend looks for — money and weights — in the same shapes.
 *  Group 1 is a dollar amount's digits, group 2 a weight's. */
export const FIGURE =
  /[-−]?\$\s?(\d[\d,]*(?:\.\d+)?)|(\d+(?:\.\d+)?)\s?(?:kg|kgs|kilos?|kilograms?|lbs?|pounds?)\b/gi

/** A number in a tool line, whole. It cannot start inside another number (after a
 *  digit, comma or point) or end inside one, and digits inside a date
 *  ("2026-01-06") are not figures: a hyphen between digits marks one. Without the
 *  first two conditions the pattern backtracks to "6" in "06", and "$6.00" would
 *  appear to come from a date. */
const NUMBER = /(?<![\d,.]|\d-)\d[\d,]*(?:\.\d+)?(?!\d|[.,]\d|-\d)/g

/** "38,250.00" and "$38,250" → "38250"; "0.50" → "0.5". String work only. */
export function canon(digits: string): string {
  let text = digits.replace(/,/g, '')
  if (text.includes('.')) text = text.replace(/0+$/, '').replace(/\.$/, '')
  return text.replace(/^0+(?=\d)/, '')
}

const MOST_LINES = 3

/** The first tool run whose result states `value` (already canonical), with the
 *  line or lines that do. Null if none of this turn's results has it. */
export function findSource(value: string, runs: ToolRun[]): Source | null {
  for (const run of runs) {
    if (!run.result) continue
    const lines = run.result
      .split('\n')
      .filter((line) => (line.match(NUMBER) ?? []).some((n) => canon(n) === value))
      .map((line) => line.trim())
    if (lines.length > 0) {
      return { tool: run.name, args: run.args, lines: lines.slice(0, MOST_LINES) }
    }
  }
  return null
}

/** `key=value key=value`, as a tool call is shown everywhere on the page. */
export function formatArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(' ')
}
