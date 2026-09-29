import type { Analysis, Incident, LearnResponse } from './api/types'

/**
 * Derives how far an incident has actually progressed.
 *
 * Every state here comes from a real API response: the incident row, an analysis
 * that was actually returned, or a learn result that was actually returned. The
 * backend exposes no audit log, so anything that cannot be determined is reported
 * as `unknown` rather than guessed at. In particular a resolved incident may or
 * may not have been approved first, because /resolve has no prerequisite, so
 * review is only ever `done` when the status itself says so.
 */

export type StepId = 'report' | 'recall' | 'analyze' | 'review' | 'resolve' | 'learn' | 'memory'

export type StepState = 'done' | 'current' | 'pending' | 'unknown'

export const WORKFLOW: {
  id: StepId
  label: string
  hint: string
}[] = [
  { id: 'report', label: 'Report', hint: 'Incident filed and retained as a report' },
  { id: 'recall', label: 'Recall', hint: 'Hindsight retrieves past experiences' },
  { id: 'analyze', label: 'Analyze', hint: 'Groq reasons over the recalled evidence' },
  { id: 'review', label: 'Human review', hint: 'A person approves or rejects the advice' },
  { id: 'resolve', label: 'Resolve', hint: 'Confirmed cause, fix, and outcome recorded' },
  { id: 'learn', label: 'Learn', hint: 'The confirmed experience is stored' },
  { id: 'memory', label: '🧠 Hindsight', hint: 'Available to future incidents' },
]

export type Workflow = {
  state: Record<StepId, StepState>
  /** Confirmed experiences actually returned for this incident. */
  confirmedCount: number
  /** Unverified reports actually returned for this incident. */
  unverifiedCount: number
  totalMemories: number
}

function isReviewed(incident: Incident): boolean {
  return incident.status === 'approved' || incident.status === 'rejected'
}

export function deriveWorkflow(
  incident: Incident | null,
  analysis: Analysis | null,
  learnResult: LearnResponse | null,
): Workflow {
  const memories = analysis?.supporting_memories ?? []
  const confirmedCount = memories.filter((m) => m.verification === 'confirmed').length
  const unverifiedCount = memories.length - confirmedCount

  if (!incident) {
    return {
      state: {
        report: 'current',
        recall: 'pending',
        analyze: 'pending',
        review: 'pending',
        resolve: 'pending',
        learn: 'pending',
        memory: 'pending',
      },
      confirmedCount: 0,
      unverifiedCount: 0,
      totalMemories: 0,
    }
  }

  const learned = learnResult?.learned === true || learnResult?.already_learned === true
  const resolved = incident.status === 'resolved'

  // /analyze performs the recall internally, so a returned analysis is the only
  // evidence either step ran.
  const analyzed = analysis !== null

  const resolvedState: Record<StepId, StepState> = {
    report: 'done',
    recall: analyzed ? 'done' : 'pending',
    analyze: analyzed ? 'done' : 'pending',
    // A resolved incident is not evidence that anyone reviewed the AI advice.
    review: isReviewed(incident) ? 'done' : resolved ? 'unknown' : 'pending',
    resolve: resolved ? 'done' : 'pending',
    learn: learned ? 'done' : resolved ? 'pending' : 'pending',
    memory: learned ? 'done' : 'pending',
  }

  // The current step is the first one that is still outstanding. Unknown steps
  // are skipped rather than treated as the current step, because "we cannot
  // tell" is not a place the workflow is waiting.
  const order = WORKFLOW.map((s) => s.id)
  const firstOutstanding = order.find((id) => {
    const state = resolvedState[id]
    return state === 'pending' || state === 'current'
  })

  if (firstOutstanding) {
    resolvedState[firstOutstanding] = 'current'
  }

  return {
    state: resolvedState,
    confirmedCount,
    unverifiedCount,
    totalMemories: memories.length,
  }
}

/** Short human sentence explaining the current step, for the loop's caption. */
export function workflowCaption(workflow: Workflow, incident: Incident | null): string {
  if (!incident) {
    return 'File an incident to start the loop.'
  }

  const current = WORKFLOW.find((step) => workflow.state[step.id] === 'current')

  if (!current) {
    return 'Loop complete. This incident is stored in Hindsight as confirmed experience.'
  }

  return `Current step: ${current.label} — ${current.hint}.`
}
