/**
 * Types mirroring the backend's Pydantic schemas in backend/app/schemas.py and
 * backend/app/models.py. Kept as plain unions rather than enums so the values
 * are exactly the strings the API returns.
 */

export type Severity = 'unknown' | 'low' | 'medium' | 'high' | 'critical'

export type IncidentStatus =
  | 'open'
  | 'investigating'
  | 'mitigated'
  | 'resolved'
  | 'approved'
  | 'rejected'

export type Confidence = 'low' | 'medium' | 'high'

export type Verification = 'confirmed' | 'unverified'

export type ExperienceType = 'confirmed_resolution' | 'incident_report'

/** GET /health */
export type Health = {
  status: string
}

/** GET /incidents, GET /incidents/{id} */
export type Incident = {
  id: number
  incident_id: string
  service: string
  description: string
  severity: Severity
  status: IncidentStatus
  root_cause: string | null
  resolution: string | null
  outcome: string | null
  created_at: string
  updated_at: string
}

/** POST /incidents */
export type IncidentCreate = Incident & {
  memory_retained: boolean
  memory_error: string | null
}

export type IncidentCreateRequest = {
  service: string
  description: string
  severity: Severity
}

/**
 * A recalled Hindsight memory with its provenance, from PastExperience.
 *
 * `verification` describes how the *past* incident was established. It says
 * nothing about the incident currently on screen.
 */
export type PastExperience = {
  text: string
  type: string | null
  source: 'hindsight'
  verification: Verification
  experience_type: ExperienceType
}

/** POST /incidents/{id}/recall */
export type RecallResponse = {
  incident_id: string
  query: string
  notice: string
  count: number
  results: PastExperience[]
}

/** POST /incidents/{id}/analyze */
export type Analysis = {
  incident_id: string
  analysis: string
  likely_root_cause: string | null
  recommended_resolution: string | null
  confidence: Confidence
  supporting_memories: PastExperience[]
  model: string
}

/** POST /incidents/{id}/approve */
export type ApprovalResponse = Incident & {
  previous_status: IncidentStatus
  approved: boolean
}

/** POST /incidents/{id}/resolve */
export type ResolveResponse = Incident & {
  previous_status: IncidentStatus
}

export type ResolveRequest = {
  root_cause: string
  resolution: string
  outcome: string
}

/** POST /incidents/{id}/learn */
export type LearnResponse = {
  incident_id: string
  status: IncidentStatus
  learned: boolean
  already_learned: boolean
  memory_retained: boolean
  document_id: string
  message: string
}
