"""Phase D.1: the model must tell confirmed experience apart from plain reports.

The live run showed a new incident successfully recalling a previous
confirmed_resolution memory. What was missing was any distinction in the prompt
between the two kinds of memory, so a human-verified past root cause and a bare
symptom report were read as the same kind of evidence.

These tests are deterministic. They never call Groq. They assert on what the
route handed to the model and on the prompt text, so the guarantees hold without
depending on what a model happens to reply.
"""

from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.groq import format_memories
from app.models import Incident

# A past incident a person actually investigated, recorded via /learn.
CONFIRMED_MEMORY = (
    "Confirmed incident resolution.\n"
    "experience_type: confirmed_resolution\n"
    "incident_id: INC-20260928-0434FE\n"
    "service: payment-api\n"
    "severity: high\n"
    "status: resolved\n"
    "original_description: Payment API returning 503 errors because database "
    "connections were timing out.\n"
    "confirmed_root_cause: Database connection pool was exhausted.\n"
    "confirmed_resolution: Increased the database connection pool size and "
    "restarted the affected API instances.\n"
    "confirmed_outcome: Payment requests recovered successfully."
)

# A first report. Its cause was never established, and it says so.
REPORT_MEMORY = (
    "Incident report.\n"
    "incident_id: INC-20260928-05F1B1\n"
    "service: payment-api\n"
    "severity: high\n"
    "status: open\n"
    "reported_at: 2026-09-28T12:27:38+00:00\n"
    "description: Payment API is intermittently returning 503 errors.\n"
    "root_cause: not determined at time of report\n"
    "resolution: not determined at time of report\n"
    "outcome: not determined at time of report"
)


def memory(text: str) -> SimpleNamespace:
    """A recalled memory as Hindsight hands one over, for direct unit tests."""
    return SimpleNamespace(text=text)


def user_prompt_from(stub) -> str:
    """Pull the user message out of a recorded request."""
    return next(m["content"] for m in stub.messages if m["role"] == "user")


def memories_section(prompt: str) -> str:
    """Isolate the recalled-memories block of the prompt.

    The prompt names the current incident in its own header, so an exclusion
    check has to look only at what was presented as historical evidence.
    """
    _, _, after = prompt.partition("## PAST EXPERIENCES FROM HINDSIGHT")
    block, _, _ = after.partition("## TASK")
    return block


# --- what the model is told about each kind of memory ------------------------


async def test_confirmed_memory_reaches_groq_labelled_confirmed(
    client: httpx.AsyncClient, incident: dict
):
    """A confirmed experience is passed to Groq with verification=confirmed."""
    client.hindsight.recall_results = [CONFIRMED_MEMORY]

    response = await client.post(f"/incidents/{incident['incident_id']}/analyze")
    assert response.status_code == 200

    text = " ".join(user_prompt_from(client.groq).split())
    assert "verification=confirmed" in text
    assert "experience_type=confirmed_resolution" in text
    # The confirmed values themselves must reach the model, or it cannot use them.
    assert "Database connection pool was exhausted" in text
    assert "Increased the database connection pool size" in text
    assert "Payment requests recovered successfully" in text


def test_confirmed_values_keep_their_field_labels():
    """The stored line structure must survive into the prompt.

    A memory flattened into one run of prose loses the `key: value` labels, and the
    model then paraphrases "a cause was found" instead of reporting the cause that
    was actually confirmed.
    """
    rendered = format_memories([memory(CONFIRMED_MEMORY)])

    assert "confirmed_root_cause: Database connection pool was exhausted." in rendered
    assert (
        "confirmed_resolution: Increased the database connection pool size"
        in rendered
    )
    assert "confirmed_outcome: Payment requests recovered successfully." in rendered


async def test_report_memory_reaches_groq_labelled_unverified(
    client: httpx.AsyncClient, incident: dict
):
    """A plain report is passed to Groq with verification=unverified."""
    client.hindsight.recall_results = [REPORT_MEMORY]

    response = await client.post(f"/incidents/{incident['incident_id']}/analyze")
    assert response.status_code == 200

    text = " ".join(user_prompt_from(client.groq).split())
    assert "verification=unverified" in text
    assert "experience_type=incident_report" in text


async def test_both_kinds_are_serialised_with_full_metadata(
    client: httpx.AsyncClient, incident: dict
):
    """Every memory carries incident_id, service, verification and type."""
    client.hindsight.recall_results = [CONFIRMED_MEMORY, REPORT_MEMORY]

    await client.post(f"/incidents/{incident['incident_id']}/analyze")

    prompt = user_prompt_from(client.groq)
    assert "incident_id=INC-20260928-0434FE" in prompt
    assert "incident_id=INC-20260928-05F1B1" in prompt
    assert "service=payment-api" in prompt
    assert "verification=confirmed" in prompt
    assert "verification=unverified" in prompt
    # The memory body itself is sent, not only the label.
    assert "Confirmed incident resolution." in prompt
    assert "Incident report." in prompt


async def test_prompt_teaches_the_difference_between_the_two_labels(
    client: httpx.AsyncClient, incident: dict
):
    """The instructions must define both labels, not merely print them."""
    client.hindsight.recall_results = [CONFIRMED_MEMORY]

    await client.post(f"/incidents/{incident['incident_id']}/analyze")

    prompt = user_prompt_from(client.groq)
    assert "verification=confirmed" in prompt
    assert "verification=unverified" in prompt
    # Confirmed means a person established cause, resolution and outcome.
    assert "confirmed for THAT incident" in prompt
    # Unverified means those three were never established.
    assert "NOT established" in prompt
    # A confirmed experience must outrank a report.
    assert "more weight" in prompt


# --- prioritisation ----------------------------------------------------------


def test_confirmed_memories_are_listed_before_reports():
    """Ordering carries the weighting, so it is pinned here."""
    rendered = format_memories(
        [memory(REPORT_MEMORY), memory(CONFIRMED_MEMORY)]
    )

    confirmed_at = rendered.index("INC-20260928-0434FE")
    report_at = rendered.index("INC-20260928-05F1B1")
    assert confirmed_at < report_at


def test_prioritisation_survives_a_report_outranking_by_similarity():
    """The report is retrieved first, as similarity alone would order it."""
    rendered = format_memories(
        [memory(REPORT_MEMORY), memory(REPORT_MEMORY), memory(CONFIRMED_MEMORY)]
    )

    assert rendered.index("INC-20260928-0434FE") < rendered.index("INC-20260928-05F1B1")


def test_prioritisation_is_stable_within_each_group():
    """Grouping must not shuffle items against retrieval order."""
    other = REPORT_MEMORY.replace("INC-20260928-05F1B1", "INC-20260928-999999")

    rendered = format_memories([memory(REPORT_MEMORY), memory(other)])

    assert rendered.index("INC-20260928-05F1B1") < rendered.index("INC-20260928-999999")


async def test_route_orders_confirmed_first_in_the_response_too(
    client: httpx.AsyncClient, incident: dict
):
    """The same ordering the prompt uses is reflected in the API response."""
    client.hindsight.recall_results = [REPORT_MEMORY, CONFIRMED_MEMORY]

    response = await client.post(f"/incidents/{incident['incident_id']}/analyze")

    memories = response.json()["supporting_memories"]
    assert [m["experience_type"] for m in memories] == [
        "confirmed_resolution",
        "incident_report",
    ]
    assert memories[0]["verification"] == "confirmed"


def test_very_long_memory_is_truncated_without_losing_its_head():
    """A fixed prompt budget still has to hold; the labels come first."""
    long_memory = CONFIRMED_MEMORY + "\n" + ("padding line that is not useful. " * 60)
    rendered = format_memories([memory(long_memory)])

    assert "verification=confirmed" in rendered
    assert "confirmed_root_cause: Database connection pool was exhausted." in rendered
    assert "[...]" in rendered


def test_no_memories_still_renders_a_placeholder():
    """The prompt must not claim evidence exists when nothing was recalled."""
    assert "no past experiences were retrieved" in format_memories([])


# --- current incident stays out, and stays read-only -------------------------


async def test_current_incident_is_excluded_from_the_prompt(
    client: httpx.AsyncClient, incident: dict
):
    """Even a confirmed memory about this incident is a self-match, dropped."""
    own = CONFIRMED_MEMORY.replace(
        "INC-20260928-0434FE", incident["incident_id"]
    ).replace(
        "service: payment-api", f"service: {incident['service']}"
    ).replace(
        "original_description: Payment API returning 503 errors because database "
        "connections were timing out.",
        f"original_description: {incident['description']}",
    )
    client.hindsight.recall_results = [own, CONFIRMED_MEMORY]

    response = await client.post(f"/incidents/{incident['incident_id']}/analyze")
    assert response.status_code == 200

    evidence = memories_section(user_prompt_from(client.groq))
    assert incident["incident_id"] not in evidence
    assert "INC-20260928-0434FE" in evidence


async def test_current_incident_report_is_excluded(client: httpx.AsyncClient, incident: dict):
    """The incident's own retained report is not evidence for itself."""
    own_report = REPORT_MEMORY.replace(
        "INC-20260928-05F1B1", incident["incident_id"]
    ).replace(
        "description: Payment API is intermittently returning 503 errors.",
        f"description: {incident['description']}",
    )
    client.hindsight.recall_results = [own_report]

    response = await client.post(f"/incidents/{incident['incident_id']}/analyze")

    assert response.json()["supporting_memories"] == []


async def test_a_genuinely_different_report_is_kept(
    client: httpx.AsyncClient, incident: dict
):
    """Exclusion is not over-broad: another incident's report is still evidence."""
    client.hindsight.recall_results = [REPORT_MEMORY]

    response = await client.post(f"/incidents/{incident['incident_id']}/analyze")

    memories = response.json()["supporting_memories"]
    assert len(memories) == 1
    assert memories[0]["verification"] == "unverified"


# --- advisory only -----------------------------------------------------------


async def test_confirmed_history_is_never_written_to_the_current_incident(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    """The historical conclusion must not become this incident's conclusion.

    This is the guarantee that matters most: a confirmed past root cause is the
    most tempting value in the whole system to copy across, and it must stay in
    Hindsight as history.
    """
    client.hindsight.recall_results = [CONFIRMED_MEMORY]

    response = await client.post(f"/incidents/{incident['incident_id']}/analyze")
    assert response.status_code == 200

    stored = (
        await session.execute(
            select(Incident).where(Incident.incident_id == incident["incident_id"])
        )
    ).scalar_one()
    assert stored.status == "open"
    assert stored.root_cause is None
    assert stored.resolution is None
    assert stored.outcome is None


async def test_analyze_stays_advisory_with_a_confirmed_match(
    client: httpx.AsyncClient, incident: dict, session: AsyncSession
):
    """A strong historical match still yields advice and no write-back."""
    client.hindsight.recall_results = [CONFIRMED_MEMORY]

    response = await client.post(f"/incidents/{incident['incident_id']}/analyze")
    body = response.json()

    assert set(body) == {
        "analysis",
        "likely_root_cause",
        "recommended_resolution",
        "confidence",
        "incident_id",
        "supporting_memories",
        "model",
    }
    stored = (
        await session.execute(
            select(Incident).where(Incident.incident_id == incident["incident_id"])
        )
    ).scalar_one()
    assert stored.root_cause is None
    assert stored.resolution is None
    assert stored.outcome is None


async def test_analyze_writes_nothing_to_hindsight(
    client: httpx.AsyncClient, incident: dict
):
    """Analysing must not add to or change the memory bank."""
    client.hindsight.recall_results = [CONFIRMED_MEMORY]
    stored_before = {doc: m.text for doc, m in client.hindsight.store.items()}

    await client.post(f"/incidents/{incident['incident_id']}/analyze")

    assert {doc: m.text for doc, m in client.hindsight.store.items()} == stored_before
