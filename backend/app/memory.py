"""Hindsight long-term memory.

CONTRACT
--------
Retain
    Only the factual fields an incident actually has at report time: incident id,
    service, description, severity, status, and when it was reported. Nothing is
    summarised, derived, or inferred. Root cause, resolution, outcome, and
    duration are unknown at this point and are recorded *as unknown*, because a
    silent omission reads as an oversight and gets filled in.

Recall
    Results are past experiences, never facts about the current incident. Hindsight
    presents a stored report as if it were a causal explanation, so the framing
    here and in the API response is deliberate: a recalled memory describes a
    different, earlier incident and its details do not transfer.

    The service's own extraction and consolidation layers will synthesise causal
    narrative that nobody wrote, so the bank is configured below to suppress that
    rather than relying on prompt wording alone.
"""

from functools import lru_cache

import aiohttp
from hindsight_client import Hindsight
from hindsight_client import RecallResponse, RetainResponse

from app.config import settings

# Frames the recall layer: report only what was stored, and admit when it is absent.
BANK_REFLECT_MISSION = (
    "This bank holds verbatim incident reports. Each entry was captured at report "
    "time and records only the incident id, service, description, severity, status, "
    "and report time. "
    "Entries do not contain a root cause, resolution, outcome, outage duration, or "
    "any link to a deployment, because none of that was known when the report was "
    "written. Those fields are recorded as 'not determined at time of report'. "
    "Never state a cause, a fix, or a duration for an incident unless an entry "
    "literally contains that text. Never merge separate entries into one causal "
    "story, and never present a past incident as an explanation of a current one. "
    "When the bank does not contain the answer, say so plainly."
)

# Steers extraction if the extraction mode is ever changed away from verbatim.
BANK_RETAIN_MISSION = (
    "Store only facts that are explicitly present in the submitted text. Do not "
    "add causes, fixes, durations, severities, or context that are not written "
    "there. Do not interpret the description as a diagnosis."
)

# Verbatim extraction stores the submitted text without an LLM rewriting it, which
# removes the most direct path to invented detail.
BANK_RETAIN_EXTRACTION_MODE = "verbatim"

# Observations are the service's synthesised narrative layer. They are what turned
# a thin report into a story about a 20-minute outage, a rollback, and a 30-second
# pool timeout. Off means no narrative consolidation.
BANK_ENABLE_OBSERVATIONS = False

# Auto-consolidation folds retrieved memories into a derived mental model, which is
# another place unverified inference accumulates.
BANK_ENABLE_AUTO_CONSOLIDATION = False

# High skepticism plus high literalism pushes recall toward quoted source text
# rather than inference. The service accepts an integer from 1 to 5 and rejects
# anything else, so 5 is the maximum, not an arbitrary large number.
BANK_DISPOSITION_SKEPTICISM = 5
BANK_DISPOSITION_LITERALISM = 5

# ---------------------------------------------------------------------------
# CONFIRMED EXPERIENCE
#
# The report below is an unverified capture taken when an incident was filed. The
# confirmed experience below is a different kind of record: it is written only after
# a human has recorded a root cause, a resolution, and an outcome in Postgres, and
# it is marked as such in the text itself so the two can never be confused when
# they are recalled side by side.
# ---------------------------------------------------------------------------

# Distinguishes a confirmed experience from an incident report. Recall surfaces both
# kinds from the same bank, and only this marker separates them.
# The marker line that identifies a confirmed experience inside a stored memory.
# Kept as a full `key: value` line so classification can match it exactly, rather
# than on a loose substring that ordinary report text might also contain.
LEARNED_EXPERIENCE_MARKER = "experience_type: confirmed_resolution"

# Prepended to a confirmed experience. States where the three fields came from, so
# the retrieval layer does not have to guess whether they were reported or verified.
LEARNED_EXPERIENCE_HEADER = (
    "Confirmed incident resolution.\n"
    f"{LEARNED_EXPERIENCE_MARKER}\n"
    "verification: confirmed by human review and stored in the incident record\n"
    "The root cause, resolution, and outcome below were recorded by a person who "
    "investigated this incident. They are not inferred, not summarised, and not "
    "generated. This is historical experience from one specific past incident; it "
    "is not a diagnosis of any current incident."
)

# Attached to every recall response so the framing travels with the data and cannot
# be separated from it by a client that only reads the results array.
PAST_EXPERIENCE_NOTICE = (
    "Past experience / historical evidence. These items were retrieved by semantic "
    "similarity from the Hindsight memory bank. They describe other, earlier "
    "incidents and are NOT confirmed facts about this incident. Any root cause, "
    "resolution, outcome, duration, or deployment link they mention belongs to a "
    "different incident. Some entries are unverified reports taken when an incident "
    "was filed; others are confirmed resolutions a person later verified. Either "
    "way they describe a past incident, not this one. Verify independently before "
    "acting, and never copy these values into the current incident."
)


class HindsightNotConfiguredError(RuntimeError):
    """Raised when Hindsight credentials are missing from the environment."""


class HindsightRequestError(RuntimeError):
    """Raised when a call to the Hindsight service fails.

    Wraps the transport error so callers never have to import aiohttp, and so the
    message can stay free of anything sensitive. Only the exception type name and
    HTTP status are carried; the upstream response body is deliberately discarded.
    """

    def __init__(self, operation: str, error: BaseException) -> None:
        self.operation = operation
        self.error_type = type(error).__name__
        self.status = getattr(error, "status", None)
        super().__init__(f"Hindsight {operation} failed ({self.error_type})")


def _wrap(operation: str):
    def decorator(coroutine):
        async def wrapper(*args, **kwargs):
            try:
                return await coroutine(*args, **kwargs)
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise HindsightRequestError(operation, exc) from exc

        return wrapper

    return decorator


@lru_cache
def get_client() -> Hindsight:
    """Return a Hindsight client, or raise if credentials are not configured."""
    if settings.hindsight_api_key is None or settings.hindsight_bank_id is None:
        raise HindsightNotConfiguredError(
            "Set HINDSIGHT_API_KEY and HINDSIGHT_BANK_ID in backend/.env"
        )

    return Hindsight(
        base_url=settings.hindsight_api_url,
        api_key=settings.hindsight_api_key.get_secret_value(),
    )


def get_bank_id() -> str:
    if settings.hindsight_bank_id is None:
        raise HindsightNotConfiguredError("Set HINDSIGHT_BANK_ID in backend/.env")
    return settings.hindsight_bank_id


async def create_bank(client: Hindsight, bank_id: str) -> None:
    """Create the bank with the settings that suppress inference."""
    await client.acreate_bank(
        bank_id=bank_id,
        name=bank_id,
        reflect_mission=BANK_REFLECT_MISSION,
        retain_mission=BANK_RETAIN_MISSION,
        retain_extraction_mode=BANK_RETAIN_EXTRACTION_MODE,
        enable_observations=BANK_ENABLE_OBSERVATIONS,
    )
    await configure_bank(client, bank_id)


async def configure_bank(client: Hindsight, bank_id: str) -> None:
    """Apply the anti-fabrication settings to an existing bank.

    Separate from ensure_bank because it is an explicit write: the settings are
    worth applying once at setup, not on every recall.
    """
    await client.aupdate_bank_config(
        bank_id=bank_id,
        retain_extraction_mode=BANK_RETAIN_EXTRACTION_MODE,
        enable_observations=BANK_ENABLE_OBSERVATIONS,
        enable_auto_consolidation=BANK_ENABLE_AUTO_CONSOLIDATION,
        reflect_mission=BANK_REFLECT_MISSION,
        retain_mission=BANK_RETAIN_MISSION,
        disposition_skepticism=BANK_DISPOSITION_SKEPTICISM,
        disposition_literalism=BANK_DISPOSITION_LITERALISM,
    )


async def ensure_bank(client: Hindsight, bank_id: str) -> None:
    """Create the bank if it does not exist yet.

    Reads first and only writes on 404, so an existing bank keeps its stored
    memories and configuration instead of being reset on every call.
    """
    try:
        await client.aget_bank_config(bank_id)
    except aiohttp.ClientResponseError as exc:
        if exc.status != 404:
            raise
        await create_bank(client, bank_id)


@_wrap("retain")
async def retain_experience(
    client: Hindsight, bank_id: str, content: str
) -> RetainResponse:
    await ensure_bank(client, bank_id)
    return await client.aretain(bank_id=bank_id, content=content)


@_wrap("recall")
async def recall_experiences(
    client: Hindsight, bank_id: str, query: str
) -> RecallResponse:
    await ensure_bank(client, bank_id)
    return await client.arecall(bank_id=bank_id, query=query)


def build_retained_experience(incident) -> str:
    """Render the factual record of an incident for retention.

    Only fields that exist on the incident are included. The three undetermined
    fields are written out explicitly rather than omitted: the memory has to say
    they are unknown, or the retrieval layer reads the gap as something to fill.
    """
    reported_at = incident.created_at.isoformat() if incident.created_at else "unknown"

    return (
        "Incident report.\n"
        f"incident_id: {incident.incident_id}\n"
        f"service: {incident.service}\n"
        f"severity: {incident.severity}\n"
        f"status: {incident.status}\n"
        f"reported_at: {reported_at}\n"
        f"description: {incident.description}\n"
        "root_cause: not determined at time of report\n"
        "resolution: not determined at time of report\n"
        "outcome: not determined at time of report"
    )


# Marker used to recognise a confirmed experience when scanning the bank.
_LEARNED_TYPE_LINE = LEARNED_EXPERIENCE_MARKER
_INCIDENT_ID_LINE = "incident_id: "


def _has_confirmed_value(value: object) -> bool:
    """True only when Postgres holds a real, non-blank confirmed value."""
    return isinstance(value, str) and bool(value.strip())


def missing_confirmed_fields(incident) -> list[str]:
    """Name the confirmed fields that Postgres does not hold yet.

    Used to refuse learning a resolved-looking incident whose conclusion was never
    actually written down. An empty string counts as missing: it records nothing.
    """
    return [
        field
        for field in ("root_cause", "resolution", "outcome")
        if not _has_confirmed_value(getattr(incident, field, None))
    ]


def build_learned_experience(incident) -> str:
    """Render a resolved incident as confirmed experience, for retention.

    Every value below is read straight off the incident row. Nothing is derived,
    reworded, or filled in from a template, so the text cannot assert a fact that
    Postgres does not hold. `build_retained_experience` is the deliberate contrast:
    it writes "not determined" for exactly these three fields.

    The caller must check `missing_confirmed_fields` first. This function renders
    whatever is there, so an unvalidated call would happily write "None".
    """
    return (
        f"{LEARNED_EXPERIENCE_HEADER}\n"
        f"{_INCIDENT_ID_LINE}{incident.incident_id}\n"
        f"service: {incident.service}\n"
        f"severity: {incident.severity}\n"
        f"status: {incident.status}\n"
        f"reported_at: {incident.created_at.isoformat() if incident.created_at else 'unknown'}\n"
        f"original_description: {incident.description}\n"
        f"confirmed_root_cause: {incident.root_cause}\n"
        f"confirmed_resolution: {incident.resolution}\n"
        f"confirmed_outcome: {incident.outcome}"
    )


# The only value a confirmed experience can carry, derived from the marker line so
# the classification and the stored text cannot drift apart.
CONFIRMED_EXPERIENCE_TYPE = "confirmed_resolution"

# Returned for anything that is not a confirmed experience: a report taken at
# incident time, or a memory written by something other than this service.
UNVERIFIED_EXPERIENCE_TYPE = "incident_report"


def classify_memory(text: str | None) -> tuple[str, str]:
    """Read a recalled memory and report (verification, experience_type).

    The classification comes only from the marker line present in the stored text,
    never from how similar the memory is to the current incident. Semantic
    similarity is what selected the memory, not evidence about its provenance, so
    treating relevance as verification would let an ordinary incident report be
    presented as a confirmed outcome.

    Returns:
        ("confirmed", "confirmed_resolution") for a memory carrying the marker,
        ("unverified", "incident_report") for everything else, including text that
        is empty, unrecognised, or written by another tool.
    """
    if not text:
        return "unverified", UNVERIFIED_EXPERIENCE_TYPE

    for line in text.splitlines():
        if line.strip() == _LEARNED_TYPE_LINE:
            return "confirmed", CONFIRMED_EXPERIENCE_TYPE

    return "unverified", UNVERIFIED_EXPERIENCE_TYPE


def prioritize_confirmed_memories(memories: list) -> list:
    """Order recalled memories so confirmed experiences come first.

    Retrieval ranks by similarity alone, so an unverified report that closely
    echoes the current wording can outrank a genuinely confirmed experience.
    A person verified the latter and not the former, so it should be read first
    both in the prompt and in the API response.

    Stable within each group: retrieval order still decides between items of equal
    standing, since it is the only relevance signal available.
    """
    return [
        memory
        for memory in memories
        if classify_memory(getattr(memory, "text", None))[0] == "confirmed"
    ] + [
        memory
        for memory in memories
        if classify_memory(getattr(memory, "text", None))[0] != "confirmed"
    ]


def _parse_learned_id(text: str | None) -> str | None:
    """Pull the incident id out of a confirmed-experience memory, if it is one."""
    if not text or _LEARNED_TYPE_LINE not in text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(_INCIDENT_ID_LINE):
            return stripped[len(_INCIDENT_ID_LINE) :].strip()
    return None


_SERVICE_LINE = "service: "


def _parse_field(text: str | None, prefix: str) -> str | None:
    """Pull one `key: value` line out of a stored memory, if present."""
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix) :].strip()
            return value or None
    return None


def parse_memory_service(text: str | None) -> str | None:
    """Return the service a memory was written about, when the text carries one."""
    return _parse_field(text, _SERVICE_LINE)


def parse_memory_incident_id(text: str | None) -> str | None:
    """Return the originating incident id for any memory that carries one.

    Applies to reports and confirmed experiences alike. A memory from another tool
    may have no id at all, which the caller has to tolerate.
    """
    return _parse_field(text, _INCIDENT_ID_LINE)


def learn_document_id(incident_id: str) -> str:
    """Deterministic Hindsight document id for an incident's confirmed experience.

    Derived from the incident id alone, so every /learn call for the same incident
    targets the same document. Paired with `update_mode="replace"` this makes the
    retain idempotent server-side: a repeat call overwrites the same document
    instead of appending a second copy.
    """
    return f"learned-{incident_id}"


@_wrap("list_memories")
async def find_learned_memory(client: Hindsight, bank_id: str, incident_id: str):
    """Return the stored confirmed experience for this incident, or None.

    Scans the bank rather than trusting a local flag, because Hindsight is the
    system of record for what has been learned. Only memories carrying the
    confirmed-experience marker are considered, so an unverified incident report
    for the same id is never mistaken for a learned one.
    """
    response = await client.alist_memories(bank_id)
    target = _normalise(incident_id)

    for item in response.items:
        if _normalise(_parse_learned_id(item.text) or "") == target:
            return item

    return None


@_wrap("retain_learned")
async def retain_learned_experience(
    client: Hindsight, bank_id: str, content: str, incident_id: str
) -> RetainResponse:
    """Store one confirmed experience under a deterministic document id.

    Uses the same client and bank as every other memory write, so there is still
    only one Hindsight configuration in this service.
    """
    await ensure_bank(client, bank_id)
    return await client.aretain(
        bank_id=bank_id,
        content=content,
        document_id=learn_document_id(incident_id),
        update_mode="replace",
    )


def build_recall_query(incident) -> str:
    """Build a Hindsight query from the incident's own fields.

    Deliberately limited to service, severity, and description. Adding the
    incident's status or id would bias recall toward the incident's own memory
    instead of genuinely similar past ones.
    """
    return (
        f"Past incidents affecting {incident.service} with severity "
        f"{incident.severity}. Reported symptoms: {incident.description}"
    )


def _normalise(text: str) -> str:
    """Collapse whitespace and lowercase, so matching ignores formatting."""
    return " ".join(str(text).split()).lower()


def exclude_current_incident(memories: list, incident) -> list:
    """Drop recalled memories that describe the incident being analysed.

    Recall is bank-wide and this incident's own retained report is usually the
    closest match to its own query, so it comes back first. Left in place it
    would be handed to the model as though it were independent historical
    evidence for a conclusion already recorded in the current incident.

    Matching relies on the incident id appearing in the memory text, which holds
    for everything this service writes because the retained record leads with
    `incident_id:`. A memory written by some other tool might not carry the id
    and would survive this filter.
    """
    incident_id = _normalise(incident.incident_id)
    description = _normalise(incident.description)

    kept = []
    for memory in memories:
        text = _normalise(getattr(memory, "text", ""))
        if incident_id and incident_id in text:
            continue
        if description and description in text:
            continue
        kept.append(memory)

    return kept
