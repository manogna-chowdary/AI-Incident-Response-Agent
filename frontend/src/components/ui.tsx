import type { ReactNode } from 'react'
import type { Confidence, IncidentStatus, Severity, Verification } from '../api/types'

/* --- layout ---------------------------------------------------------------- */

export function AppShell({ children }: { children: ReactNode }) {
  return <div className="shell">{children}</div>
}

export function Card({
  children,
  tone = 'default',
  className = '',
}: {
  children: ReactNode
  tone?: 'default' | 'confirmed' | 'unverified' | 'warning'
  className?: string
}) {
  return <section className={`card card--${tone} ${className}`.trim()}>{children}</section>
}

export function CardHeader({
  title,
  subtitle,
  actions,
  icon,
}: {
  title: string
  subtitle?: string
  actions?: ReactNode
  icon?: string
}) {
  return (
    <header className="card__header">
      <div className="card__heading">
        <h2 className="card__title">
          {icon ? (
            <span className="card__icon" aria-hidden="true">
              {icon}
            </span>
          ) : null}
          {title}
        </h2>
        {subtitle ? <p className="card__subtitle">{subtitle}</p> : null}
      </div>
      {actions ? <div className="card__actions">{actions}</div> : null}
    </header>
  )
}

export function Split({ children }: { children: ReactNode }) {
  return <div className="split">{children}</div>
}

/* --- badges ---------------------------------------------------------------- */

const SEVERITY_LABEL: Record<Severity, string> = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
  unknown: 'Unknown',
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={`badge badge--severity-${severity}`}>
      {SEVERITY_LABEL[severity]}
    </span>
  )
}

const STATUS_LABEL: Record<IncidentStatus, string> = {
  open: 'Open',
  investigating: 'Investigating',
  mitigated: 'Mitigated',
  resolved: 'Resolved',
  approved: 'Approved',
  rejected: 'Rejected',
}

export function StatusBadge({ status }: { status: IncidentStatus }) {
  return <span className={`badge badge--status-${status}`}>{STATUS_LABEL[status]}</span>
}

/**
 * The provenance badge. Green means a person verified that past incident;
 * amber means it was only reported. This is the single most important
 * distinction in the UI, so it is always spelled out in full.
 */
export function VerificationBadge({
  verification,
  experienceType,
}: {
  verification: Verification
  experienceType: 'confirmed_resolution' | 'incident_report'
}) {
  const confirmed = verification === 'confirmed'

  return (
    <span className={`badge badge--verification-${verification}`}>
      {confirmed ? 'Confirmed' : 'Unverified'} · {experienceType}
    </span>
  )
}

export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  return (
    <span className={`badge badge--confidence-${confidence}`}>
      {confidence} confidence
    </span>
  )
}

/* --- feedback -------------------------------------------------------------- */

export function Spinner({ label }: { label: string }) {
  return (
    <div className="spinner" role="status">
      <span className="spinner__dot" aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}

export function ErrorNote({ title, message }: { title: string; message: string }) {
  return (
    <div className="note note--error" role="alert">
      <strong>{title}</strong>
      <p>{message}</p>
    </div>
  )
}

export function SuccessNote({ title, message }: { title: string; message: string }) {
  return (
    <div className="note note--success" role="status">
      <strong>{title}</strong>
      <p>{message}</p>
    </div>
  )
}

export function InfoNote({ children }: { children: ReactNode }) {
  return <div className="note note--info">{children}</div>
}

/**
 * The standing warning that historical evidence is not a conclusion.
 *
 * The wording is deliberately blunt because this is the single thing a viewer
 * could otherwise misread: a confirmed past incident sitting directly above the
 * current incident's own (still unknown) root cause.
 */
export function HistoricalWarning() {
  return (
    <div className="note note--warning" role="note">
      <strong>
        <span aria-hidden="true">⚠ </span>
        Historical evidence is not the current incident&rsquo;s confirmed root cause
      </strong>
      <p>
        This experience was confirmed for a <em>previous</em> incident. It is
        historical evidence for the current analysis and is not automatically
        written to the current incident.
      </p>
    </div>
  )
}

export function EmptyState({ title, message }: { title: string; message: string }) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      <p className="empty__message">{message}</p>
    </div>
  )
}

/* --- fields ---------------------------------------------------------------- */

export function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="field">
      <span className="field__label">{label}</span>
      <span className="field__value">{value ?? <em>Not recorded</em>}</span>
    </div>
  )
}

export function Mono({ children }: { children: ReactNode }) {
  return <code className="mono">{children}</code>
}
