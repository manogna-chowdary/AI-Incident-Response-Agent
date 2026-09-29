"""Tests for POST /incidents/{incident_id}/learn, and for how it is recalled.

The memory this endpoint writes becomes evidence that other, later incidents get
reasoned against, so the tests concentrate on what the text does and does not say:

* it is built only from the stored row,
* it is marked as confirmed experience rather than an unverified report,
* a non-resolved or conclusion-free incident is never sent at all,
* the same incident is not stored twice,
* and on the way back out, recall reports the stored marker faithfully.
Hindsight is stubbed, so the assertions read exactly what would have been written
rather than trusting a return value.
"""

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory import LEARNED_EXPERIENCE_MARKER, build_learned_experience

# The report /learn is contrasted against: unverified, captured at incident time.
REPORT_MEMORY = (
    "Incident report.\n"
    "incident_id: INC-OLD-000001\n"
    "service: payment-api\n"
    "severity: high\n"
    "status: open\n"
    "reported_at: 2026-09-28T09:00:00+00:00\n"
    "description: Search API latency spike\n"
    "root_cause: not determined at time of report\n"
    "resolution: not determined at time of report\n"
    "outcome: not determined at time of report"
)

# Exactly the shape /learn writes, so recall is tested against realistic content.
CONFIRMED_MEMORY = (
    "Confirmed incident resolution.\n"
    "experience_type: confirmed_resolution\n"
    "verification: confirmed by human review and stored in the incident record\n"
    "The root cause, resolution, and outcome below were recorded by a person who "
    "investigated this incident. They are not inferred, not summarised, and not "
    "generated. This is historical experience from one specific past incident; it "
    "is not a diagnosis of any current incident.\n"
    "incident_id: INC-OLD-000001\n"
    "service: payment-api\n"
    "severity: high\n"
    "status: resolved\n"
    "reported_at: 2026-09-28T09:00:00+00:00\n"
    "original_description: Checkout timeouts after a config rollout\n"
    "confirmed_root_cause: Database connection pool was exhausted.\n"
    "confirmed_resolution: Increased the pool size and restarted the API instances.\n"
    "confirmed_outcome: Payment requests recovered successfully."
)

RESOLUTION = {
    "root_cause": "Database connection pool was exhausted.",
    "resolution": "Increased the pool size and restarted the affected API instances.",
    "outcome": "Payment requests recovered successfully.",
}


async def resolved(client: httpx.AsyncClient, **overrides) -> dict:
    """A created incident with a confirmed resolution recorded, as Phase B does."""
    created = await client.post(
        "/incidents",
        json={
            "service": "payment-api",
            "description": "Payment API returning 503s",
            "severity": "high",
        },
    )
    incident_id = created.json()["incident_id"]
    await client.post(
        f"/incidents/{incident_id}/resolve",
        json={**RESOLUTION, **overrides},
    )
    return created.json()


def field_of(text: str, key: str) -> str:
    """Read one `key: value` line out of a retained memory."""
    prefix = f"{key}:"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    raise AssertionError(f"{key!r} missing from:\n{text}")


# --- a learned memory is recalled as confirmed ----------------------------


async def test_learned_memory_is_recalled_as_confirmed(
    client: httpx.AsyncClient,
):
    """A: a confirmed_resolution memory reports verification='confirmed'."""
    incident = await resolved(client)

    await client.post(f"/incidents/{incident['incident_id']}/learn")
    response = await client.post(
        f"/incidents/{incident['incident_id']}/recall", json={"query": "payment 503"}
    )

    assert response.status_code == 200, response.text
    learned = [
        m for m in response.json()["results"] if m["experience_type"] == "confirmed_resolution"
    ]
    assert len(learned) == 1
    assert learned[0]["verification"] == "confirmed"
    assert "INC-" + incident["incident_id"][4:] in learned[0]["text"]


async def test_learned_memory_reports_experience_type(client: httpx.AsyncClient):
    """B: the response exposes experience_type alongside verification."""
    incident = await resolved(client)
    await client.post(f"/incidents/{incident['incident_id']}/learn")

    response = await client.post(
        f"/incidents/{incident['incident_id']}/recall", json={"query": "payment 503"}
    )

    learned = [
        m for m in response.json()["results"] if m["verification"] == "confirmed"
    ]
    assert len(learned) == 1
    assert learned[0]["experience_type"] == "confirmed_resolution"


async def test_incident_report_stays_unverified(client: httpx.AsyncClient, incident: dict):
    """C: an ordinary report is still unverified, even though it is in the bank."""
    response = await client.post(
        f"/incidents/{incident['incident_id']}/recall", json={"query": "payment 503"}
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results
    for item in results:
        if "not determined at time of report" in item["text"]:
            assert item["verification"] == "unverified"
            assert item["experience_type"] == "incident_report"


async def test_recall_mixes_both_kinds_correctly(
    client: httpx.AsyncClient, incident: dict
):
    """A report and a confirmed experience can surface together and be told apart."""
    client.hindsight.recall_results = [REPORT_MEMORY, CONFIRMED_MEMORY]

    response = await client.post(
        f"/incidents/{incident['incident_id']}/recall", json={"query": "database pool"}
    )

    by_type = {m["experience_type"]: m for m in response.json()["results"]}
    assert by_type["incident_report"]["verification"] == "unverified"
    assert by_type["confirmed_resolution"]["verification"] == "confirmed"


async def test_similarity_alone_does_not_confirm(client: httpx.AsyncClient, incident: dict):
    """A memory that merely sounds like a confirmed experience stays unverified."""
    client.hindsight.recall_results = [
        "This is very similar to a confirmed resolution of a connection pool issue."
    ]

    response = await client.post(
        f"/incidents/{incident['incident_id']}/recall", json={"query": "pool"}
    )

    item = response.json()["results"][0]
    assert item["verification"] == "unverified"
    assert item["experience_type"] == "incident_report"


async def test_marker_quoted_in_prose_is_not_a_marker(
    client: httpx.AsyncClient, incident: dict
):
    """A memory that quotes the marker inside a sentence is not a confirmed one."""
    client.hindsight.recall_results = [
        'note: the operator later wrote "experience_type: confirmed_resolution" here'
    ]

    response = await client.post(
        f"/incidents/{incident['incident_id']}/recall", json={"query": "pool"}
    )

    assert response.json()["results"][0]["verification"] == "unverified"


async def test_current_incident_still_excluded_from_confirmed_memories(
    client: httpx.AsyncClient, incident: dict
):
    """D: exclusion still holds on /analyze, even for a confirmed memory.

    A learned experience about *this* incident is a self-match like any other, and
    is dropped before the model sees it.
    """
    own = (
        "Confirmed incident resolution.\n"
        "experience_type: confirmed_resolution\n"
        f"incident_id: {incident['incident_id']}\n"
        f"service: {incident['service']}\n"
        f"original_description: {incident['description']}\n"
        "confirmed_root_cause: pool exhausted"
    )
    client.hindsight.recall_results = [own, CONFIRMED_MEMORY]

    response = await client.post(f"/incidents/{incident['incident_id']}/analyze")

    assert response.status_code == 200
    memories = response.json()["supporting_memories"]
    assert all(incident["incident_id"] not in m["text"] for m in memories)
    assert len(memories) == 1
    assert memories[0]["verification"] == "confirmed"


async def test_recall_endpoint_still_returns_raw_results(
    client: httpx.AsyncClient, incident: dict
):
    """D (2): /recall is unchanged and does not filter.

    Exclusion is /analyze's job, and it is left exactly as it was; this test pins
    that so the classification change did not quietly move it.
    """
    own = (
        "Confirmed incident resolution.\n"
        "experience_type: confirmed_resolution\n"
        f"incident_id: {incident['incident_id']}\n"
        f"service: {incident['service']}\n"
        f"original_description: {incident['description']}\n"
        "confirmed_root_cause: pool exhausted"
    )
    client.hindsight.recall_results = [own, CONFIRMED_MEMORY]

    response = await client.post(
        f"/incidents/{incident['incident_id']}/recall", json={"query": "payment 503"}
    )

    assert response.status_code == 200
    assert response.json()["count"] == 2


async def test_analyze_supporting_memories_carry_classification(
    client: httpx.AsyncClient, incident: dict
):
    """The same classification reaches /analyze, so the two endpoints agree."""
    client.hindsight.recall_results = [REPORT_MEMORY, CONFIRMED_MEMORY]

    response = await client.post(f"/incidents/{incident['incident_id']}/analyze")

    assert response.status_code == 200
    by_type = {m["experience_type"]: m for m in response.json()["supporting_memories"]}
    assert by_type["confirmed_resolution"]["verification"] == "confirmed"
    assert by_type["incident_report"]["verification"] == "unverified"


async def test_analyze_still_does_not_copy_confirmed_values_into_incident(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    """Recall framing is advisory: a confirmed past result must not be written here."""
    from app.models import Incident
    from sqlalchemy import select

    client.hindsight.recall_results = [CONFIRMED_MEMORY]

    await client.post(f"/incidents/{incident['incident_id']}/analyze")

    result = await session.execute(
        select(Incident)
        .where(Incident.incident_id == incident["incident_id"])
        .execution_options(populate_existing=True)
    )
    row = result.scalar_one()
    assert row.status == "open"
    assert row.root_cause is None
    assert row.resolution is None
    assert row.outcome is None


async def test_every_recalled_item_has_both_fields(client: httpx.AsyncClient, incident: dict):
    """The fields are required, not defaulted, so neither can silently go missing."""
    client.hindsight.recall_results = [REPORT_MEMORY, CONFIRMED_MEMORY, "a bare memory"]

    response = await client.post(
        f"/incidents/{incident['incident_id']}/recall", json={"query": "database"}
    )

    for item in response.json()["results"]:
        assert item["verification"] in ("confirmed", "unverified")
        assert item["experience_type"] in ("confirmed_resolution", "incident_report")
        assert item["source"] == "hindsight"


async def test_recall_classification_is_documented(client: httpx.AsyncClient):
    spec = (await client.get("/openapi.json")).json()
    schema = spec["components"]["schemas"]["PastExperience"]

    assert "experience_type" in schema["properties"]
    for field in ("verification", "experience_type"):
        assert field in schema["required"]


# --- classifier unit tests ------------------------------------------------


def test_classify_memory_marker_variants():
    from app.memory import classify_memory

    for text in (
        CONFIRMED_MEMORY,
        f"prefix\n{CONFIRMED_MEMORY}",
        "  experience_type: confirmed_resolution  ",
    ):
        assert classify_memory(text) == ("confirmed", "confirmed_resolution")


def test_classify_memory_non_markers():
    from app.memory import classify_memory

    for text in (
        REPORT_MEMORY,
        "",
        None,
        "confirmed resolution of a similar issue",
        "experience_type: unknown",
        "experience_type:confirmed_resolution",
    ):
        assert classify_memory(text) == ("unverified", "incident_report")


# --- learning the incident ------------------------------------------------


async def test_learn_succeeds(client: httpx.AsyncClient):
    incident = await resolved(client)

    response = await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["incident_id"] == incident["incident_id"]
    assert body["status"] == "resolved"
    assert body["learned"] is True
    assert body["already_learned"] is False
    assert body["memory_retained"] is True
    assert body["document_id"] == f"learned-{incident['incident_id']}"


async def test_learn_writes_one_memory(client: httpx.AsyncClient):
    incident = await resolved(client)
    before = len(client.hindsight.retained)

    await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert len(client.hindsight.retained) == before + 1


async def test_learned_memory_contains_every_required_field(
    client: httpx.AsyncClient,
):
    incident = await resolved(client)

    await client.post(f"/incidents/{incident['incident_id']}/learn")

    text = client.hindsight.retained[-1]
    assert field_of(text, "incident_id") == incident["incident_id"]
    assert field_of(text, "service") == "payment-api"
    assert field_of(text, "severity") == "high"
    assert field_of(text, "status") == "resolved"
    assert field_of(text, "original_description") == "Payment API returning 503s"
    assert field_of(text, "confirmed_root_cause") == RESOLUTION["root_cause"]
    assert field_of(text, "confirmed_resolution") == RESOLUTION["resolution"]
    assert field_of(text, "confirmed_outcome") == RESOLUTION["outcome"]


async def test_learned_memory_has_a_verification_marker(client: httpx.AsyncClient):
    incident = await resolved(client)

    await client.post(f"/incidents/{incident['incident_id']}/learn")

    text = client.hindsight.retained[-1]
    assert LEARNED_EXPERIENCE_MARKER in text
    assert "verification: confirmed" in text


async def test_learned_memory_is_distinct_from_an_unverified_report(
    client: httpx.AsyncClient,
):
    """A confirmed experience must not read like a report filed at incident time."""
    incident = await resolved(client)
    await client.post(f"/incidents/{incident['incident_id']}/learn")
    learned = client.hindsight.retained[-1]
    report = client.hindsight.retained[0]

    assert learned.startswith("Confirmed incident resolution.")
    assert report.startswith("Incident report.")
    assert LEARNED_EXPERIENCE_MARKER not in report
    assert "not determined at time of report" in report
    assert "not determined" not in learned


async def test_learned_memory_does_not_say_undetermined(client: httpx.AsyncClient):
    incident = await resolved(client)

    await client.post(f"/incidents/{incident['incident_id']}/learn")

    text = client.hindsight.retained[-1]
    assert "not determined" not in text
    assert "None" not in text


async def test_learned_memory_adds_nothing_beyond_the_row(client: httpx.AsyncClient):
    """No inferred detail: nothing may appear that Postgres does not hold."""
    incident = await resolved(client)

    await client.post(f"/incidents/{incident['incident_id']}/learn")

    text = client.hindsight.retained[-1]
    # Prose that would only exist if something had been invented.
    for invented in (
        "minutes",
        "hours",
        "rolled back",
        "deployed",
        "rollback",
        "postmortem",
        "SLO",
        "prevented",
        "because the",
    ):
        assert invented not in text, f"inferred detail leaked into memory: {invented}"


async def test_learned_memory_preserves_multiline_confirmed_values(
    client: httpx.AsyncClient,
):
    resolution = "1. Raised pool size to 200\n2. Restarted api-1..4\n3. Verified"

    incident = await resolved(client, resolution=resolution)
    await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert resolution in client.hindsight.retained[-1]


async def test_learn_uses_deterministic_document_id(client: httpx.AsyncClient):
    incident = await resolved(client)

    await client.post(f"/incidents/{incident['incident_id']}/learn")

    call = client.hindsight.retain_calls[-1]
    assert call["document_id"] == f"learned-{incident['incident_id']}"
    assert call["update_mode"] == "replace"


async def test_learn_does_not_modify_the_incident(
    client: httpx.AsyncClient, session: AsyncSession
):
    """Reading and storing must leave the row byte-for-byte unchanged."""
    from app.models import Incident
    from sqlalchemy import select

    incident = await resolved(client)
    incident_id = incident["incident_id"]

    async def snapshot():
        result = await session.execute(
            select(Incident)
            .where(Incident.incident_id == incident_id)
            .execution_options(populate_existing=True)
        )
        row = result.scalar_one()
        return (
            row.status,
            row.root_cause,
            row.resolution,
            row.outcome,
            row.created_at,
            row.updated_at,
        )

    before = await snapshot()
    await client.post(f"/incidents/{incident_id}/learn")
    assert await snapshot() == before


async def test_learn_does_not_call_groq(client: httpx.AsyncClient):
    incident = await resolved(client)
    calls_before = client.groq.calls

    await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert client.groq.calls == calls_before == 0


async def test_learn_does_not_recall(client: httpx.AsyncClient):
    """Learning only writes. It must not depend on a recall round trip."""
    incident = await resolved(client)
    recalled_before = list(client.hindsight.recalled)

    await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert client.hindsight.recalled == recalled_before


# --- unknown incident -----------------------------------------------------


async def test_learn_unknown_incident_404(client: httpx.AsyncClient):
    response = await client.post("/incidents/INC-DOES-NOT-EXIST/learn")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_learn_unknown_incident_writes_nothing(client: httpx.AsyncClient):
    before = len(client.hindsight.retained)

    await client.post("/incidents/INC-NOPE/learn")

    assert len(client.hindsight.retained) == before


# --- only resolved incidents ---------------------------------------------


@pytest.mark.parametrize("status", ["open", "investigating", "mitigated", "approved"])
async def test_learn_rejects_non_resolved_status(
    client: httpx.AsyncClient, session: AsyncSession, status
):
    created = await client.post(
        "/incidents", json={"service": "s", "description": "d", "severity": "low"}
    )
    incident_id = created.json()["incident_id"]

    from app.models import Incident
    from sqlalchemy import select

    result = await session.execute(
        select(Incident).where(Incident.incident_id == incident_id)
    )
    row = result.scalar_one()
    row.status = status
    await session.commit()
    session.expire_all()

    response = await client.post(f"/incidents/{incident_id}/learn")

    assert response.status_code == 400
    assert status in response.json()["detail"]


async def test_learn_rejects_approved_even_with_conclusion_fields(
    client: httpx.AsyncClient, session: AsyncSession
):
    """An approved incident is not evidence. Approval is not confirmation."""
    from app.models import Incident
    from sqlalchemy import select

    incident = await resolved(client)
    result = await session.execute(
        select(Incident).where(Incident.incident_id == incident["incident_id"])
    )
    row = result.scalar_one()
    row.status = "approved"
    await session.commit()
    session.expire_all()

    response = await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert response.status_code == 400


async def test_learn_rejects_rejected_incident(client: httpx.AsyncClient, session: AsyncSession):
    from app.models import Incident
    from sqlalchemy import select

    incident = await resolved(client)
    result = await session.execute(
        select(Incident).where(Incident.incident_id == incident["incident_id"])
    )
    row = result.scalar_one()
    row.status = "rejected"
    await session.commit()
    session.expire_all()

    assert (await client.post(f"/incidents/{incident['incident_id']}/learn")).status_code == 400


@pytest.mark.parametrize("status", ["open", "investigating", "mitigated", "approved", "rejected"])
async def test_non_resolved_incident_is_never_sent_to_hindsight(
    client: httpx.AsyncClient, session: AsyncSession, status
):
    from app.models import Incident
    from sqlalchemy import select

    created = await client.post(
        "/incidents", json={"service": "s", "description": "d"}
    )
    incident_id = created.json()["incident_id"]
    result = await session.execute(
        select(Incident).where(Incident.incident_id == incident_id)
    )
    row = result.scalar_one()
    row.status = status
    await session.commit()
    session.expire_all()

    before = len(client.hindsight.retained)
    await client.post(f"/incidents/{incident_id}/learn")

    assert len(client.hindsight.retained) == before


# --- missing or blank confirmed values -----------------------------------


async def test_learn_rejects_resolved_without_conclusion(
    client: httpx.AsyncClient, session: AsyncSession
):
    """Status can read 'resolved' while the row holds nothing: refuse to learn it."""
    from app.models import Incident
    from sqlalchemy import select

    created = await client.post("/incidents", json={"service": "s", "description": "d"})
    incident_id = created.json()["incident_id"]
    result = await session.execute(
        select(Incident).where(Incident.incident_id == incident_id)
    )
    row = result.scalar_one()
    row.status = "resolved"
    await session.commit()
    session.expire_all()

    response = await client.post(f"/incidents/{incident_id}/learn")

    assert response.status_code == 400
    detail = response.json()["detail"]
    for field in ("root_cause", "resolution", "outcome"):
        assert field in detail


@pytest.mark.parametrize("missing_field", ["root_cause", "resolution", "outcome"])
async def test_learn_rejects_each_missing_field(
    client: httpx.AsyncClient, session: AsyncSession, missing_field
):
    from app.models import Incident
    from sqlalchemy import select

    incident = await resolved(client)
    result = await session.execute(
        select(Incident).where(Incident.incident_id == incident["incident_id"])
    )
    row = result.scalar_one()
    setattr(row, missing_field, None)
    await session.commit()
    session.expire_all()

    response = await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert response.status_code == 400
    assert missing_field in response.json()["detail"]


@pytest.mark.parametrize("blank", ["", "   ", "\n", "\t", " \n "])
@pytest.mark.parametrize("blank_field", ["root_cause", "resolution", "outcome"])
async def test_learn_rejects_blank_confirmed_values(
    client: httpx.AsyncClient, session: AsyncSession, blank_field, blank
):
    from app.models import Incident
    from sqlalchemy import select

    incident = await resolved(client)
    result = await session.execute(
        select(Incident).where(Incident.incident_id == incident["incident_id"])
    )
    row = result.scalar_one()
    setattr(row, blank_field, blank)
    await session.commit()
    session.expire_all()

    response = await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert response.status_code == 400, f"accepted blank {blank_field}={blank!r}"


async def test_blank_confirmed_value_writes_nothing(
    client: httpx.AsyncClient, session: AsyncSession
):
    from app.models import Incident
    from sqlalchemy import select

    incident = await resolved(client)
    result = await session.execute(
        select(Incident).where(Incident.incident_id == incident["incident_id"])
    )
    row = result.scalar_one()
    row.outcome = "  "
    await session.commit()
    session.expire_all()

    before = len(client.hindsight.retained)
    await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert len(client.hindsight.retained) == before


# --- duplicate protection -------------------------------------------------


async def test_second_learn_is_a_no_op(client: httpx.AsyncClient):
    incident = await resolved(client)

    first = await client.post(f"/incidents/{incident['incident_id']}/learn")
    second = await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert first.status_code == 200
    assert first.json()["learned"] is True
    assert second.status_code == 200
    assert second.json()["learned"] is False
    assert second.json()["already_learned"] is True


async def test_repeated_learn_writes_one_memory(client: httpx.AsyncClient):
    incident = await resolved(client)
    before = len(client.hindsight.retained)

    for _ in range(5):
        await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert len(client.hindsight.retained) == before + 1


async def test_repeated_learn_reuses_one_document_id(client: httpx.AsyncClient):
    incident = await resolved(client)

    await client.post(f"/incidents/{incident['incident_id']}/learn")
    await client.post(f"/incidents/{incident['incident_id']}/learn")

    learned_calls = [
        c for c in client.hindsight.retain_calls if c["document_id"] is not None
    ]
    assert len(learned_calls) == 1
    assert learned_calls[0]["document_id"] == f"learned-{incident['incident_id']}"


async def test_duplicate_check_ignores_unverified_report(
    client: httpx.AsyncClient
):
    """The report retained at creation time must not look like a learned memory.

    Both carry the same incident id; only the confirmed marker should count.
    """
    incident = await resolved(client)

    response = await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert response.json()["learned"] is True
    assert response.json()["already_learned"] is False


async def test_different_incidents_are_learned_separately(client: httpx.AsyncClient):
    first = await resolved(client)
    second = await resolved(client)
    assert first["incident_id"] != second["incident_id"]

    await client.post(f"/incidents/{first['incident_id']}/learn")
    response = await client.post(f"/incidents/{second['incident_id']}/learn")

    assert response.json()["learned"] is True
    assert response.json()["already_learned"] is False


async def test_learn_after_correction_relearns(client: httpx.AsyncClient):
    """A corrected resolution should be learnable, replacing the stale memory."""
    incident = await resolved(client)
    await client.post(f"/incidents/{incident['incident_id']}/learn")

    await client.post(
        f"/incidents/{incident['incident_id']}/resolve",
        json={**RESOLUTION, "outcome": "Recovered; verified clean for 24h."},
    )
    again = await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert again.status_code == 200
    assert again.json()["already_learned"] is True
    assert len(client.hindsight.retained) == 2  # creation report + one experience


# --- Hindsight failure ----------------------------------------------------


async def test_retain_failure_returns_502(client: httpx.AsyncClient):
    import aiohttp

    incident = await resolved(client)
    client.hindsight.retain_error = aiohttp.ClientError("boom")

    response = await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert response.status_code == 502
    assert "hindsight" in response.json()["detail"].lower()


async def test_retain_failure_leaves_incident_untouched(
    client: httpx.AsyncClient, session: AsyncSession
):
    import aiohttp

    from app.models import Incident
    from sqlalchemy import select

    incident = await resolved(client)
    incident_id = incident["incident_id"]
    client.hindsight.retain_error = aiohttp.ClientError("boom")

    await client.post(f"/incidents/{incident_id}/learn")

    result = await session.execute(
        select(Incident)
        .where(Incident.incident_id == incident_id)
        .execution_options(populate_existing=True)
    )
    row = result.scalar_one()
    assert row.status == "resolved"
    assert row.root_cause == RESOLUTION["root_cause"]
    assert row.resolution == RESOLUTION["resolution"]
    assert row.outcome == RESOLUTION["outcome"]


async def test_retain_failure_does_not_delete_the_incident(
    client: httpx.AsyncClient, session: AsyncSession
):
    import aiohttp

    from app.models import Incident
    from sqlalchemy import func, select

    incident = await resolved(client)
    client.hindsight.retain_error = aiohttp.ClientError("boom")

    await client.post(f"/incidents/{incident['incident_id']}/learn")

    count = await session.scalar(select(func.count()).select_from(Incident))
    assert count >= 1


async def test_retain_failure_is_retryable(client: httpx.AsyncClient):
    import aiohttp

    incident = await resolved(client)
    client.hindsight.retain_error = aiohttp.ClientError("boom")
    assert (await client.post(f"/incidents/{incident['incident_id']}/learn")).status_code == 502

    client.hindsight.retain_error = None
    retry = await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert retry.status_code == 200
    assert retry.json()["learned"] is True


async def test_list_failure_returns_502(client: httpx.AsyncClient):
    """A failure while checking for duplicates must not be mistaken for 'not learned'."""
    import aiohttp

    incident = await resolved(client)
    client.hindsight.list_error = aiohttp.ClientError("boom")

    response = await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert response.status_code == 502


async def test_hindsight_failure_message_leaks_nothing(client: httpx.AsyncClient):
    import aiohttp

    incident = await resolved(client)
    client.hindsight.retain_error = aiohttp.ClientError("secret-token-leaked")

    response = await client.post(f"/incidents/{incident['incident_id']}/learn")

    assert "secret-token-leaked" not in response.text
    assert "Traceback" not in response.text


# --- builder is pure ------------------------------------------------------


async def test_builder_isolated_from_resolve_output(client: httpx.AsyncClient, session: AsyncSession):
    """Direct check of the memory builder against a hand-built incident."""
    from app.models import Incident

    incident = Incident(
        incident_id="INC-TEST-1",
        service="cart",
        severity="medium",
        status="resolved",
        description="cart stuck",
        root_cause="cache stampede",
        resolution="added jitter",
        outcome="recovered",
    )
    text = build_learned_experience(incident)

    assert field_of(text, "confirmed_root_cause") == "cache stampede"
    assert field_of(text, "confirmed_resolution") == "added jitter"
    assert field_of(text, "confirmed_outcome") == "recovered"
    assert LEARNED_EXPERIENCE_MARKER in text
    assert "not determined" not in text


# --- OpenAPI --------------------------------------------------------------


async def test_learn_is_documented(client: httpx.AsyncClient):
    spec = (await client.get("/openapi.json")).json()

    assert "/incidents/{incident_id}/learn" in spec["paths"]
    op = spec["paths"]["/incidents/{incident_id}/learn"]["post"]
    assert "IncidentLearnResponse" in str(op)


async def test_learn_takes_no_body(client: httpx.AsyncClient):
    incident = await resolved(client)

    response = await client.post(f"/incidents/{incident['incident_id']}/learn", json={})

    assert response.status_code == 200
