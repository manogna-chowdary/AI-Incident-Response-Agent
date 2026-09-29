# Architecture

## 1. What the application does

The agent is a first responder for security and operations incidents.

1. An alert arrives.
2. The agent recalls how similar incidents were resolved before.
3. It triages the alert with Groq and assigns a severity.
4. It proposes an ordered remediation playbook.
5. A human approves or rejects each step.
6. When the incident closes, the outcome is written to memory so the next
   similar incident is handled faster and better.

## 2. Request flow

```
POST /api/alerts
      |
      v
[AlertIngestion]  -> store the alert
      |
      v
[MemoryRecall]    -> Hindsight recall(bank, query)  -> prior incidents + runbooks
      |
      v
[Triage]          -> Groq structured output       -> category, severity, reasoning
      |
      v
[Plan]            -> Groq structured output       -> ordered remediation steps
      |
      v
[Persist]         -> PostgreSQL: incidents, actions
      |
      v
PATCH /api/actions/{id}/approve   -> human decision, recorded
      |
      v
POST /api/incidents/{id}/close    -> Hindsight retain(postmortem)
```

## 3. Component responsibilities

| Component | Owns | Talks to |
| --- | --- | --- |
| React frontend | Incident list, incident detail, playbook approval UI | FastAPI over REST |
| FastAPI backend | HTTP contract, orchestration, persistence | Groq, Hindsight, PostgreSQL |
| PostgreSQL | Structured incident state: incidents, alerts, evidence, actions | Backend only |
| Groq | Triage classification and playbook generation | Backend only |
| Hindsight | Long-term memory: past incidents, runbooks, outcomes | Backend only |

The browser never calls Groq or Hindsight directly. Both API keys stay server-side,
and the backend is the single place that decides what gets remembered.

## 4. Technology choices

| Layer | Choice | Reason |
| --- | --- | --- |
| Backend | FastAPI + Uvicorn | Async, and `/docs` is generated for free — useful live in a demo |
| Config | pydantic-settings | Typed settings from environment variables |
| ORM | SQLAlchemy 2.0 async + asyncpg | Async end-to-end |
| Migrations | Alembic (next step) | Schema changes stay reviewable |
| Database | PostgreSQL 16 + pgvector | Relational integrity plus vector search |
| LLM | Groq | Low time-to-first-token, which is what makes the demo feel live |
| Memory | Hindsight | Self-hostable, banked memory, supports Groq as its own LLM |
| Frontend | React 19 + Vite + TypeScript | Fastest iteration during a time-boxed build |
| Local infra | Docker Compose | One command to bring Postgres up |

## 5. Data flow for one alert

1. `POST /api/alerts` writes a row to `alerts` and links it to an `incident`.
2. The memory layer calls Hindsight `recall` on the service's bank and the shared
   runbooks bank, then merges the two result sets.
3. Triage calls Groq with the alert plus the recalled memories, requesting a JSON
   schema response. The output is validated with Pydantic.
4. Planning calls Groq again with the triage result and the recalled runbooks.
5. Evidence and actions are persisted. The UI polls for the incident state.
6. A human approves or rejects each action. The decision is recorded.
7. On close, a postmortem summary is written to Hindsight via `retain`.

## 6. Key decisions

**Two databases, one Postgres server.** Hindsight runs as its own service against
its own database. It must never share `incident_db`, because its internal migrations
would collide with Alembic's.

**One Groq key, two consumers.** The backend calls Groq directly; Hindsight also
uses Groq internally for fact extraction during `retain`. Both need the key, and both
count against the same rate limit.

**`retain` is slow and LLM-backed.** It runs on incident close, never inline in a
request path. Recall is fast enough to sit inside triage.

**Structured output is validated, not trusted.** Every Groq response goes through
`model_validate` with a bounded retry. A raw model string never reaches the UI.

**Memory banks are scoped per service.** One bank per service or environment plus a
shared `soc-runbooks` bank. This mirrors the support-agent-plus-shared-knowledge
pattern, and keeps recalls relevant.

**Hindsight is wrapped in one module.** `app/agent/memory.py` exposes
`recall_similar`, `reflect_on_patterns`, and `retain_incident`. Swapping the memory
backend touches one file.

## 7. Deliberate omissions

Not yet built, and each is a known gap rather than an oversight:

- No Alembic yet — it arrives with the first model.
- No WebSockets — the UI polls. Fine at MVP volume.
- No alert correlation — every alert becomes its own incident.
- No MITRE or IOC extraction — triage is category and severity only.
- No postmortem generation — `retain` gets a structured summary instead.
- No Redis — orchestration is synchronous inside the request.
