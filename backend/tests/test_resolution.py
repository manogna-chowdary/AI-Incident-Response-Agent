"""Tests for POST /incidents/{incident_id}/resolve.

The three fields written here are the confirmed record of what happened, so the
tests concentrate on two things: the values are stored exactly as given, and
nothing else on the incident is disturbed. Assertions read the row back out of
Postgres rather than trusting the response body.
"""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Incident

RESOLUTION = {
    "root_cause": "Database connection pool was exhausted.",
    "resolution": (
        "Increased the database connection pool size and restarted the "
        "affected API instances."
    ),
    "outcome": "Payment requests recovered successfully.",
}


async def fetch(session: AsyncSession, incident_id: str) -> Incident:
    """Re-read the row from Postgres, bypassing the identity map.

    `populate_existing` matters: the client commits on its own session, so a cached
    instance would report stale columns.
    """
    result = await session.execute(
        select(Incident)
        .where(Incident.incident_id == incident_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


# --- successful resolution ------------------------------------------------


async def test_resolve_succeeds(client: httpx.AsyncClient, incident: dict):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve", json=RESOLUTION
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["incident_id"] == incident["incident_id"]
    assert body["status"] == "resolved"
    assert body["root_cause"] == RESOLUTION["root_cause"]
    assert body["resolution"] == RESOLUTION["resolution"]
    assert body["outcome"] == RESOLUTION["outcome"]
    assert body["previous_status"] == "open"


async def test_resolve_persists_to_database(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    await client.post(f"/incidents/{incident['incident_id']}/resolve", json=RESOLUTION)

    stored = await fetch(session, incident["incident_id"])
    assert stored.status == "resolved"
    assert stored.root_cause == RESOLUTION["root_cause"]
    assert stored.resolution == RESOLUTION["resolution"]
    assert stored.outcome == RESOLUTION["outcome"]


async def test_resolve_returns_full_incident(client: httpx.AsyncClient, incident: dict):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve", json=RESOLUTION
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
    ):
        assert field in body, f"missing {field}"


async def test_resolve_visible_on_read_endpoints(
    client: httpx.AsyncClient, incident: dict
):
    """The stored result must survive a round trip through both read endpoints."""
    await client.post(f"/incidents/{incident['incident_id']}/resolve", json=RESOLUTION)

    one = await client.get(f"/incidents/{incident['incident_id']}")
    assert one.status_code == 200
    assert one.json()["status"] == "resolved"
    assert one.json()["root_cause"] == RESOLUTION["root_cause"]

    listed = await client.get("/incidents")
    match = next(i for i in listed.json() if i["incident_id"] == incident["incident_id"])
    assert match["status"] == "resolved"
    assert match["outcome"] == RESOLUTION["outcome"]


async def test_resolve_preserves_report_fields(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    """Resolving records the outcome; it must not rewrite the original report."""
    await client.post(f"/incidents/{incident['incident_id']}/resolve", json=RESOLUTION)

    stored = await fetch(session, incident["incident_id"])
    assert stored.service == incident["service"]
    assert stored.description == incident["description"]
    assert stored.severity == incident["severity"]
    assert stored.created_at is not None


async def test_resolve_trims_surrounding_whitespace(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    await client.post(
        f"/incidents/{incident['incident_id']}/resolve",
        json={k: f"  {v}  " for k, v in RESOLUTION.items()},
    )

    stored = await fetch(session, incident["incident_id"])
    assert stored.root_cause == RESOLUTION["root_cause"]
    assert stored.resolution == RESOLUTION["resolution"]
    assert stored.outcome == RESOLUTION["outcome"]


async def test_resolve_keeps_multiline_text(client: httpx.AsyncClient, incident: dict):
    """Line breaks inside a confirmed note are legitimate and must survive."""
    resolution = "1. Raised pool size to 200\n2. Restarted api-1..4\n3. Verified recovery"

    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve",
        json={**RESOLUTION, "resolution": resolution},
    )

    assert response.status_code == 200
    assert response.json()["resolution"] == resolution


async def test_resolve_accepts_long_text(client: httpx.AsyncClient, incident: dict):
    """A long confirmed note is legitimate; the columns are Text, not String(n)."""
    outcome = "Recovered. " * 2000

    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve",
        json={**RESOLUTION, "outcome": outcome},
    )

    assert response.status_code == 200
    # The schema trims, so the trailing space is gone.
    assert response.json()["outcome"] == outcome.strip()


# --- missing incident -----------------------------------------------------


async def test_resolve_unknown_incident_404(client: httpx.AsyncClient):
    response = await client.post("/incidents/INC-DOES-NOT-EXIST/resolve", json=RESOLUTION)

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_resolve_empty_id_404(client: httpx.AsyncClient):
    response = await client.post("/incidents//resolve", json=RESOLUTION)
    assert response.status_code == 404


async def test_resolve_unknown_id_with_bad_body(client: httpx.AsyncClient):
    """A malformed body is a 422 even for an unknown id.

    FastAPI validates the request body before the handler runs, so the incident
    lookup never happens. The 404 guarantee applies to a well-formed body, which
    is covered by test_resolve_unknown_incident_404.
    """
    response = await client.post("/incidents/INC-NOPE/resolve", json={})

    assert response.status_code == 422


async def test_resolve_is_case_sensitive_on_id(client: httpx.AsyncClient, incident: dict):
    lowered = incident["incident_id"].lower()
    if lowered == incident["incident_id"]:
        pytest.skip("generated id has no case to vary")

    response = await client.post(f"/incidents/{lowered}/resolve", json=RESOLUTION)
    assert response.status_code == 404


# --- validation -----------------------------------------------------------


@pytest.mark.parametrize("field", ["root_cause", "resolution", "outcome"])
async def test_resolve_requires_every_field(
    client: httpx.AsyncClient, incident: dict, field
):
    body = {k: v for k, v in RESOLUTION.items() if k != field}

    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve", json=body
    )

    assert response.status_code == 422


async def test_resolve_rejects_empty_body(client: httpx.AsyncClient, incident: dict):
    response = await client.post(f"/incidents/{incident['incident_id']}/resolve", json={})

    assert response.status_code == 422


@pytest.mark.parametrize("blank", ["", "   ", "\n", "\t", " \n\t "])
@pytest.mark.parametrize("field", ["root_cause", "resolution", "outcome"])
async def test_resolve_rejects_blank_fields(
    client: httpx.AsyncClient, incident: dict, field, blank
):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve",
        json={**RESOLUTION, field: blank},
    )

    assert response.status_code == 422, f"accepted blank {field}={blank!r}"


async def test_resolve_rejects_all_three_blank(client: httpx.AsyncClient, incident: dict):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve",
        json={"root_cause": " ", "resolution": "  ", "outcome": "\t"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("value", [None, 1, 1.5, True, [], {}, ["a"]])
@pytest.mark.parametrize("field", ["root_cause", "resolution", "outcome"])
async def test_resolve_rejects_non_string_fields(
    client: httpx.AsyncClient, incident: dict, field, value
):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve",
        json={**RESOLUTION, field: value},
    )

    assert response.status_code == 422, f"accepted {field}={value!r}"


async def test_resolve_rejects_extra_fields(client: httpx.AsyncClient, incident: dict):
    """An unknown field is rejected rather than silently ignored, so a typo in a
    field name cannot look like a successful write."""
    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve",
        json={**RESOLUTION, "rootcase": "typo"},
    )

    assert response.status_code == 422


async def test_resolve_rejects_status_override(client: httpx.AsyncClient, incident: dict):
    """Status is set by the endpoint, not taken from the body."""
    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve",
        json={**RESOLUTION, "status": "approved"},
    )

    assert response.status_code == 422


async def test_resolve_rejects_non_object_body(client: httpx.AsyncClient, incident: dict):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve", json="resolved"
    )

    assert response.status_code == 422


@pytest.mark.parametrize("field", ["root_cause", "resolution", "outcome"])
async def test_failed_validation_writes_nothing(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession, field
):
    """A rejected request must not leave a partial record behind."""
    before = await fetch(session, incident["incident_id"])

    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve",
        json={**RESOLUTION, field: "   "},
    )
    assert response.status_code == 422

    after = await fetch(session, incident["incident_id"])
    assert after.status == before.status == "open"
    assert after.root_cause is None
    assert after.resolution is None
    assert after.outcome is None


async def test_unknown_incident_creates_nothing(
    client: httpx.AsyncClient, session: AsyncSession
):
    before = len((await session.execute(select(Incident))).scalars().all())

    await client.post("/incidents/INC-NOPE/resolve", json=RESOLUTION)

    after = len((await session.execute(select(Incident))).scalars().all())
    assert after == before


# --- no Groq, no Hindsight ------------------------------------------------


async def test_resolve_does_not_call_groq(client: httpx.AsyncClient, incident: dict):
    calls_before = client.groq.calls

    await client.post(f"/incidents/{incident['incident_id']}/resolve", json=RESOLUTION)

    assert client.groq.calls == calls_before == 0


async def test_resolve_does_not_write_to_hindsight(
    client: httpx.AsyncClient, incident: dict
):
    retained_before = list(client.hindsight.retained)
    recalled_before = list(client.hindsight.recalled)

    await client.post(f"/incidents/{incident['incident_id']}/resolve", json=RESOLUTION)

    assert client.hindsight.retained == retained_before
    assert client.hindsight.recalled == recalled_before


# --- transitions ----------------------------------------------------------


async def test_resolve_after_approve(client: httpx.AsyncClient, incident: dict):
    """The intended order: approve, then record the confirmed result."""
    await client.post(f"/incidents/{incident['incident_id']}/approve", json={"approved": True})
    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve", json=RESOLUTION
    )

    assert response.status_code == 200
    assert response.json()["previous_status"] == "approved"
    assert response.json()["status"] == "resolved"


@pytest.mark.parametrize(
    "start_status", ["open", "investigating", "mitigated", "approved", "rejected"]
)
async def test_resolve_from_every_status(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession, start_status
):
    """Resolution is allowed from any state, including rejected.

    Rejected is included deliberately: a rejected approval must not make it
    impossible to record what actually happened.
    """
    original = await fetch(session, incident["incident_id"])
    original.status = start_status
    await session.commit()

    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve", json=RESOLUTION
    )

    assert response.status_code == 200
    assert response.json()["previous_status"] == start_status
    assert response.json()["status"] == "resolved"


async def test_resolve_overwrites_a_previous_resolution(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    """Re-resolving is how a mistaken record is corrected."""
    await client.post(f"/incidents/{incident['incident_id']}/resolve", json=RESOLUTION)

    corrected = {
        "root_cause": "Connection pool exhaustion, not a slow query.",
        "resolution": "Raised pool size to 200.",
        "outcome": "Recovered; no errors for 24h.",
    }
    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve", json=corrected
    )

    assert response.status_code == 200
    assert response.json()["previous_status"] == "resolved"
    stored = await fetch(session, incident["incident_id"])
    assert stored.root_cause == corrected["root_cause"]
    assert stored.resolution == corrected["resolution"]
    assert stored.outcome == corrected["outcome"]


async def test_resolve_bumps_updated_at(client: httpx.AsyncClient, incident: dict):
    response = await client.post(
        f"/incidents/{incident['incident_id']}/resolve", json=RESOLUTION
    )

    assert response.json()["updated_at"] > incident["updated_at"]


# --- isolation ------------------------------------------------------------


async def test_resolve_only_touches_one_incident(
    client: httpx.AsyncClient, session: AsyncSession
):
    first = (
        await client.post("/incidents", json={"service": "a", "description": "a is down"})
    ).json()
    second = (
        await client.post("/incidents", json={"service": "b", "description": "b is down"})
    ).json()

    await client.post(f"/incidents/{first['incident_id']}/resolve", json=RESOLUTION)

    assert (await fetch(session, first["incident_id"])).status == "resolved"
    other = await fetch(session, second["incident_id"])
    assert other.status == "open"
    assert other.root_cause is None


async def test_resolve_does_not_modify_approved_flag(
    client: httpx.AsyncClient, incident: dict
):
    """Approving and resolving are separate records of separate decisions."""
    await client.post(f"/incidents/{incident['incident_id']}/approve", json={"approved": False})
    await client.post(f"/incidents/{incident['incident_id']}/resolve", json=RESOLUTION)

    one = await client.get(f"/incidents/{incident['incident_id']}")
    assert one.json()["status"] == "resolved"
    assert one.json()["root_cause"] == RESOLUTION["root_cause"]


# --- OpenAPI --------------------------------------------------------------


async def test_resolve_is_documented(client: httpx.AsyncClient):
    spec = (await client.get("/openapi.json")).json()

    assert "/incidents/{incident_id}/resolve" in spec["paths"]
    op = spec["paths"]["/incidents/{incident_id}/resolve"]["post"]
    assert "IncidentResolveRequest" in str(op)
    assert "IncidentResolveResponse" in str(op)


async def test_resolve_schema_marks_fields_required(client: httpx.AsyncClient):
    """Swagger has to show all three as required, or a client can build a request
    the server will reject."""
    spec = (await client.get("/openapi.json")).json()
    schema_name = spec["paths"]["/incidents/{incident_id}/resolve"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

    schema = spec["components"]["schemas"][schema_name]
    assert set(schema["required"]) == {"root_cause", "resolution", "outcome"}
    assert schema["additionalProperties"] is False
