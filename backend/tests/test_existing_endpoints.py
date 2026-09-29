"""Regression tests for the endpoints that existed before the approval workflow.

Phase A added one endpoint. These tests exist to prove it did not disturb the other
five: the read endpoints, the recall path, and the advisory analyse path, including
the guarantee that analyse never writes back to the incident.
"""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Incident


async def fetch(session: AsyncSession, incident_id: str) -> Incident:
    result = await session.execute(
        select(Incident).where(Incident.incident_id == incident_id)
    )
    return result.scalar_one()


# --- health ---------------------------------------------------------------


async def test_health(client: httpx.AsyncClient):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- create ---------------------------------------------------------------


async def test_create_incident(client: httpx.AsyncClient):
    response = await client.post(
        "/incidents",
        json={
            "service": "checkout",
            "description": "checkout returns 500",
            "severity": "critical",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["incident_id"].startswith("INC-")
    assert body["service"] == "checkout"
    assert body["severity"] == "critical"
    assert body["status"] == "open"
    assert body["root_cause"] is None
    assert body["memory_retained"] is True


async def test_create_defaults_severity_to_unknown(client: httpx.AsyncClient):
    response = await client.post(
        "/incidents", json={"service": "x", "description": "y"}
    )

    assert response.status_code == 201
    assert response.json()["severity"] == "unknown"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"service": "", "description": "d"},
        {"service": "s", "description": ""},
        {"service": "s"},
        {"description": "d"},
    ],
)
async def test_create_rejects_bad_payload(client: httpx.AsyncClient, payload):
    response = await client.post("/incidents", json=payload)
    assert response.status_code == 422


async def test_create_generates_unique_ids(client: httpx.AsyncClient):
    first = (await client.post("/incidents", json={"service": "a", "description": "a"})).json()
    second = (await client.post("/incidents", json={"service": "b", "description": "b"})).json()

    assert first["incident_id"] != second["incident_id"]


async def test_create_retains_memory(client: httpx.AsyncClient):
    await client.post("/incidents", json={"service": "cart", "description": "cart broken"})

    assert len(client.hindsight.retained) == 1
    assert "cart" in client.hindsight.retained[0]


# --- list / get -----------------------------------------------------------


async def test_list_incidents(client: httpx.AsyncClient):
    await client.post("/incidents", json={"service": "one", "description": "1"})
    await client.post("/incidents", json={"service": "two", "description": "2"})

    response = await client.get("/incidents")

    assert response.status_code == 200
    services = {i["service"] for i in response.json()}
    assert {"one", "two"} <= services


async def test_list_empty_is_empty_list(client: httpx.AsyncClient):
    response = await client.get("/incidents")

    assert response.status_code == 200
    assert response.json() == []


async def test_get_incident(client: httpx.AsyncClient, incident: dict):
    response = await client.get(f"/incidents/{incident['incident_id']}")

    assert response.status_code == 200
    assert response.json()["incident_id"] == incident["incident_id"]


async def test_get_unknown_incident_404(client: httpx.AsyncClient):
    response = await client.get("/incidents/INC-NOPE")

    assert response.status_code == 404


# --- recall ---------------------------------------------------------------


async def test_recall_returns_past_experience(client: httpx.AsyncClient, incident: dict):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/recall", json={"query": "503 database"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["incident_id"] == incident["incident_id"]
    assert body["count"] == 1
    assert body["results"][0]["source"] == "hindsight"
    assert body["results"][0]["verification"] == "unverified"
    assert "past experience" in body["notice"].lower()


async def test_recall_unknown_incident_404(client: httpx.AsyncClient):
    response = await client.post("/incidents/INC-NOPE/recall", json={"query": "q"})

    assert response.status_code == 404


@pytest.mark.parametrize("payload", [{}, {"query": ""}, {"query": "   "}])
async def test_recall_rejects_bad_query(client: httpx.AsyncClient, incident: dict, payload):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/recall", json=payload
    )
    assert response.status_code == 422


async def test_recall_does_not_modify_incident(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    await client.post(
        f"/incidents/{incident['incident_id']}/recall", json={"query": "database timeouts"}
    )

    stored = await fetch(session, incident["incident_id"])
    assert stored.status == "open"
    assert stored.root_cause is None
    assert stored.resolution is None
    assert stored.outcome is None


# --- analyze --------------------------------------------------------------


async def test_analyze_returns_advisory_output(
    client: httpx.AsyncClient, incident: dict
):
    response = await client.post(f"/incidents/{incident['incident_id']}/analyze")

    assert response.status_code == 200
    body = response.json()
    assert body["incident_id"] == incident["incident_id"]
    assert body["analysis"] == "Stubbed analysis for tests."
    assert body["likely_root_cause"] is None
    assert body["confidence"] == "low"
    assert "model" in body


async def test_analyze_takes_no_body(client: httpx.AsyncClient, incident: dict):
    """Still a bodyless POST, as before this phase."""
    response = await client.post(f"/incidents/{incident['incident_id']}/analyze", json={})

    assert response.status_code == 200


async def test_analyze_unknown_incident_404(client: httpx.AsyncClient):
    response = await client.post("/incidents/INC-NOPE/analyze")

    assert response.status_code == 404


async def test_analyze_does_not_modify_incident(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    await client.post(f"/incidents/{incident['incident_id']}/analyze")

    stored = await fetch(session, incident["incident_id"])
    assert stored.status == "open"
    assert stored.root_cause is None
    assert stored.resolution is None
    assert stored.outcome is None


async def test_analyze_recalls_with_incident_details(
    client: httpx.AsyncClient, incident: dict
):
    await client.post(f"/incidents/{incident['incident_id']}/analyze")

    assert len(client.hindsight.recalled) == 1
    query = client.hindsight.recalled[0]
    assert incident["service"] in query
    assert "503" in query


async def test_analyze_excludes_its_own_memory(
    client: httpx.AsyncClient, incident: dict
):
    """The current incident's report must not be offered as evidence for itself."""
    response = await client.post(f"/incidents/{incident['incident_id']}/analyze")

    memories = response.json()["supporting_memories"]
    assert all(incident["incident_id"] not in m["text"] for m in memories)


# --- overall surface ------------------------------------------------------


async def test_exact_operation_surface(client: httpx.AsyncClient):
    """Phase A adds exactly one operation and removes nothing."""
    spec = (await client.get("/openapi.json")).json()
    ops = sorted(
        (method.upper(), path)
        for path, methods in spec["paths"].items()
        for method in methods
    )

    assert ops == sorted(
        [
            ("GET", "/health"),
            ("GET", "/incidents"),
            ("POST", "/incidents"),
            ("GET", "/incidents/{incident_id}"),
            ("POST", "/incidents/{incident_id}/recall"),
            ("POST", "/incidents/{incident_id}/analyze"),
            ("POST", "/incidents/{incident_id}/approve"),
            ("POST", "/incidents/{incident_id}/resolve"),
            ("POST", "/incidents/{incident_id}/learn"),
        ]
    )
