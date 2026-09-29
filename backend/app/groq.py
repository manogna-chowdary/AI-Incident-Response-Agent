"""Groq service.

Responsible only for calling the LLM and turning its reply into a validated
object. It performs no database access and holds no incident state: the caller
supplies the incident and the already-recalled memories.

The API key is read from settings at the point of use and never returned, logged,
or attached to an exception message.
"""

import json
import logging
from functools import lru_cache
from typing import Any

import httpx
from groq import AsyncGroq, GroqError

from app.config import settings
from app.memory import (
    classify_memory,
    parse_memory_incident_id,
    parse_memory_service,
    prioritize_confirmed_memories,
)
from app.schemas import AnalysisConfidence, IncidentAnalysis

logger = logging.getLogger(__name__)

# The service name is what the model sees, so it stays generic and reveals nothing
# about the deployment.
SYSTEM_PROMPT = """\
You are an incident response analyst. You examine a current production incident, \
compare it against past experiences retrieved from a long-term memory bank, and \
suggest how it might be resolved.

## CURRENT INCIDENT
Incident id: {incident_id}
Service: {service}
Severity: {severity}
Status: {status}
Description: {description}

The current incident has no determined root cause, resolution, or outcome. Those \
fields do not exist yet and nothing in this request fills them in.

## PAST EXPERIENCES FROM HINDSIGHT
The items below were retrieved by semantic similarity from a long-term memory bank. \
They are HISTORICAL EVIDENCE about other, earlier incidents, never confirmed facts \
about the current one.

Each item carries a verification label telling you how that past incident was \
established. The two labels are NOT equally strong, and you must not treat them \
the same.

verification=confirmed, experience_type=confirmed_resolution
    A person investigated that past incident and recorded its root cause, \
resolution, and outcome in the incident record before it was stored. Those three \
values were confirmed for THAT incident. They are reliable as history.
    They are still about a different incident, so they are not established for the \
current one.

verification=unverified, experience_type=incident_report
    A record captured when that incident was first reported. Its root cause, \
resolution, and outcome were NOT established and are recorded as "not determined \
at time of report". A report tells you the symptom, the service, and the severity \
and nothing more. Any causal story in a report is unverified.
    A symptom in a report is a symptom, not a diagnosis. Do not read a report's \
description as though it named the cause.

When a confirmed experience and a report describe similar symptoms, the confirmed \
experience carries more weight, because a person actually verified it. Say so \
explicitly when you rely on one. When only reports are available, the cause of any \
of those incidents is unknown, and you cannot inherit a cause from them.

{memories}

## TASK
1. Analyse what the current incident describes, based on its description alone.
2. Compare the current incident against the past experiences. For each confirmed \
experience, state explicitly:
   a. what actually happened in that past incident,
   b. why it is relevant to the current incident, naming the specific shared \
symptom, service, or failure mode,
   c. what must be verified in the current incident before its solution is applied.
3. State which experiences are not relevant, and why. A report that merely mentions \
a similar word is not evidence.
4. If a confirmed past root cause is a plausible hypothesis for the current \
incident, you may name it as a hypothesis, and you should name the historical \
resolution as an action worth investigating. You may not present either as the \
established cause or the agreed fix for this incident.
5. Report your confidence honestly.
6. Say what evidence is missing and what should be checked next.

## EVIDENCE RULES (strict)
- Past experiences are historical evidence only. Never present one as a confirmed \
fact about the current incident, however well the symptoms match.
- A confirmed past root cause may be offered as a HYPOTHESIS to test. It must be \
labelled as historical and hypothetical, never as this incident's root cause, and \
never as something already established here.
- A confirmed past resolution may be suggested as an action to investigate when the \
symptoms match. It is a starting point to verify, not a confirmed action to carry \
out.
- An unverified report contributes no root cause. Do not infer a cause from a \
report's description, and do not treat similarity between two reports as evidence \
that either was diagnosed.
- Do NOT invent a root cause, resolution, outcome, outage duration, deployment \
history, or infrastructure state. None of these are available to you for the \
current incident. If you mention one at all, mark it clearly as a hypothesis.
- If the evidence is insufficient, say plainly that the cause is not determined \
and set "likely_root_cause" to null. A correct "we do not know yet" is a better \
answer than a confident guess, and an uncertain answer is expected.
- Use JSON null, never the strings "unknown" or "n/a".
- Confidence must reflect the evidence, not the fluency of the analogy. "low" when \
evidence is thin or only unverified reports exist. "medium" when a confirmed \
experience matches on a specific shared symptom. Reserve "high" for direct, \
specific, unambiguous evidence about the current incident itself. A confirmed \
historical experience is still historical, so it alone does not justify "high".

## WRITING THE "analysis" FIELD
When a confirmed experience is relevant, the "analysis" field must walk through \
these three points in order, naming the past incident's id:
1. What actually happened then, as a person confirmed it for that incident. Quote its \
confirmed_root_cause, confirmed_resolution, and confirmed_outcome as they are written. \
Do not generalise them into "a cause was found and a fix was applied": the specific \
values are the whole value of that memory.
2. Why that matches this incident, citing the specific shared symptom or service.
3. What must be verified here before its solution is applied.
When nothing relevant was retrieved, say so plainly instead of padding.

Reply with JSON only. No prose before or after. Use exactly this shape:
{{
  "analysis": "string",
  "likely_root_cause": "string or null",
  "recommended_resolution": "string or null",
  "confidence": "low" | "medium" | "high"
}}
"""

# Values that mean "the model declined to answer" rather than an actual finding.
# Normalising these to null keeps unverified text out of likely_root_cause.
NON_ANSWERS = frozenset(
    {
        "",
        "-",
        "n/a",
        "na",
        "none",
        "null",
        "unknown",
        "unclear",
        "undetermined",
        "no information",
        "not enough information",
        "insufficient information",
        "insufficient evidence",
        "not enough evidence",
        "cannot determine",
        "cannot be determined",
    }
)


class GroqNotConfiguredError(RuntimeError):
    """Raised when the Groq API key is missing from the environment."""


class GroqRequestError(RuntimeError):
    """Raised when the call to Groq failed. Carries no request or key detail."""


class GroqResponseError(RuntimeError):
    """Raised when Groq replied with something that is not usable analysis."""


@lru_cache
def get_client() -> AsyncGroq:
    """Return a Groq client, or raise if no key is configured."""
    if settings.groq_api_key is None:
        raise GroqNotConfiguredError("Set GROQ_API_KEY in backend/.env")

    return AsyncGroq(
        api_key=settings.groq_api_key.get_secret_value(),
        timeout=settings.groq_timeout_seconds,
        max_retries=2,
    )


def _blank_to_null(value: Any) -> Any:
    """Map placeholder strings to None so they never read as a real finding."""
    if isinstance(value, str) and value.strip().lower() in NON_ANSWERS:
        return None
    return value


def _memory_verification(memory: Any) -> tuple[str, str]:
    """Classify one recalled memory as (verification, experience_type).

    Taken from the stored text alone. Relevance to the current incident says
    nothing about whether a human confirmed that past incident, so it is not used.
    """
    return classify_memory(getattr(memory, "text", None))


def format_memories(memories: list[Any]) -> str:
    """Render recalled memories for the prompt, confirmed experiences first.

    Confirmed experiences are listed first, before plain reports. Retrieval ranks
    by similarity, so a report that closely echoes the current wording can outrank
    a genuinely confirmed experience, and a person verified the latter and not the
    former.

    Truncated per item: the prompt is a fixed budget, and one very long memory
    should not crowd out the current incident.
    """
    if not memories:
        return "(no past experiences were retrieved for this incident)"

    lines = []
    for index, memory in enumerate(prioritize_confirmed_memories(memories), start=1):
        raw = getattr(memory, "text", "") or ""
        verification, experience_type = _memory_verification(memory)
        incident_id = parse_memory_incident_id(raw)
        service = parse_memory_service(raw)

        # Keep the stored line structure. Collapsing a memory into one run of prose
        # strips the `key: value` labels off the confirmed root cause, resolution
        # and outcome, and the model then paraphrases those values instead of
        # reporting them.
        body = "\n".join(f"     {line.strip()}" for line in raw.splitlines() if line.strip())
        if len(body) > 1200:
            body = body[:1200].rstrip() + "\n     [...]"

        lines.append(
            f"{index}. verification={verification} experience_type={experience_type}"
            f" incident_id={incident_id or 'unknown'}"
            f" service={service or 'unknown'}\n{body}"
        )
    return "\n".join(lines)


def build_messages(incident, memories: list[Any]) -> list[dict[str, str]]:
    """Build the chat messages for one analysis request."""
    user_prompt = SYSTEM_PROMPT.format(
        incident_id=incident.incident_id,
        service=incident.service,
        severity=incident.severity,
        status=incident.status,
        description=incident.description,
        memories=format_memories(memories),
    )
    return [
        {
            "role": "system",
            "content": "You reply with JSON only, following the requested schema exactly.",
        },
        {"role": "user", "content": user_prompt},
    ]


async def analyze_incident(incident: Any, memories: list[Any]) -> IncidentAnalysis:
    """Send one incident plus its recalled memories to Groq and parse the reply.

    Raises GroqNotConfiguredError, GroqRequestError, or GroqResponseError. It does
    not raise on the model declining to conclude: that arrives as a valid result
    with null root_cause and resolution.
    """
    client = get_client()

    try:
        completion = await client.chat.completions.create(
            model=settings.groq_model,
            messages=build_messages(incident, memories),
            temperature=0.1,
            max_tokens=1200,
            response_format={"type": "json_object"},
        )
    except GroqError as exc:
        # Log the type and status only. The message can echo request content.
        logger.warning(
            "Groq request failed: %s (status=%s)",
            type(exc).__name__,
            getattr(exc, "status_code", None),
        )
        raise GroqRequestError(f"Groq request failed ({type(exc).__name__})") from exc
    except httpx.TimeoutException as exc:
        logger.warning("Groq request timed out after %ss", settings.groq_timeout_seconds)
        raise GroqRequestError("Groq request timed out") from exc

    content = completion.choices[0].message.content
    if not content or not content.strip():
        logger.warning("Groq returned an empty completion")
        raise GroqResponseError("Groq returned an empty response")

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning("Groq response was not valid JSON (length=%s)", len(content))
        raise GroqResponseError("Groq returned a response that was not valid JSON") from exc

    if not isinstance(payload, dict):
        raise GroqResponseError("Groq returned JSON that was not an object")

    # analysis and confidence must actually be there. Defaulting them would turn a
    # truncated or empty reply into a clean-looking "no conclusion" result, which
    # is exactly the false assurance this endpoint must not give.
    for field in ("analysis", "confidence"):
        if field not in payload:
            raise GroqResponseError(f"Groq response was missing '{field}'")

    analysis_text = payload["analysis"]
    if not isinstance(analysis_text, str) or not analysis_text.strip():
        raise GroqResponseError("Groq returned an empty analysis")

    # The two judgement fields may legitimately be null. A missing key is treated
    # as null too, which is the conservative direction: no claim made.
    for field in ("likely_root_cause", "recommended_resolution"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            raise GroqResponseError(f"Groq returned a non-text value for '{field}'")
        payload[field] = _blank_to_null(value)

    try:
        confidence = AnalysisConfidence(str(payload["confidence"]).strip().lower())
    except ValueError as exc:
        raise GroqResponseError(
            "Groq returned a confidence value outside low, medium, high"
        ) from exc

    return IncidentAnalysis(
        analysis=analysis_text.strip(),
        likely_root_cause=payload["likely_root_cause"],
        recommended_resolution=payload["recommended_resolution"],
        confidence=confidence,
    )
