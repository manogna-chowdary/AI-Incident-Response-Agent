import type {
  Analysis,
  ApprovalResponse,
  Health,
  Incident,
  IncidentCreate,
  IncidentCreateRequest,
  LearnResponse,
  PastExperience,
  RecallResponse,
  ResolveRequest,
  ResolveResponse,
} from './types'

/**
 * Requests go to `/api/...`; the dev-server proxy strips that prefix before
 * forwarding to the backend, which serves /health and /incidents at the root.
 * Keeping one origin in the browser means no CORS preflight.
 */
const API_BASE = '/api'

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * Pull a readable message out of a FastAPI error body.
 *
 * Validation failures arrive as a list of {loc, msg, type} objects, so a plain
 * `detail` string is not always there to show.
 */
function describe(body: unknown, status: number): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail

    if (typeof detail === 'string') {
      return detail
    }

    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => {
          if (item && typeof item === 'object' && 'msg' in item) {
            const field = (item as { loc?: unknown[] }).loc
            const name = Array.isArray(field) ? String(field[field.length - 1]) : ''
            return name ? `${name}: ${(item as { msg: string }).msg}` : (item as { msg: string }).msg
          }
          return null
        })
        .filter((value): value is string => Boolean(value))

      if (messages.length > 0) {
        return messages.join('; ')
      }
    }
  }

  if (status === 0) {
    return 'Cannot reach the API. Is the backend running on port 8000?'
  }

  return `Request failed with HTTP ${status}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response

  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
      ...init,
    })
  } catch {
    throw new ApiError(
      'Cannot reach the API. Is the backend running on port 8000?',
      0,
    )
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(describe(body, response.status), response.status)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

export function getHealth(): Promise<Health> {
  return request<Health>('/health')
}

export function listIncidents(): Promise<Incident[]> {
  return request<Incident[]>('/incidents')
}

export function getIncident(incidentId: string): Promise<Incident> {
  return request<Incident>(`/incidents/${encodeURIComponent(incidentId)}`)
}

export function createIncident(payload: IncidentCreateRequest): Promise<IncidentCreate> {
  return request<IncidentCreate>('/incidents', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function analyzeIncident(incidentId: string): Promise<Analysis> {
  return request<Analysis>(`/incidents/${encodeURIComponent(incidentId)}/analyze`, {
    method: 'POST',
  })
}

export function recallMemories(
  incidentId: string,
  query: string,
): Promise<RecallResponse> {
  return request<RecallResponse>(
    `/incidents/${encodeURIComponent(incidentId)}/recall`,
    { method: 'POST', body: JSON.stringify({ query }) },
  )
}

export function approveIncident(
  incidentId: string,
  approved: boolean,
): Promise<ApprovalResponse> {
  return request<ApprovalResponse>(
    `/incidents/${encodeURIComponent(incidentId)}/approve`,
    { method: 'POST', body: JSON.stringify({ approved }) },
  )
}

export function resolveIncident(
  incidentId: string,
  payload: ResolveRequest,
): Promise<ResolveResponse> {
  return request<ResolveResponse>(
    `/incidents/${encodeURIComponent(incidentId)}/resolve`,
    { method: 'POST', body: JSON.stringify(payload) },
  )
}

export function learnIncident(incidentId: string): Promise<LearnResponse> {
  return request<LearnResponse>(`/incidents/${encodeURIComponent(incidentId)}/learn`, {
    method: 'POST',
  })
}

export type { PastExperience }
