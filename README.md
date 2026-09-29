# AI Incident Response Agent

An agent that triages incoming alerts, recalls how similar incidents were resolved
before, proposes a remediation playbook, and learns from the outcome of each one.

Built with FastAPI, React, PostgreSQL, Groq, and Hindsight.

## MVP scope

| In scope | Deliberately out of scope for now |
| --- | --- |
| FastAPI backend | MITRE ATT&CK mapping |
| React frontend | IOC extraction |
| PostgreSQL for incident data | WebSockets / live streaming |
| Hindsight for long-term memory | Redis / job queue |
| Groq for AI reasoning | Alert correlation |
| Human approval workflow | Postmortem generation |

## Architecture

```
React (Vite)  ──REST──>  FastAPI  ──>  Groq          (reasoning)
     :5173      /api     :8000    ──>  Hindsight     (memory, :8888)
                                ──>  PostgreSQL     (incident data, :5432)
```

Full design notes in [docs/architecture.md](docs/architecture.md).

## Prerequisites

- Python 3.12+
- Node 20+
- Docker Desktop (for the Postgres container)

## Setup

**1. Backend**

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1         # Windows PowerShell
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API at http://localhost:8000, interactive docs at http://localhost:8000/docs.

**2. Frontend**

```bash
cd frontend
npm install
npm run dev
```

UI at http://localhost:5173.

## Verifying the setup

- http://localhost:8000/health returns `{"status":"ok"}`
- http://localhost:8000/docs lists the API surface

## Scripts

| Location | Command | Does |
| --- | --- | --- |
| `backend` | `uvicorn app.main:app --reload` | Run the API |
| `frontend` | `npm run dev` | Run the UI |
| `frontend` | `npm run build` | Type-check and build |
| `frontend` | `npm run lint` | Lint (oxlint) |

## Roadmap

1. Incident/Alert models + Alembic migrations
2. Alert ingestion endpoint and seed data
3. Groq triage with structured output
4. Hindsight recall of prior incidents
5. Remediation playbook generation
6. Human approval workflow in the UI
