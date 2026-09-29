import { useState } from 'react'
import type { Incident, LearnResponse, ResolveRequest } from '../api/types'
import {
  Card,
  CardHeader,
  ErrorNote,
  Field,
  InfoNote,
  Mono,
  Spinner,
  StatusBadge,
  SuccessNote,
} from './ui'

/**
 * The human in the loop. The heading and the advisory note are doing real work
 * here: a viewer who has just read an AI recommendation should not be able to
 * miss that acting on it is a separate, manual step.
 */
export function DecisionPanel({
  incident,
  onApprove,
  onReject,
  busy,
  error,
}: {
  incident: Incident
  onApprove: () => Promise<void>
  onReject: () => Promise<void>
  busy: boolean
  error: string | null
}) {
  const decided = incident.status === 'approved' || incident.status === 'rejected'

  return (
    <Card>
      <CardHeader
        icon="👤"
        title="Human Review"
        subtitle="AI recommendations are advisory. A human must review and approve the recommendation before treating it as an action."
      />

      <div className="decision__current">
        <span>Current status</span>
        <StatusBadge status={incident.status} />
      </div>

      {decided ? (
        <InfoNote>
          This incident was already {incident.status}. Approving or rejecting
          again is allowed and simply records the new decision.
        </InfoNote>
      ) : null}

      {error ? <ErrorNote title="Decision failed" message={error} /> : null}

      <div className="decision__buttons">
        <button
          className="button button--approve"
          type="button"
          disabled={busy}
          onClick={onApprove}
        >
          Approve recommendation
        </button>
        <button
          className="button button--reject"
          type="button"
          disabled={busy}
          onClick={onReject}
        >
          Reject recommendation
        </button>
      </div>

      <p className="decision__note">
        Approving sets status to <Mono>approved</Mono> and rejecting sets it to{' '}
        <Mono>rejected</Mono>. Neither writes a root cause, resolution, or
        outcome &mdash; record those below once an investigation has established
        them.
      </p>
    </Card>
  )
}

/** Record what was actually established. These three feed /learn. */
export function ResolvePanel({
  incident,
  onResolve,
  busy,
  error,
}: {
  incident: Incident
  onResolve: (payload: ResolveRequest) => Promise<void>
  busy: boolean
  error: string | null
}) {
  const [rootCause, setRootCause] = useState(incident.root_cause ?? '')
  const [resolution, setResolution] = useState(incident.resolution ?? '')
  const [outcome, setOutcome] = useState(incident.outcome ?? '')
  const [localError, setLocalError] = useState<string | null>(null)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setLocalError(null)

    // The backend rejects a blank field, and a blank value would be worse than
    // none: the row would claim the question was answered while recording nothing.
    if (!rootCause.trim() || !resolution.trim() || !outcome.trim()) {
      setLocalError('All three fields are required. A blank value is not a record.')
      return
    }

    try {
      await onResolve({
        root_cause: rootCause.trim(),
        resolution: resolution.trim(),
        outcome: outcome.trim(),
      })
    } catch {
      // The parent holds the API message.
    }
  }

  return (
    <Card>
      <CardHeader
        icon="✅"
        title="Resolve with confirmed conclusion"
        subtitle="This information represents the human-confirmed outcome of the investigation. Nothing here is generated."
      />

      {incident.status === 'resolved' ? (
        <InfoNote>
          This incident is already resolved. Submitting again overwrites the
          recorded values, which is how a mistaken record is corrected.
        </InfoNote>
      ) : null}

      <form className="form" onSubmit={handleSubmit}>
        <label className="form__field">
          <span>Confirmed root cause</span>
          <textarea
            value={rootCause}
            onChange={(event) => setRootCause(event.target.value)}
            rows={2}
            placeholder="Database connection pool was exhausted."
            disabled={busy}
          />
        </label>

        <label className="form__field">
          <span>Confirmed resolution</span>
          <textarea
            value={resolution}
            onChange={(event) => setResolution(event.target.value)}
            rows={2}
            placeholder="Increased the pool size and restarted the affected instances."
            disabled={busy}
          />
        </label>

        <label className="form__field">
          <span>Confirmed outcome</span>
          <textarea
            value={outcome}
            onChange={(event) => setOutcome(event.target.value)}
            rows={2}
            placeholder="Payment requests recovered successfully."
            disabled={busy}
          />
        </label>

        {localError || error ? (
          <ErrorNote title="Could not resolve" message={(localError ?? error)!} />
        ) : null}

        <button className="button button--primary" type="submit" disabled={busy}>
          {busy ? 'Recording…' : 'Record resolution'}
        </button>
      </form>

      {incident.status === 'resolved' ? (
        <div className="resolve__record">
          <p className="resolve__record-label">Recorded on this incident</p>
          <Field label="Confirmed root cause" value={incident.root_cause} />
          <Field label="Confirmed resolution" value={incident.resolution} />
          <Field label="Confirmed outcome" value={incident.outcome} />
        </div>
      ) : null}
    </Card>
  )
}

/** Store a resolved incident as confirmed experience in Hindsight. */
export function LearnPanel({
  incident,
  onLearn,
  busy,
  error,
  result,
}: {
  incident: Incident
  onLearn: () => Promise<void>
  busy: boolean
  error: string | null
  result: LearnResponse | null
}) {
  const eligible = incident.status === 'resolved'
  const hasFields = Boolean(incident.root_cause && incident.resolution && incident.outcome)
  // The closed loop, and the one thing worth pointing at in a demo: this
  // incident's own report was recalled above as unverified, and its confirmed
  // conclusion is what upgrades the stored memory.
  const learned = result?.learned === true || result?.already_learned === true

  return (
    <Card className="learn" tone={learned ? 'confirmed' : 'default'}>
      <CardHeader
        icon="🧠"
        title="Learn Confirmed Resolution"
        subtitle="Store this human-confirmed incident experience in Hindsight so future incidents can benefit from it."
      />

      {!eligible ? (
        <InfoNote>
          Only a resolved incident can be learned. Record the root cause,
          resolution, and outcome first.
        </InfoNote>
      ) : !hasFields ? (
        <InfoNote>
          This incident is resolved but one of the three confirmed fields is
          missing, so there is nothing trustworthy to store.
        </InfoNote>
      ) : null}

      {error ? <ErrorNote title="Learning failed" message={error} /> : null}

      {result ? (
        result.already_learned ? (
          <InfoNote>
            Already learned. <Mono>{result.document_id}</Mono> is in the memory
            bank, so nothing was written again.
          </InfoNote>
        ) : result.learned ? (
          <SuccessNote
            title="✓ Confirmed experience stored in Hindsight"
            message={`Stored as confirmed experience under document ${result.document_id}. Future analyses will recall it as a confirmed_resolution rather than an unverified report.${result.message ? ` ${result.message}` : ''}`}
          />
        ) : (
          <ErrorNote
            title="Not learned"
            message={result.message || 'The memory was not stored.'}
          />
        )
      ) : null}

      {busy ? <Spinner label="Storing confirmed experience…" /> : null}

      <button
        className="button button--primary"
        type="button"
        disabled={busy || !eligible || !hasFields}
        onClick={onLearn}
      >
        {busy ? 'Learning…' : 'Learn confirmed resolution'}
      </button>

      <p className="decision__note">
        This closes the loop. The incident is currently stored in Hindsight as an{' '}
        <Mono>incident_report</Mono> that is not treated as confirmed evidence.
        Learning replaces it with a <Mono>confirmed_resolution</Mono> that future
        incidents will actually rely on.
      </p>

      <p className="decision__note">
        This reads the incident and never writes to it. A Hindsight failure cannot
        damage the record, and a failed store is reported rather than hidden.
      </p>
    </Card>
  )
}
