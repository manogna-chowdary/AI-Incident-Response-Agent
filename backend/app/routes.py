import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.groq import analyze_incident as analyze_with_groq
from app.memory import (
    PAST_EXPERIENCE_NOTICE,
    HindsightNotConfiguredError,
    HindsightRequestError,
    build_learned_experience,
    build_recall_query,
    build_retained_experience,
    classify_memory,
    exclude_current_incident,
    find_learned_memory,
    get_bank_id,
    get_client,
    learn_document_id,
    missing_confirmed_fields,
    prioritize_confirmed_memories,
    recall_experiences,
    retain_experience,
    retain_learned_experience,
)
from app.models import Incident, IncidentStatus
from app.schemas import (
    IncidentAnalysisResponse,
    IncidentApprovalRequest,
    IncidentApprovalResponse,
    IncidentCreate,
    IncidentCreateResponse,
    IncidentLearnResponse,
    IncidentRead,
    IncidentRecallRequest,
    IncidentRecallResponse,
    IncidentResolveRequest,
    IncidentResolveResponse,
    PastExperience,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/incidents", tags=["incidents"])


def generate_incident_id() -> str:
    """Build a unique human-readable reference such as INC-20260928-3F9A2C."""
    return f"INC-{datetime.now(timezone.utc):%Y%m%d}-{uuid4().hex[:6].upper()}"


async def _find_incident(session: AsyncSession, incident_id: str) -> Incident:
    result = await session.execute(select(Incident).where(Incident.incident_id == incident_id))
    incident = result.scalar_one_or_none()

    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

    return incident


def _as_past_experience(memory) -> PastExperience:
    """Build the API view of one recalled memory.

    The verification label is derived from the stored text, so a memory /learn
    wrote is reported as a confirmed experience while an ordinary incident report
    stays unverified. Shared by /recall and /analyze so the two cannot disagree
    about the same memory.
    """
    verification, experience_type = classify_memory(memory.text)

    return PastExperience(
        text=memory.text,
        type=memory.type,
        verification=verification,
        experience_type=experience_type,
    )


@router.post("", response_model=IncidentCreateResponse, status_code=201)
async def create_incident(
    payload: IncidentCreate,
    session: AsyncSession = Depends(get_session),
) -> Incident:
    incident = Incident(
        incident_id=generate_incident_id(),
        service=payload.service,
        description=payload.description,
        severity=payload.severity,
        status=IncidentStatus.OPEN,
    )

    session.add(incident)
    await session.commit()
    await session.refresh(incident)

    memory_retained = False
    memory_error: str | None = None

    try:
        client = get_client()
        bank_id = get_bank_id()
        await retain_experience(client, bank_id, build_retained_experience(incident))
        memory_retained = True
    except HindsightNotConfiguredError as exc:
        logger.info("Incident %s stored without memory: %s", incident.incident_id, exc)
        memory_error = str(exc)
    except HindsightRequestError as exc:
        logger.warning(
            "Memory retain failed for %s: %s (status=%s)",
            incident.incident_id,
            exc.error_type,
            exc.status,
        )
        memory_error = str(exc)

    return IncidentCreateResponse(
        id=incident.id,
        incident_id=incident.incident_id,
        service=incident.service,
        description=incident.description,
        severity=incident.severity,
        status=incident.status,
        root_cause=incident.root_cause,
        resolution=incident.resolution,
        outcome=incident.outcome,
        created_at=incident.created_at,
        updated_at=incident.updated_at,
        memory_retained=memory_retained,
        memory_error=memory_error,
    )


@router.get("", response_model=list[IncidentRead])
async def list_incidents(
    session: AsyncSession = Depends(get_session),
) -> list[Incident]:
    result = await session.execute(
        select(Incident).order_by(Incident.created_at.desc(), Incident.id.desc())
    )
    return list(result.scalars().all())


@router.get("/{incident_id}", response_model=IncidentRead)
async def get_incident(
    incident_id: str,
    session: AsyncSession = Depends(get_session),
) -> Incident:
    return await _find_incident(session, incident_id)


@router.post("/{incident_id}/recall", response_model=IncidentRecallResponse)
async def recall_incident_memories(
    incident_id: str,
    payload: IncidentRecallRequest,
    session: AsyncSession = Depends(get_session),
) -> IncidentRecallResponse:
    """Return past experiences for an incident.

    Read-only by design. Nothing from the recalled memories is written back to
    the incident: root_cause, resolution, and outcome are only ever set by a
    decision made outside this endpoint, after verification.
    """
    incident = await _find_incident(session, incident_id)

    client = get_client()
    bank_id = get_bank_id()
    recalled = await recall_experiences(client, bank_id, payload.query)

    items = [_as_past_experience(item) for item in recalled.results]

    return IncidentRecallResponse(
        incident_id=incident.incident_id,
        query=payload.query,
        notice=PAST_EXPERIENCE_NOTICE,
        count=len(items),
        results=items,
    )


@router.post("/{incident_id}/analyze", response_model=IncidentAnalysisResponse)
async def analyze_incident(
    incident_id: str,
    session: AsyncSession = Depends(get_session),
) -> IncidentAnalysisResponse:
    """Analyse an incident against past experiences and suggest a resolution.

    Advisory only. Nothing is written back: root_cause, resolution, and outcome on
    the incident stay untouched, so no suggestion becomes a record without a
    separate, deliberate decision. Returns nulls when the evidence is
    insufficient rather than guessing.
    """
    # (a) the current incident
    incident = await _find_incident(session, incident_id)

    # (b) a recall query built from service, description, and severity
    query = build_recall_query(incident)

    # (c) past experiences from Hindsight
    client = get_client()
    bank_id = get_bank_id()
    recalled = await recall_experiences(client, bank_id, query)

    # (d) drop the incident's own report so it is not treated as evidence for itself
    past_experiences = exclude_current_incident(recalled.results, incident)

    # (e) and (f) send both to Groq for a suggestion
    analysis = await analyze_with_groq(incident, past_experiences)

    return IncidentAnalysisResponse(
        incident_id=incident.incident_id,
        analysis=analysis.analysis,
        likely_root_cause=analysis.likely_root_cause,
        recommended_resolution=analysis.recommended_resolution,
        confidence=analysis.confidence,
        supporting_memories=[
            _as_past_experience(item)
            for item in prioritize_confirmed_memories(past_experiences)
        ],
        model=settings.groq_model,
    )


@router.post("/{incident_id}/approve", response_model=IncidentApprovalResponse)
async def approve_incident(
    incident_id: str,
    payload: IncidentApprovalRequest,
    session: AsyncSession = Depends(get_session),
) -> IncidentApprovalResponse:
    """Record a human approval decision for an incident.

    Changes status to 'approved' or 'rejected' and nothing else. Every transition is
    allowed from any current status, and repeating the same decision is idempotent:
    a second approval of an approved incident stays approved.

    This endpoint writes no memory. Hindsight is deliberately left alone in this
    phase, so an approval is not yet reflected in recalled experience.
    """

    incident = await _find_incident(session, incident_id)
    previous_status = incident.status

    incident.status = (
        IncidentStatus.APPROVED if payload.approved else IncidentStatus.REJECTED
    )

    # Only `status` is assigned. root_cause, resolution, and outcome are never
    # touched here: approving an incident records that a human accepted it, it does
    # not assert what caused it or how it was fixed.
    await session.commit()
    await session.refresh(incident)

    return IncidentApprovalResponse(
        id=incident.id,
        incident_id=incident.incident_id,
        service=incident.service,
        description=incident.description,
        severity=incident.severity,
        status=incident.status,
        root_cause=incident.root_cause,
        resolution=incident.resolution,
        outcome=incident.outcome,
        created_at=incident.created_at,
        updated_at=incident.updated_at,
        previous_status=previous_status,
        approved=payload.approved,
    )


@router.post("/{incident_id}/resolve", response_model=IncidentResolveResponse)
async def resolve_incident(
    incident_id: str,
    payload: IncidentResolveRequest,
    session: AsyncSession = Depends(get_session),
) -> IncidentResolveResponse:
    """Record the confirmed root cause, resolution, and outcome, and close the incident.

    These three values are what an engineer actually established. Nothing infers
    them: the body is required, and this endpoint never calls Groq or Hindsight, so
    an AI suggestion from /analyze cannot reach these columns on its own.

    No prerequisite state is required. An incident can be resolved whether it was
    never approved or was rejected; a resolution is a statement of what happened,
    and gating it on a prior approval would block recording the truth in cases where
    the approval workflow was skipped or the incident self-resolved. Re-resolving
    overwrites the three fields and is the way to correct a mistaken record.
    """

    incident = await _find_incident(session, incident_id)
    previous_status = incident.status

    # All three are required by the schema, so there is no partial write: the
    # request is rejected whole or stored whole.
    incident.root_cause = payload.root_cause
    incident.resolution = payload.resolution
    incident.outcome = payload.outcome
    incident.status = IncidentStatus.RESOLVED

    await session.commit()
    await session.refresh(incident)

    return IncidentResolveResponse(
        id=incident.id,
        incident_id=incident.incident_id,
        service=incident.service,
        description=incident.description,
        severity=incident.severity,
        status=incident.status,
        root_cause=incident.root_cause,
        resolution=incident.resolution,
        outcome=incident.outcome,
        created_at=incident.created_at,
        updated_at=incident.updated_at,
        previous_status=previous_status,
    )


@router.post("/{incident_id}/learn", response_model=IncidentLearnResponse)
async def learn_incident(
    incident_id: str,
    session: AsyncSession = Depends(get_session),
) -> IncidentLearnResponse:
    """Store a resolved incident as confirmed experience in Hindsight.

    Only a resolved incident whose root cause, resolution, and outcome are all
    recorded in Postgres is eligible. The memory is rendered purely from the stored
    row, so nothing in it is inferred, and it is marked as confirmed experience so
    it stays distinguishable from an unverified report.

    This endpoint reads the incident and never writes to it. A Hindsight failure
    therefore cannot damage the record, and the failure is reported rather than
    swallowed: a silent miss would leave a resolved incident that looks learned
    when it is not.

    Repeating the call is a no-op that reports `already_learned`.
    """

    incident = await _find_incident(session, incident_id)

    if incident.status != IncidentStatus.RESOLVED:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Incident {incident_id} has status '{incident.status}'. Only a "
                "resolved incident can be learned. Record the root cause, "
                "resolution, and outcome via /resolve first."
            ),
        )

    missing = missing_confirmed_fields(incident)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Incident {incident_id} is resolved but has no confirmed value for: "
                f"{', '.join(missing)}. A resolved incident without these cannot be "
                "used as past experience."
            ),
        )

    client = get_client()
    bank_id = get_bank_id()
    document_id = learn_document_id(incident.incident_id)

    existing = await find_learned_memory(client, bank_id, incident.incident_id)
    if existing is not None:
        return IncidentLearnResponse(
            incident_id=incident.incident_id,
            status=incident.status,
            learned=False,
            already_learned=True,
            memory_retained=True,
            document_id=document_id,
            message=(
                "Already stored as confirmed experience. No duplicate was written."
            ),
        )

    content = build_learned_experience(incident)
    await retain_learned_experience(client, bank_id, content, incident.incident_id)

    return IncidentLearnResponse(
        incident_id=incident.incident_id,
        status=incident.status,
        learned=True,
        already_learned=False,
        memory_retained=True,
        document_id=document_id,
        message=(
            "Stored as confirmed experience. It can now be recalled for similar "
            "past incidents."
        ),
    )
