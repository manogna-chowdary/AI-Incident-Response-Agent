import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  analyzeIncident,
  approveIncident,
  createIncident,
  getHealth,
  learnIncident,
  listIncidents,
  resolveIncident,
} from './api/client'
import type {
  Analysis,
  Health,
  Incident,
  IncidentCreate,
  LearnResponse,
  ResolveRequest,
  Severity,
} from './api/types'
import { AnalysisPanel, AnalysisPrompt } from './components/AnalysisPanel'
import { IncidentHeader, Timeline } from './components/Detail'
import { CreateIncidentForm, IncidentList } from './components/Incidents'
import { LearningLoop } from './components/LearningLoop'
import { DecisionPanel, LearnPanel, ResolvePanel } from './components/Panels'
import {
  AppShell,
  Card,
  CardHeader,
  ErrorNote,
  Split,
  Spinner,
} from './components/ui'
import { deriveWorkflow } from './workflow'

type Busy = 'create' | 'analyze' | 'approve' | 'resolve' | 'learn' | null

function message(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message
  }
  return error instanceof Error ? error.message : String(error)
}

function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [pinnedId, setPinnedId] = useState<string | null>(null)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [learnResult, setLearnResult] = useState<LearnResponse | null>(null)
  const [busy, setBusy] = useState<Busy>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [loadingList, setLoadingList] = useState(true)

  const refresh = useCallback(async () => {
    const list = await listIncidents()
    setIncidents(list)
    return list
  }, [])

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null))
  }, [])

  useEffect(() => {
    // Mount-time fetch: an effect is the right tool here because this synchronises
    // React with the API. The guard avoids setting state after an unmount.
    let active = true

    listIncidents()
      .then((list) => {
        if (active) {
          setIncidents(list)
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setListError(message(error))
        }
      })
      .finally(() => {
        if (active) {
          setLoadingList(false)
        }
      })

    return () => {
      active = false
    }
  }, [])

  // Derived rather than stored: with nothing pinned, the most recent incident is
  // selected. That keeps the initial view correct without a state write in an
  // effect, and it survives the list being refetched.
  const selectedId = pinnedId ?? incidents[0]?.incident_id ?? null
  const selected = incidents.find((i) => i.incident_id === selectedId) ?? null

  // Analysis and learning results belong to one incident. Switching incidents
  // must not leave the previous incident's evidence on screen.
  function select(id: string) {
    setPinnedId(id)
    setAnalysis(null)
    setLearnResult(null)
    setActionError(null)
  }

  async function handleCreate(service: string, description: string, severity: Severity) {
    setBusy('create')
    setActionError(null)
    try {
      const created: IncidentCreate = await createIncident({ service, description, severity })
      await refresh()
      setPinnedId(created.incident_id)
      setAnalysis(null)
      setLearnResult(null)
    } catch (error) {
      setActionError(message(error))
      throw error
    } finally {
      setBusy(null)
    }
  }

  async function handleAnalyze() {
    if (!selected) {
      return
    }
    setBusy('analyze')
    setActionError(null)
    try {
      setAnalysis(await analyzeIncident(selected.incident_id))
    } catch (error) {
      setActionError(message(error))
    } finally {
      setBusy(null)
    }
  }

  async function handleApprove(approved: boolean) {
    if (!selected) {
      return
    }
    setBusy('approve')
    setActionError(null)
    try {
      const updated = await approveIncident(selected.incident_id, approved)
      setIncidents((current) =>
        current.map((i) => (i.incident_id === updated.incident_id ? updated : i)),
      )
    } catch (error) {
      setActionError(message(error))
    } finally {
      setBusy(null)
    }
  }

  async function handleResolve(payload: ResolveRequest) {
    if (!selected) {
      return
    }
    setBusy('resolve')
    setActionError(null)
    try {
      const updated = await resolveIncident(selected.incident_id, payload)
      setIncidents((current) =>
        current.map((i) => (i.incident_id === updated.incident_id ? updated : i)),
      )
    } catch (error) {
      setActionError(message(error))
      throw error
    } finally {
      setBusy(null)
    }
  }

  async function handleLearn() {
    if (!selected) {
      return
    }
    setBusy('learn')
    setActionError(null)
    setLearnResult(null)
    try {
      setLearnResult(await learnIncident(selected.incident_id))
    } catch (error) {
      setActionError(message(error))
    } finally {
      setBusy(null)
    }
  }

  const otherActionError =
    busy === 'create' ? null : (actionError ?? null)

  // One derivation feeds both the loop and the timeline, so the two can never
  // disagree about how far the incident has actually progressed.
  const workflow = deriveWorkflow(selected, analysis, learnResult)
  const learned = learnResult?.learned === true || learnResult?.already_learned === true

  return (
    <AppShell>
      <header className="topbar">
        <div className="topbar__identity">
          <h1 className="topbar__title">AI Incident Response Agent</h1>
          <p className="topbar__subtitle">
            Learn from past incidents. Improve future responses with Hindsight.
          </p>
          <p className="topbar__badge">
            <span aria-hidden="true">🧠 </span>Powered by Hindsight Memory
          </p>
        </div>
        <div className="topbar__health">
          <span
            className={`health health--${health ? 'ok' : 'down'}`}
            title={health ? 'API reachable' : 'API unreachable'}
            role="status"
          >
            <span className="health__dot" aria-hidden="true" />
            {health ? 'API online' : 'API offline'}
          </span>
        </div>
      </header>

      <Split>
        <div className="column column--side">
          <CreateIncidentForm
            onCreate={handleCreate}
            busy={busy === 'create'}
            error={otherActionError}
          />

          {loadingList ? (
            <Card>
              <Spinner label="Loading incidents…" />
            </Card>
          ) : listError ? (
            <ErrorNote title="Could not load incidents" message={listError} />
          ) : (
            <IncidentList
              incidents={incidents}
              selectedId={selectedId}
              onSelect={select}
            />
          )}
        </div>

        <div className="column column--main">
          {!selected ? (
            <Card>
              <CardHeader title="No incident selected" />
              <p className="memory__none">
                File an incident, or pick one from the list.
              </p>
            </Card>
          ) : (
            <>
              <LearningLoop incident={selected} workflow={workflow} />

              <Card>
                <IncidentHeader incident={selected} />

                <button
                  className="button button--primary button--analyze"
                  type="button"
                  disabled={busy !== null}
                  onClick={handleAnalyze}
                >
                  {busy === 'analyze' ? 'Analyzing…' : 'Analyze with Hindsight + Groq'}
                </button>
                <p className="button--analyze-hint">
                  Recalls past experiences from Hindsight long-term memory, then
                  reasons over them with Groq. The result is advisory.
                </p>
              </Card>

              {analysis ? (
                <AnalysisPanel
                  analysis={analysis}
                  loading={busy === 'analyze'}
                  memories={analysis.supporting_memories}
                  currentService={selected.service}
                />
              ) : (
                <AnalysisPrompt incident={selected} />
              )}

              <Card>
                <CardHeader title="Incident Timeline" />
                <Timeline
                  incident={selected}
                  analyzed={analysis !== null}
                  learned={learned}
                />
              </Card>

              <DecisionPanel
                incident={selected}
                onApprove={() => handleApprove(true)}
                onReject={() => handleApprove(false)}
                busy={busy === 'approve'}
                error={busy === 'approve' ? null : otherActionError}
              />

              <ResolvePanel
                incident={selected}
                onResolve={handleResolve}
                busy={busy === 'resolve'}
                error={busy === 'resolve' ? null : otherActionError}
              />

              <LearnPanel
                incident={selected}
                onLearn={handleLearn}
                busy={busy === 'learn'}
                error={busy === 'learn' ? null : otherActionError}
                result={learnResult}
              />
            </>
          )}
        </div>
      </Split>
    </AppShell>
  )
}

export default App
