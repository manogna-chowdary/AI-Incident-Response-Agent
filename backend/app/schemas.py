from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.models import IncidentSeverity, IncidentStatus


class IncidentCreate(BaseModel):
    """Request body for creating an incident."""

    service: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1)
    severity: IncidentSeverity = IncidentSeverity.SEVERITY_UNKNOWN


class IncidentRead(BaseModel):
    """Full incident as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: str
    service: str
    description: str
    severity: IncidentSeverity
    status: IncidentStatus
    root_cause: str | None
    resolution: str | None
    outcome: str | None
    created_at: datetime
    updated_at: datetime


class MemoryRecallItem(BaseModel):
    """A single memory exactly as Hindsight returned it."""

    text: str
    type: str | None


class PastExperience(MemoryRecallItem):
    """One recalled memory, with its provenance read from the stored content.

    A recalled memory always describes a different, earlier incident, so
    `verification` says how that *other* incident's record was established, not
    whether it is confirmed about the one being analysed. Both values are set by
    `app.memory.classify_memory` from the marker in the stored text:

    * `confirmed` + `confirmed_resolution` — a resolution a person verified, stored
      by /learn.
    * `unverified` + `incident_report` — a report captured when the incident was
      filed, or a memory this service did not write.
    """

    source: Literal["hindsight"] = "hindsight"
    verification: Literal["confirmed", "unverified"]
    experience_type: Literal["confirmed_resolution", "incident_report"]


class IncidentCreateResponse(IncidentRead):
    """An incident as returned by creation, plus whether memory was stored.

    The incident is persisted first, so creation succeeds even when Hindsight is
    unavailable. This reports what happened to the memory write instead of
    failing the whole request.
    """

    memory_retained: bool
    memory_error: str | None = None


class IncidentRecallRequest(BaseModel):
    """Request body for recalling past incident experiences."""

    query: str = Field(min_length=1)

    @field_validator("query")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """A query of only spaces would be a wasted call to Hindsight."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


class IncidentRecallResponse(BaseModel):
    """Past experiences retrieved for an incident.

    Read-only with respect to the incident: nothing here is written back to
    root_cause, resolution, or outcome.
    """

    incident_id: str
    query: str
    notice: str
    count: int
    results: list[PastExperience]


class AnalysisConfidence(StrEnum):
    """How well the evidence supports the analysis."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IncidentAnalysis(BaseModel):
    """The LLM's judgement, before it is attached to an incident.

    Root cause and resolution are nullable on purpose. "We do not know yet" has
    to be a representable answer, otherwise the model fills the field with a guess.
    """

    analysis: str
    likely_root_cause: str | None
    recommended_resolution: str | None
    confidence: AnalysisConfidence


class IncidentAnalysisResponse(IncidentAnalysis):
    """Analysis of one incident, with the past experiences it was based on.

    Advisory output. These fields are a suggestion to investigate, not a record of
    what happened, and nothing here is written back to the incident.
    """

    incident_id: str
    supporting_memories: list[PastExperience]
    model: str


class IncidentApprovalRequest(BaseModel):
    """Human approval decision for an incident.

    `approved` is required, so a missing field is a 422 rather than a silent
    default. Only a real JSON boolean is accepted: an approval is a decision that
    must not be inferred from a truthy string or an integer.
    """

    model_config = ConfigDict(extra="forbid")

    approved: StrictBool = Field(
        description="True -> set status to 'approved'. False -> set status to 'rejected'."
    )

    @field_validator("approved")
    @classmethod
    def _reject_blank(cls, value: bool) -> bool:
        """Guard against `false` arriving as a blank string from a form post."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("approved must be a boolean, not a blank value")
        return value


class IncidentApprovalResponse(IncidentRead):
    """The incident as it stands after a human approval decision.

    Carries the full incident so the caller sees the resulting state, and
    `previous_status` so a client can tell what the decision changed.
    """

    previous_status: IncidentStatus
    approved: bool


def _reject_blank_text(value: str) -> str:
    """Trim a free-text field and refuse it if nothing is left.

    All three conclusion fields are optional on the model but mandatory here. A
    blank string is worse than a null: the row would claim the question was
    answered while recording nothing.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be empty or whitespace only")
    return stripped


class IncidentResolveRequest(BaseModel):
    """Human-confirmed cause, fix, and result for a closed incident.

    These are the actual outcome as confirmed by whoever investigated, not a
    suggestion. Nothing here is generated: the API stores what it is given and
    nothing else infers these fields.
    """

    model_config = ConfigDict(extra="forbid")

    root_cause: str = Field(
        min_length=1,
        description="Confirmed root cause, as established by the investigation.",
    )
    resolution: str = Field(
        min_length=1,
        description="What was actually changed or done to fix it.",
    )
    outcome: str = Field(
        min_length=1,
        description="Confirmed result after the fix.",
    )

    _check_root_cause = field_validator("root_cause")(_reject_blank_text)
    _check_resolution = field_validator("resolution")(_reject_blank_text)
    _check_outcome = field_validator("outcome")(_reject_blank_text)


class IncidentResolveResponse(IncidentRead):
    """The incident after its resolution has been recorded.

    Includes `previous_status` so a caller can tell what recording the resolution
    changed.
    """

    previous_status: IncidentStatus


class IncidentLearnResponse(BaseModel):
    """Outcome of storing a resolved incident as confirmed experience.

    Reports what was retained rather than the incident itself: /learn reads the
    incident, it does not change it. `already_learned` distinguishes a first
    successful store from a repeat call that was skipped.
    """

    incident_id: str
    status: IncidentStatus
    learned: bool
    already_learned: bool
    memory_retained: bool
    document_id: str
    message: str