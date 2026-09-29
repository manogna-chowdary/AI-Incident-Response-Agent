import { parseMemory } from '../api/memory'
import type { Analysis, Incident, PastExperience } from '../api/types'
import { MemoryList } from './MemoryList'
import { Card, CardHeader, ConfidenceBadge, EmptyState, HistoricalWarning, Mono, Spinner } from './ui'

/**
 * Split a confirmed memory id out of the analysis prose.
 *
 * The model writes about a past incident in free text, so the only reliable way
 * to attach its "why relevant" sentence to a specific memory is to find which
 * memory the surrounding sentence names. Anything not attributed is still
 * shown, just without a per-memory rationale, and the card says so rather than
 * inventing one.
 */
function relevanceByMemory(
  analysis: Analysis,
  memories: PastExperience[],
): Map<string, string> {
  const result = new Map<string, string>()

  for (const memory of memories) {
    const { incidentId } = parseMemory(memory)
    if (!incidentId) {
      continue
    }

    // Sentences are the model's unit of explanation.
    const sentences = analysis.analysis
      .split(/(?<=[.!?])\s+/)
      .map((sentence) => sentence.trim())
      .filter((sentence) => sentence.includes(incidentId))

    if (sentences.length > 0) {
      result.set(memory.text, sentences.join(' '))
    }
  }

  return result
}

/**
 * The two things a viewer needs from an analysis, in priority order: the
 * conclusion, and whether the agent was willing to give one.
 *
 * The model call is advisory, so this section never claims to have changed the
 * incident. The null branches are written out explicitly because "the agent
 * declined to conclude" is a feature worth showing, not an empty state to hide.
 */
export function AnalysisPanel({
  analysis,
  loading,
  memories,
  currentService,
}: {
  analysis: Analysis | null
  loading: boolean
  memories: PastExperience[]
  currentService: string
}) {
  if (loading) {
    return (
      <Card>
        <Spinner label="Recalling past experiences from Hindsight, then reasoning with Groq…" />
      </Card>
    )
  }

  if (!analysis) {
    return null
  }

  const relevance = relevanceByMemory(analysis, memories)
  const confirmedCount = memories.filter((m) => m.verification === 'confirmed').length

  return (
    <>
      <Card className="analysis">
        <CardHeader
          icon="🤖"
          title="AI Analysis"
          subtitle="Advisory. Nothing here was written to the incident."
          actions={<ConfidenceBadge confidence={analysis.confidence} />}
        />

        <p className="analysis__model">
          Groq model <Mono>{analysis.model}</Mono> · reasoning over{' '}
          {memories.length > 0 ? (
            <>
              <strong>{memories.length}</strong> recalled{' '}
              {memories.length === 1 ? 'experience' : 'experiences'}
            </>
          ) : (
            'no recalled experiences'
          )}
        </p>

        <div className="analysis__grid">
          <div className="analysis__cell analysis__cell--lead">
            <span className="analysis__cell-label">Likely Root Cause</span>
            {analysis.likely_root_cause ? (
              <p>{analysis.likely_root_cause}</p>
            ) : (
              <EmptyState
                title="Root cause not determined"
                message="The available evidence did not support a conclusion, so the agent did not offer one."
              />
            )}
          </div>

          <div className="analysis__cell">
            <span className="analysis__cell-label">Confidence</span>
            <p className="analysis__confidence">
              <ConfidenceBadge confidence={analysis.confidence} />
            </p>
            {analysis.likely_root_cause ? null : (
              <p className="analysis__unknown">
                Confidence applies to the evidence, not to a conclusion.
              </p>
            )}
          </div>

          <div className="analysis__cell">
            <span className="analysis__cell-label">Recommended Resolution</span>
            {analysis.recommended_resolution ? (
              <p>{analysis.recommended_resolution}</p>
            ) : (
              <p className="analysis__unknown">No recommendation returned</p>
            )}
          </div>
        </div>

        <details className="analysis__narrative">
          <summary>Full analysis</summary>
          <p className="analysis__text">{analysis.analysis}</p>
        </details>
      </Card>

      <Card className="hindsight">
        <CardHeader
          icon="🧠"
          title="Hindsight Supporting Memories"
          subtitle="Historical experiences retrieved from long-term memory."
          actions={
            <span className="hindsight__count">
              {memories.length} recalled
            </span>
          }
        />

        {memories.length === 0 ? (
          <EmptyState
            title="🧠 No sufficiently relevant confirmed experience found"
            message="Hindsight retrieved nothing relevant to this service or symptom, so the analysis above rests on the incident description alone."
          />
        ) : (
          <>
            <HistoricalWarning />
            <MemoryList
              memories={memories}
              currentService={currentService}
              relevance={relevance}
            />
          </>
        )}

        {confirmedCount === 0 && memories.length > 0 ? (
          <p className="hindsight__insufficient">
            Every recalled item is an unverified report. The agent avoids making an
            unsupported conclusion when historical evidence is insufficient.
          </p>
        ) : null}
      </Card>
    </>
  )
}

/** Placeholder shown before the first analysis, so the page never looks broken. */
export function AnalysisPrompt({ incident }: { incident: Incident }) {
  return (
    <Card>
      <CardHeader
        icon="🤖"
        title="AI Analysis"
        subtitle="No analysis has been run for this incident yet."
      />
      <p className="memory__none">
        Run <strong>Analyze with Hindsight + Groq</strong> to recall past experiences
        for {incident.service} and reason over them. The result appears here and
        does not change the incident without your approval.
      </p>
    </Card>
  )
}
