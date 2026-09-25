/**
 * Manual entry, the training half: a workout with its sets, and body weight.
 *
 * Every weight is in pounds — the unit comes from the listing, not a field, so
 * nothing typed here can mix units. A weight is typed as text and read by the
 * same strict rules as a dollar amount: exactly, or refused by its shape. An
 * empty weight is bodyweight work, which is no load rather than zero.
 *
 * Exercise names are offered from what is already logged. The Forge tool
 * matches a lift by name, and the server folds "Bench Press" into an existing
 * "bench press" — the list is there so the right name is the easy one to type.
 */
import { useCallback, useEffect, useState } from 'react'

import {
  data,
  reason,
  type BodyWeightOut,
  type TrainingListing,
  type WorkoutOut,
} from './api/data'
import { showToast } from './toast'

type SetRow = { exercise: string; reps: string; weight: string }

const EMPTY_SET: SetRow = { exercise: '', reps: '', weight: '' }

/** "225.000" → "225", "235.500" → "235.5". Display only: the stored figure is
 * exact, and trailing zeros after the point say nothing about the lift. */
function trim(value: string): string {
  return value.includes('.') ? value.replace(/\.?0+$/, '') : value
}

export function TrainingEntry({ active }: { active: boolean }) {
  const [listing, setListing] = useState<TrainingListing | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setListing(await data.training())
      setFailure(null)
    } catch (error) {
      setFailure(reason(error))
    }
  }, [])

  useEffect(() => {
    if (active) void refresh()
  }, [active, refresh])

  async function removeWorkout(workout: WorkoutOut) {
    if (!window.confirm(`Remove the ${workout.kind} session on ${workout.performed_on}?`)) return
    try {
      await data.removeWorkout(workout.id)
    } catch (error) {
      setFailure(reason(error))
    }
    await refresh()
  }

  async function removeBodyWeight(entry: BodyWeightOut) {
    if (!window.confirm(`Remove the body weight for ${entry.as_of}?`)) return
    try {
      await data.removeBodyWeight(entry.id)
    } catch (error) {
      setFailure(reason(error))
    }
    await refresh()
  }

  if (!listing) return failure ? <p className="error">{failure}</p> : null
  const unit = listing.unit

  return (
    <>
      <h3 className="training-heading">Training</h3>
      {failure && <p className="error">{failure}</p>}
      <div className="forms">
        <WorkoutForm listing={listing} onDone={refresh} />
        <BodyWeightForm unit={unit} onDone={refresh} />
      </div>

      {listing.workouts.length > 0 && (
        <>
          <h3>Sessions entered by hand, most recent first</h3>
          <table className="table">
            <tbody>
              {listing.workouts.map((workout) => (
                <tr key={workout.id}>
                  <td className="muted">{workout.performed_on}</td>
                  <td>
                    <SessionSummary workout={workout} />
                  </td>
                  <td className="num">
                    <button
                      type="button"
                      className="quiet"
                      onClick={() => void removeWorkout(workout)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {listing.body_weights.length > 0 && (
        <>
          <h3>Body weight entered by hand</h3>
          <table className="table">
            <tbody>
              {listing.body_weights.map((entry) => (
                <tr key={entry.id}>
                  <td className="muted">{entry.as_of}</td>
                  <td className="num">
                    {trim(entry.weight)} {entry.unit}
                  </td>
                  <td className="num">
                    <button
                      type="button"
                      className="quiet"
                      onClick={() => void removeBodyWeight(entry)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  )
}

/** "back squat 225×5, 225×5, 235.5×5 · pull-up bw×8" — sets grouped by lift,
 * in the order they were done. */
function SessionSummary({ workout }: { workout: WorkoutOut }) {
  const lifts: { exercise: string; sets: string[] }[] = []
  for (const set of workout.sets) {
    const last = lifts[lifts.length - 1]
    const text = `${set.weight == null ? 'bw' : trim(set.weight)}×${set.reps}`
    if (last && last.exercise === set.exercise) last.sets.push(text)
    else lifts.push({ exercise: set.exercise, sets: [text] })
  }
  const unit = workout.sets.find((s) => s.unit)?.unit
  return (
    <span>
      <span className="muted">{workout.kind}</span>
      {workout.duration_minutes && (
        <span className="muted"> · {trim(workout.duration_minutes)} min</span>
      )}
      {lifts.map((lift) => (
        <span key={lift.exercise}>
          {' · '}
          {lift.exercise} {lift.sets.join(', ')}
        </span>
      ))}
      {unit && <span className="muted small"> ({unit})</span>}
      {workout.notes && <span className="muted small"> — {workout.notes}</span>}
    </span>
  )
}

function WorkoutForm({
  listing,
  onDone,
}: {
  listing: TrainingListing
  onDone: () => Promise<void>
}) {
  // Empty, and required: the day a session happened is the person's to state.
  const [performedOn, setPerformedOn] = useState('')
  const [kind, setKind] = useState('strength')
  const [duration, setDuration] = useState('')
  const [notes, setNotes] = useState('')
  const [sets, setSets] = useState<SetRow[]>([{ ...EMPTY_SET }])
  const [refusal, setRefusal] = useState<string | null>(null)

  const filled = sets.filter((s) => s.exercise.trim() || s.reps.trim() || s.weight.trim())
  const complete = filled.every((s) => s.exercise.trim() && /^\d+$/.test(s.reps.trim()))
  const ready = performedOn && complete && (kind !== 'strength' || filled.length > 0)

  function change(index: number, field: keyof SetRow, value: string) {
    setSets((rows) => rows.map((row, i) => (i === index ? { ...row, [field]: value } : row)))
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    try {
      await data.recordWorkout({
        performed_on: performedOn,
        kind,
        duration_minutes: duration.trim() || null,
        notes: notes.trim() || null,
        sets: filled.map((s) => ({
          exercise: s.exercise,
          reps: Number(s.reps),
          weight: s.weight.trim() || null,
        })),
      })
      setSets([{ ...EMPTY_SET }])
      setDuration('')
      setNotes('')
      setRefusal(null)
      showToast(`Session on ${performedOn} recorded`)
    } catch (error) {
      setRefusal(reason(error))
    }
    await onDone()
  }

  return (
    <form className="card wide" onSubmit={submit}>
      <h3>Record a workout</h3>
      <div className="workout-fields">
        <label>
          Date
          <input
            type="date"
            value={performedOn}
            onChange={(e) => setPerformedOn(e.target.value)}
            required
          />
        </label>
        <label>
          Kind
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {listing.kinds.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <label>
          Minutes <span className="muted small">(optional)</span>
          <input
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
            inputMode="decimal"
            placeholder="60"
          />
        </label>
      </div>

      <datalist id="logged-exercises">
        {listing.exercises.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>
      <div className="set-rows" role="group" aria-label="Sets">
        <div className="set-row set-head muted small">
          <span>Exercise</span>
          <span>Reps</span>
          <span>Weight ({listing.unit})</span>
          <span />
        </div>
        {sets.map((row, index) => (
          <div className="set-row" key={index}>
            <input
              value={row.exercise}
              onChange={(e) => change(index, 'exercise', e.target.value)}
              list="logged-exercises"
              placeholder="back squat"
              aria-label={`Set ${index + 1} exercise`}
              maxLength={80}
            />
            <input
              value={row.reps}
              onChange={(e) => change(index, 'reps', e.target.value)}
              inputMode="numeric"
              placeholder="5"
              aria-label={`Set ${index + 1} reps`}
            />
            <input
              value={row.weight}
              onChange={(e) => change(index, 'weight', e.target.value)}
              inputMode="decimal"
              placeholder="bodyweight"
              aria-label={`Set ${index + 1} weight in ${listing.unit}`}
            />
            <button
              type="button"
              className="quiet"
              aria-label={`Remove set ${index + 1}`}
              disabled={sets.length === 1}
              onClick={() => setSets((rows) => rows.filter((_, i) => i !== index))}
            >
              ×
            </button>
          </div>
        ))}
      </div>
      <div className="inline">
        <button
          type="button"
          className="quiet"
          onClick={() => setSets((rows) => [...rows, { ...rows[rows.length - 1] }])}
        >
          Repeat last set
        </button>
        <button
          type="button"
          className="quiet"
          onClick={() => setSets((rows) => [...rows, { ...EMPTY_SET }])}
        >
          Add a set
        </button>
      </div>
      <p className="muted small">
        Weights in {listing.unit}, as lifted. Leave the weight empty for bodyweight work.
      </p>
      <label>
        Notes <span className="muted small">(optional)</span>
        <input value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={500} />
      </label>
      {refusal && <p className="error">{refusal}</p>}
      <button type="submit" disabled={!ready}>
        Record workout
      </button>
    </form>
  )
}

function BodyWeightForm({ unit, onDone }: { unit: string; onDone: () => Promise<void> }) {
  const [asOf, setAsOf] = useState('')
  const [weight, setWeight] = useState('')
  const [refusal, setRefusal] = useState<string | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    try {
      await data.recordBodyWeight({ as_of: asOf, weight })
      setWeight('')
      setRefusal(null)
      showToast(`Body weight for ${asOf} recorded`)
    } catch (error) {
      setRefusal(reason(error))
    }
    await onDone()
  }

  return (
    <form className="card" onSubmit={submit}>
      <h3>Record body weight</h3>
      <label>
        Date
        <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} required />
      </label>
      <label>
        Weight ({unit})
        <input
          value={weight}
          onChange={(e) => setWeight(e.target.value)}
          placeholder="181.4"
          inputMode="decimal"
          required
        />
      </label>
      <p className="muted small">One a day. A day already recorded is never overwritten.</p>
      {refusal && <p className="error">{refusal}</p>}
      <button type="submit" disabled={!asOf || !weight.trim()}>
        Record
      </button>
    </form>
  )
}
