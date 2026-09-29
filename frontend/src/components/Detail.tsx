import type { Incident } from '../api/types'
import { Field, Mono, SeverityBadge, StatusBadge } from './ui'

/**
 * A point-in-time view of one incident, built from the values the API returns.
 *
 * `events` is derived from the record rather than from a separate history
 * endpoint: the backend has no audit log, so the ordering below is inferred from
 * status and the populated fields, and is labelled as such. Only the report time
 * is a real timestamp; every later step says what established it instead of
 * inventing a clock reading.
 */
export function Timeline({
  incident,
  analyzed,
  learned,
}: {
  incident: Incident
  /** An analysis was actually returned for this incident in this session. */
  analyzed: boolean
  /** A learn call actually succeeded for this incident in this session. */
  learned: boolean
}) {
  const events: { label: string; detail: string | null; done: boolean }[] = [
    {
      label: 'Incident reported',
      detail: new Date(incident.created_at).toLocaleString(),
      done: true,
    },
  ]

  if (analyzed) {
    events.push({
      label: 'AI analyzed',
      detail: 'Hindsight recall and Groq reasoning completed for this incident.',
      done: true,
    })
  }

  if (incident.status === 'approved' || incident.status === 'rejected') {
    events.push({
      label: 'Human reviewed',
      detail: `A human recorded a decision: recommendation ${incident.status}.`,
      done: true,
    })
  }

  if (incident.status === 'resolved') {
    events.push({
      label: 'Incident resolved',
      detail: incident.root_cause ?? 'Resolution recorded on the incident.',
      done: true,
    })
  }

  if (learned) {
    events.push({
      label: 'Learned into Hindsight',
      detail: 'This confirmed resolution is now recalled as evidence by future incidents.',
      done: true,
    })
  } else if (incident.status === 'resolved') {
    events.push({
      label: 'Learned into Hindsight',
      detail: 'Not yet stored. Use "Learn confirmed resolution" below.',
      done: false,
    })
  }

  if (!incident.root_cause && incident.status !== 'resolved') {
    events.push({
      label: 'Conclusion pending',
      detail: 'Root cause, resolution, and outcome have not been established.',
      done: false,
    })
  }

  return (
    <ol className="timeline">
      {events.map((event, index) => (
        <li
          className={`timeline__item ${event.done ? '' : 'timeline__item--pending'}`.trim()}
          key={index}
        >
          <span className="timeline__marker" aria-hidden="true" />
          <div>
            <p className="timeline__label">{event.label}</p>
            {event.detail ? <p className="timeline__detail">{event.detail}</p> : null}
          </div>
        </li>
      ))}
    </ol>
  )
}

/**
 * The main content header. Incident ID, service, severity, and status are all
 * present, with status and severity as badges so they scan without reading.
 */
export function IncidentHeader({ incident }: { incident: Incident }) {
  return (
    <div className="detail__head">
      <div className="detail__title">
        <h1>{incident.service}</h1>
        <div className="detail__badges">
          <SeverityBadge severity={incident.severity} />
          <StatusBadge status={incident.status} />
          <Mono>{incident.incident_id}</Mono>
        </div>
      </div>
      <p className="detail__description">{incident.description}</p>
      <p className="detail__meta">
        Reported {new Date(incident.created_at).toLocaleString()} · updated{' '}
        {new Date(incident.updated_at).toLocaleString()}
      </p>
    </div>
  )
}

export function IncidentRecord({ incident }: { incident: Incident }) {
  return (
    <>
      <Field label="Root cause" value={incident.root_cause} />
      <Field label="Resolution" value={incident.resolution} />
      <Field label="Outcome" value={incident.outcome} />
      <Field
        label="Last updated"
        value={new Date(incident.updated_at).toLocaleString()}
      />
    </>
  )
}
