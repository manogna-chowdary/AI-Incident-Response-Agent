import type { PastExperience } from './types'

/**
 * A recalled memory is a single blob of text with `key: value` lines. The API
 * returns it verbatim and this parses those lines back into fields so the UI can
 * show a confirmed experience's root cause, resolution, and outcome as distinct
 * labelled values instead of one paragraph of text.
 *
 * This mirrors the shape written by backend/app/memory.py. It is a display
 * convenience only: `verification` and `experience_type` always come from the
 * API and are never inferred here.
 */
export type ParsedMemory = {
  incidentId: string | null
  service: string | null
  severity: string | null
  reportedAt: string | null
  description: string | null
  rootCause: string | null
  resolution: string | null
  outcome: string | null
}

const FIELDS: Record<keyof ParsedMemory, string[]> = {
  incidentId: ['incident_id'],
  service: ['service'],
  severity: ['severity'],
  reportedAt: ['reported_at'],
  description: ['original_description', 'description'],
  rootCause: ['confirmed_root_cause', 'root_cause'],
  resolution: ['confirmed_resolution', 'resolution'],
  outcome: ['confirmed_outcome', 'outcome'],
}

const NOT_DETERMINED = /^not determined/i

function parseFields(text: string): ParsedMemory {
  // A memory always arrives with text in practice, but a null or missing value
  // must not take the whole card down: MemoryCard parses before it renders, so
  // a throw here would blank the memory entirely.
  const lines = (typeof text === 'string' ? text : '').split('\n')
  const result = {} as ParsedMemory

  for (const [key, labels] of Object.entries(FIELDS) as [
    keyof ParsedMemory,
    string[],
  ][]) {
    let value: string | null = null

    for (const line of lines) {
      const trimmed = line.trim()

      for (const label of labels) {
        if (trimmed.toLowerCase().startsWith(`${label.toLowerCase()}:`)) {
          const candidate = trimmed.slice(label.length + 1).trim()
          if (candidate) {
            value = candidate
            break
          }
        }
      }

      if (value !== null) {
        break
      }
    }

    // A report writes "not determined at time of report". Showing that string as
    // a root cause would be worse than showing nothing.
    if (value !== null && NOT_DETERMINED.test(value)) {
      value = null
    }

    result[key] = value
  }

  return result
}

export function parseMemory(memory: PastExperience): ParsedMemory {
  return parseFields(typeof memory?.text === 'string' ? memory.text : '')
}

/**
 * True when this memory actually carries a confirmed conclusion, rather than
 * merely being labelled confirmed. A memory can be marked confirmed and still
 * be missing its values if something other than /learn wrote it.
 */
export function hasConfirmedConclusion(memory: PastExperience): boolean {
  if (memory.experience_type !== 'confirmed_resolution') {
    return false
  }

  const parsed = parseMemory(memory)
  return Boolean(parsed.rootCause && parsed.resolution && parsed.outcome)
}
