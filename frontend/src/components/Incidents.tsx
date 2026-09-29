import { useState } from 'react'
import type { Incident, Severity } from '../api/types'
import { Card, CardHeader, ErrorNote, SeverityBadge, StatusBadge } from './ui'

const SEVERITIES: Severity[] = ['unknown', 'low', 'medium', 'high', 'critical']

export function CreateIncidentForm({
  onCreate,
  busy,
  error,
}: {
  onCreate: (service: string, description: string, severity: Severity) => Promise<void>
  busy: boolean
  error: string | null
}) {
  const [service, setService] = useState('')
  const [description, setDescription] = useState('')
  const [severity, setSeverity] = useState<Severity>('unknown')
  const [localError, setLocalError] = useState<string | null>(null)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setLocalError(null)

    // The backend requires both fields; catch it here so the user is not told
    // about a 422 for something the form already knows.
    if (!service.trim()) {
      setLocalError('A service name is required.')
      return
    }
    if (!description.trim()) {
      setLocalError('A description is required.')
      return
    }

    try {
      await onCreate(service.trim(), description.trim(), severity)
      setService('')
      setDescription('')
      setSeverity('unknown')
    } catch {
      // The parent already holds the message from the API.
    }
  }

  return (
    <Card>
      <CardHeader
        icon="📝"
        title="Report an Incident"
        subtitle="Stored in PostgreSQL, and retained in Hindsight as an unverified report."
      />

      <form className="form" onSubmit={handleSubmit}>
        <label className="form__field">
          <span>Service</span>
          <input
            value={service}
            onChange={(event) => setService(event.target.value)}
            placeholder="payment-api"
            disabled={busy}
          />
        </label>

        <label className="form__field">
          <span>Description</span>
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="What is failing, and what are the symptoms?"
            rows={3}
            disabled={busy}
          />
        </label>

        <label className="form__field">
          <span>Severity</span>
          <select
            value={severity}
            onChange={(event) => setSeverity(event.target.value as Severity)}
            disabled={busy}
          >
            {SEVERITIES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        {localError || error ? <ErrorNote title="Could not create" message={(localError ?? error)!} /> : null}

        <button className="button button--primary" type="submit" disabled={busy}>
          {busy ? 'Filing…' : 'File incident'}
        </button>
      </form>
    </Card>
  )
}

export function IncidentList({
  incidents,
  selectedId,
  onSelect,
}: {
  incidents: Incident[]
  selectedId: string | null
  onSelect: (incidentId: string) => void
}) {
  return (
    <Card className="list">
      <CardHeader title="Incidents" subtitle={`${incidents.length} recorded`} />

      {incidents.length === 0 ? (
        <p className="memory__none">No incidents yet.</p>
      ) : (
        <ul className="list__items">
          {incidents.map((incident) => {
            const active = incident.incident_id === selectedId

            return (
              <li key={incident.incident_id}>
                <button
                  className={`list__item ${active ? 'list__item--active' : ''}`.trim()}
                  onClick={() => onSelect(incident.incident_id)}
                  type="button"
                  aria-current={active ? 'true' : undefined}
                >
                  <span className="list__item-top">
                    <span className="list__service">{incident.service}</span>
                    <SeverityBadge severity={incident.severity} />
                  </span>
                  <span className="list__description">{incident.description}</span>
                  <span className="list__item-bottom">
                    <code className="mono">{incident.incident_id}</code>
                    <StatusBadge status={incident.status} />
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
