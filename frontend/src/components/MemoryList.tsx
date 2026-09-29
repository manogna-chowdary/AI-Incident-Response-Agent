import { hasConfirmedConclusion, parseMemory } from '../api/memory'
import type { PastExperience } from '../api/types'
import { Card, Field, HistoricalWarning, Mono, VerificationBadge } from './ui'

/**
 * One recalled Hindsight memory.
 *
 * This is the component that has to make one distinction unmissable, so the two
 * variants are different components rather than one component with a flag:
 * `ConfirmedMemoryCard` and `UnverifiedMemoryCard`. A judge should not have to
 * read carefully to see that a confirmed experience and a filed report are not
 * the same kind of thing.
 */
export function MemoryCard({
  memory,
  relevance,
  currentService,
}: {
  memory: PastExperience
  /** The model's own sentence(s) explaining the match, from the analysis text. */
  relevance?: string | null
  currentService: string
}) {
  return memory.verification === 'confirmed' ? (
    <ConfirmedMemoryCard
      memory={memory}
      relevance={relevance ?? null}
      currentService={currentService}
    />
  ) : (
    <UnverifiedMemoryCard
      memory={memory}
      relevance={relevance ?? null}
      currentService={currentService}
    />
  )
}

/**
 * A human-verified past incident.
 *
 * Everything in the `memory__historical` block is labelled HISTORICAL at the
 * group level rather than per field, so it reads as one block of prior-incident
 * context and cannot be skimmed as the current incident's answer.
 */
function ConfirmedMemoryCard({
  memory,
  relevance,
  currentService,
}: {
  memory: PastExperience
  relevance: string | null
  currentService: string
}) {
  const parsed = parseMemory(memory)
  const complete = hasConfirmedConclusion(memory)
  const sameService = parsed.service?.toLowerCase() === currentService.toLowerCase()

  return (
    <Card tone="confirmed" className="memory memory--confirmed">
      <div className="memory__head">
        <VerificationBadge
          verification={memory.verification}
          experienceType={memory.experience_type}
        />
        <Mono>{parsed.incidentId ?? 'no incident id'}</Mono>
      </div>

      <h3 className="memory__kind">✓ Confirmed Experience</h3>
      <p className="memory__kind-note">
        Human-verified outcome from a previous incident.
      </p>

      <div className="memory__historical">
        <p className="memory__historical-label">Historical incident</p>

        <Field label="Incident ID" value={parsed.incidentId} />
        <Field label="Service" value={parsed.service} />
        <Field label="Severity" value={parsed.severity} />

        <div className="memory__historical-block">
          <p className="memory__historical-block-label">Historical root cause</p>
          <p className="memory__historical-value">
            {parsed.rootCause ?? <em>Not recorded in this memory</em>}
          </p>
        </div>

        <div className="memory__historical-block">
          <p className="memory__historical-block-label">Historical resolution</p>
          <p className="memory__historical-value">
            {parsed.resolution ?? <em>Not recorded in this memory</em>}
          </p>
        </div>

        <div className="memory__historical-block">
          <p className="memory__historical-block-label">Historical outcome</p>
          <p className="memory__historical-value">
            {parsed.outcome ?? <em>Not recorded in this memory</em>}
          </p>
        </div>
      </div>

      {parsed.description ? (
        <p className="memory__description">{parsed.description}</p>
      ) : null}

      {sameService ? (
        <p className="memory__service">
          Same service as the current incident: <strong>{parsed.service}</strong>
        </p>
      ) : parsed.service ? (
        <p className="memory__service">
          Different service (<strong>{parsed.service}</strong>)
          {parsed.severity ? ` · severity ${parsed.severity}` : ''}
        </p>
      ) : null}

      {!complete ? (
        <p className="memory__incomplete">
          Marked confirmed, but this record carries no complete root cause,
          resolution, and outcome. Treat it as unverified until someone checks it.
        </p>
      ) : null}

      <HistoricalWarning />

      <WhyMatched relevance={relevance} complete={complete} />

      <RawMemory memory={memory} />
    </Card>
  )
}

/**
 * A report filed at report time. Deliberately quieter than a confirmed
 * experience: it is real signal about symptoms and service, and it is
 * explicitly not evidence of a cause.
 */
function UnverifiedMemoryCard({
  memory,
  relevance,
  currentService,
}: {
  memory: PastExperience
  relevance: string | null
  currentService: string
}) {
  const parsed = parseMemory(memory)
  const sameService = parsed.service?.toLowerCase() === currentService.toLowerCase()

  return (
    <Card tone="unverified" className="memory memory--unverified">
      <div className="memory__head">
        <VerificationBadge
          verification={memory.verification}
          experienceType={memory.experience_type}
        />
        <Mono>{parsed.incidentId ?? 'no incident id'}</Mono>
      </div>

      <h3 className="memory__kind">○ Unverified Report</h3>
      <p className="memory__kind-note">
        Historical incident report. Not treated as confirmed root-cause evidence.
      </p>

      <div className="memory__report">
        <p className="memory__historical-label">Historical incident information</p>

        <Field label="Service" value={parsed.service} />
        <Field label="Severity" value={parsed.severity} />

        <div className="memory__historical-block">
          <p className="memory__historical-block-label">Description</p>
          <p className="memory__historical-value">
            {parsed.description ?? <em>Not recorded in this memory</em>}
          </p>
        </div>
      </div>

      {sameService ? (
        <p className="memory__service">
          Same service as the current incident: <strong>{parsed.service}</strong>
        </p>
      ) : null}

      <p className="memory__unverified">
        Not used as confirmed root-cause evidence. Its root cause, resolution, and
        outcome were never established &mdash; the memory records them as
        &ldquo;not determined at time of report&rdquo;. It contributes a symptom and
        a service name only.
      </p>

      {relevance ? <WhyMatched relevance={relevance} /> : null}

      <RawMemory memory={memory} />
    </Card>
  )
}

/**
 * The evidence block for "why did this come back?".
 *
 * Two separate sources, deliberately not merged. `relevance` is the model's own
 * sentence naming this incident, quoted from the analysis text. The bullets are
 * facts read from the stored memory itself, so they are labelled as properties
 * of the memory rather than presented as the model's reasoning. Nothing is
 * generated: if the model said nothing, it says so.
 */
function WhyMatched({
  relevance,
  complete,
}: {
  relevance: string | null
  complete?: boolean
}) {
  const facts: string[] = []
  if (complete) {
    facts.push('Historical resolution available')
  }

  if (!relevance && facts.length === 0) {
    return null
  }

  return (
    <div className="memory__evidence">
      <p className="memory__evidence-label">Why this memory matched</p>

      {facts.length > 0 ? (
        <ul className="memory__evidence-list">
          {facts.map((fact) => (
            <li key={fact}>✓ {fact}</li>
          ))}
        </ul>
      ) : null}

      {relevance ? (
        <div className="memory__evidence-reason">
          <span className="memory__evidence-reason-label">
            In the model&rsquo;s own words
          </span>
          <p>{relevance}</p>
        </div>
      ) : (
        <p className="memory__evidence-none">
          The model did not state a specific reason for this match.
        </p>
      )}
    </div>
  )
}

/**
 * The memory exactly as Hindsight returned it, always visible.
 *
 * This was a collapsed <details>, which rendered as a summary line with no
 * content until clicked, so the section read as blank. It is also deliberately
 * not height-capped: the requirement is the complete text, and clipping the tail
 * of a long memory hid values the viewer is meant to be checking.
 */
function RawMemory({ memory }: { memory: PastExperience }) {
  const text = typeof memory.text === 'string' ? memory.text : ''

  return (
    <div className="memory__raw">
      <p className="memory__raw-label">Raw memory from Hindsight</p>

      {text.trim() ? (
        <pre className="memory__raw-text">{text}</pre>
      ) : (
        <p className="memory__raw-empty">No raw memory text available</p>
      )}
    </div>
  )
}

export function MemoryList({
  memories,
  currentService,
  relevance,
}: {
  memories: PastExperience[]
  currentService: string
  /** memory text -> the model's sentence(s) explaining why it matched. */
  relevance: Map<string, string>
}) {
  if (memories.length === 0) {
    return (
      <Card className="memory__empty">
        <p className="memory__none">
          Hindsight returned no past experiences for this incident. Any analysis
          below rests on the incident description alone.
        </p>
      </Card>
    )
  }

  const confirmed = memories.filter((m) => m.verification === 'confirmed').length
  const unverified = memories.length - confirmed

  return (
    <>
      <div className="memory__legend">
        <span className="memory__legend-counts">
          <strong>{memories.length}</strong> recalled ·{' '}
          <strong className="memory__legend-confirmed">{confirmed}</strong> confirmed ·{' '}
          <strong className="memory__legend-unverified">{unverified}</strong> unverified
        </span>
        <span className="memory__legend-note">
          Confirmed first, strongest evidence on top.
        </span>
      </div>

      <div className="memory__list">
        {memories.map((memory, index) => (
          <MemoryCard
            key={`${(memory.text ?? '').slice(0, 40)}-${index}`}
            memory={memory}
            currentService={currentService}
            relevance={relevance.get(memory.text) ?? null}
          />
        ))}
      </div>
    </>
  )
}
