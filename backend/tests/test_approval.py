"""Tests for POST /incidents/{incident_id}/approve.

Covers the four things the endpoint promises: it finds the incident, 404s when it
cannot, flips status on a decision, and changes nothing else. The "changes nothing
else" checks read the row back out of Postgres rather than trusting the response
body, because the stored row is what actually matters.
"""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Incident


async def fetch(session: AsyncSession, incident_id: str) -> Incident:
    """Re-read the row from Postgres, bypassing the identity map.

    `populate_existing` matters here: the same session is reused across a request
    that commits elsewhere, so a cached instance would report stale columns.
    """
    result = await session.execute(
        select(Incident)
        .where(Incident.incident_id == incident_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


# --- happy path -----------------------------------------------------------


@pytest.mark.parametrize(
    ("approved", "expected_status"),
    [(True, "approved"), (False, "rejected")],
)
async def test_approve_sets_status(
    client: httpx.AsyncClient, incident: dict, approved, expected_status
):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/approve", json={"approved": approved}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["incident_id"] == incident["incident_id"]
    assert body["status"] == expected_status
    assert body["approved"] is approved
    assert body["previous_status"] == "open"


async def test_approve_persists_status(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    await client.post(f"/incidents/{incident['incident_id']}/approve", json={"approved": True})

    stored = await fetch(session, incident["incident_id"])
    assert stored.status == "approved"


async def test_approval_visible_on_read(
    client: httpx.AsyncClient, incident: dict
):
    """The new status must survive a round trip through the read endpoints."""
    await client.post(f"/incidents/{incident['incident_id']}/approve", json={"approved": False})

    one = await client.get(f"/incidents/{incident['incident_id']}")
    assert one.status_code == 200
    assert one.json()["status"] == "rejected"

    listed = await client.get("/incidents")
    assert listed.status_code == 200
    statuses = {i["incident_id"]: i["status"] for i in listed.json()}
    assert statuses[incident["incident_id"]] == "rejected"


async def test_approve_returns_full_incident(client: httpx.AsyncClient, incident: dict):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/approve", json={"approved": True}
    )

    body = response.json()
    for field in (
        "id",
        "incident_id",
        "service",
        "description",
        "severity",
        "status",
        "root_cause",
        "resolution",
        "outcome",
        "created_at",
        "updated_at",
        "previous_status",
        "approved",
    ):
        assert field in body, f"missing {field}"


# --- 404 ------------------------------------------------------------------


async def test_approve_unknown_incident_404(client: httpx.AsyncClient):
    response = await client.post("/incidents/INC-DOES-NOT-EXIST/approve", json={"approved": True})

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_approve_empty_id_404(client: httpx.AsyncClient):
    response = await client.post("/incidents//approve", json={"approved": True})
    assert response.status_code == 404


async def test_approve_is_case_sensitive_on_id(client: httpx.AsyncClient, incident: dict):
    lowered = incident["incident_id"].lower()
    if lowered == incident["incident_id"]:
        pytest.skip("generated id has no case to vary")

    response = await client.post(f"/incidents/{lowered}/approve", json={"approved": True})
    assert response.status_code == 404


# --- validation -----------------------------------------------------------


async def test_approve_requires_the_field(client: httpx.AsyncClient, incident: dict):
    """A missing `approved` must not default to a decision."""
    response = await client.post(f"/incidents/{incident['incident_id']}/approve", json={})

    assert response.status_code == 422


@pytest.mark.parametrize("value", ["true", "false", "yes", 1, 0, None, [], {}])
async def test_approve_rejects_non_boolean(
    client: httpx.AsyncClient, incident: dict, value
):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/approve", json={"approved": value}
    )
    assert response.status_code == 422, f"accepted {value!r}"


async def test_approve_rejects_blank_string(client: httpx.AsyncClient, incident: dict):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/approve", json={"approved": "   "}
    )
    assert response.status_code == 422


async def test_approve_rejects_extra_fields(client: httpx.AsyncClient, incident: dict):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/approve",
        json={"approved": True, "status": "approved", "root_cause": "injected"},
    )
    assert response.status_code == 422


async def test_approve_rejects_non_object_body(client: httpx.AsyncClient, incident: dict):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/approve", json=True
    )
    assert response.status_code == 422


async def test_rejected_validation_leaves_status_open(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    await client.post(f"/incidents/{incident['incident_id']}/approve", json={"approved": "maybe"})

    stored = await fetch(session, incident["incident_id"])
    assert stored.status == "open"


# --- the endpoint must not touch root_cause / resolution / outcome ---------


async def test_approve_does_not_set_root_cause(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/approve", json={"approved": True}
    )

    assert response.json()["root_cause"] is None
    stored = await fetch(session, incident["incident_id"])
    assert stored.root_cause is None


async def test_approve_does_not_set_resolution_or_outcome(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    await client.post(f"/incidents/{incident['incident_id']}/approve", json={"approved": True})

    stored = await fetch(session, incident["incident_id"])
    assert stored.resolution is None
    assert stored.outcome is None


async def test_approve_preserves_existing_conclusion_fields(
    client: httpx.AsyncClient, session: AsyncSession
):
    """Pre-existing values must be preserved, not cleared or overwritten."""
    created = await client.post(
        "/incidents",
        json={"service": "billing", "description": "billing lag", "severity": "low"},
    )
    incident_id = created.json()["incident_id"]

    original = await fetch(session, incident_id)
    original.root_cause = "operator-confirmed cause"
    original.resolution = "operator-confirmed fix"
    original.outcome = "recovered"
    await session.commit()

    await client.post(f"/incidents/{incident_id}/approve", json={"approved": True})

    # The client runs on its own session; expire this one's snapshot so the
    # assertions below observe the row as it is after the approval.
    session.expire_all()
    stored = await fetch(session, incident_id)
    assert stored.status == "approved"
    assert stored.root_cause == "operator-confirmed cause"
    assert stored.resolution == "operator-confirmed fix"
    assert stored.outcome == "recovered"


async def test_approve_leaves_other_fields_alone(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    await client.post(f"/incidents/{incident['incident_id']}/approve", json={"approved": True})

    stored = await fetch(session, incident["incident_id"])
    assert stored.service == incident["service"]
    assert stored.description == incident["description"]
    assert stored.severity == incident["severity"]
    assert stored.created_at is not None


async def test_reject_does_not_set_conclusion_fields(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    await client.post(f"/incidents/{incident['incident_id']}/approve", json={"approved": False})

    stored = await fetch(session, incident["incident_id"])
    assert stored.status == "rejected"
    assert stored.root_cause is None
    assert stored.resolution is None
    assert stored.outcome is None


# --- transitions and idempotency ------------------------------------------


async def test_approve_then_reject(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    await client.post(f"/incidents/{incident['incident_id']}/approve", json={"approved": True})
    second = await client.post(
        f"/incidents/{incident['incident_id']}/approve", json={"approved": False}
    )

    assert second.status_code == 200
    assert second.json()["previous_status"] == "approved"
    assert second.json()["status"] == "rejected"
    assert (await fetch(session, incident["incident_id"])).status == "rejected"


async def test_approve_is_idempotent(client: httpx.AsyncClient, incident: dict):
    first = await client.post(
        f"/incidents/{incident['incident_id']}/approve", json={"approved": True}
    )
    second = await client.post(
        f"/incidents/{incident['incident_id']}/approve", json={"approved": True}
    )

    assert first.json()["status"] == second.json()["status"] == "approved"
    assert second.json()["previous_status"] == "approved"


@pytest.mark.parametrize("start_status", ["open", "investigating", "mitigated", "resolved"])
async def test_approve_from_every_status(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession, start_status
):
    original = await fetch(session, incident["incident_id"])
    original.status = start_status
    await session.commit()

    response = await client.post(
        f"/incidents/{incident['incident_id']}/approve", json={"approved": True}
    )

    assert response.status_code == 200
    assert response.json()["previous_status"] == start_status
    assert response.json()["status"] == "approved"


# --- isolation ------------------------------------------------------------


async def test_approve_only_touches_one_incident(
    client: httpx.AsyncClient, session: AsyncSession
):
    first = (
        await client.post("/incidents", json={"service": "a", "description": "a is down"})
    ).json()
    second = (
        await client.post("/incidents", json={"service": "b", "description": "b is down"})
    ).json()

    await client.post(f"/incidents/{first['incident_id']}/approve", json={"approved": True})

    assert (await fetch(session, first["incident_id"])).status == "approved"
    assert (await fetch(session, second["incident_id"])).status == "open"


async def test_approve_does_not_write_to_hindsight(
    client: httpx.AsyncClient, incident: dict
):
    """Hindsight is out of scope for this phase: approval must not call it."""
    retained_before = list(client.hindsight.retained)
    recalled_before = list(client.hindsight.recalled)

    await client.post(f"/incidents/{incident['incident_id']}/approve", json={"approved": True})

    assert client.hindsight.retained == retained_before
    assert client.hindsight.recalled == recalled_before


async def test_approve_does_not_call_groq(client: httpx.AsyncClient, incident: dict):
    calls_before = client.groq.calls

    await client.post(f"/incidents/{incident['incident_id']}/approve", json={"approved": True})

    assert client.groq.calls == calls_before == 0


# --- OpenAPI --------------------------------------------------------------


async def test_approve_is_documented(client: httpx.AsyncClient):
    spec = (await client.get("/openapi.json")).json()

    assert "/incidents/{incident_id}/approve" in spec["paths"]
    op = spec["paths"]["/incidents/{incident_id}/approve"]["post"]
    assert "IncidentApprovalRequest" in str(op)
    assert "IncidentApprovalResponse" in str(op)
