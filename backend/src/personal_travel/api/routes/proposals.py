from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status

from personal_travel.api.dependencies import OwnerDependency, SessionDependency
from personal_travel.api.schemas import COMMON_ERROR_RESPONSES
from personal_travel.api.schemas.proposals import (
    ProposalApplyResponse,
    ProposalDetailResponse,
    ProposalGenerateRequest,
)
from personal_travel.config import Settings, get_settings
from personal_travel.services.proposals import ProposalService

router = APIRouter(
    prefix="/trips/{trip_id}/proposals",
    tags=["itinerary proposals"],
    responses=COMMON_ERROR_RESPONSES,
)


@router.post("", response_model=ProposalDetailResponse, status_code=status.HTTP_201_CREATED)
async def generate_proposal(
    trip_id: UUID,
    payload: ProposalGenerateRequest,
    session: SessionDependency,
    owner_id: OwnerDependency,
    settings: Annotated[Settings, Depends(get_settings)],
    expected_revision: Annotated[int, Header(alias="X-Expected-Revision", ge=0)],
) -> ProposalDetailResponse:
    return await ProposalService(session, owner_id, settings).generate(
        trip_id, payload, expected_revision=expected_revision
    )


@router.get("/by-key/{idempotency_key}", response_model=ProposalDetailResponse)
async def get_proposal_by_key(
    trip_id: UUID,
    idempotency_key: UUID,
    session: SessionDependency,
    owner_id: OwnerDependency,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProposalDetailResponse:
    return await ProposalService(session, owner_id, settings).get_by_key(trip_id, idempotency_key)


@router.get("/{proposal_id}", response_model=ProposalDetailResponse)
async def get_proposal(
    trip_id: UUID,
    proposal_id: UUID,
    session: SessionDependency,
    owner_id: OwnerDependency,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProposalDetailResponse:
    return await ProposalService(session, owner_id, settings).get(trip_id, proposal_id)


@router.post("/{proposal_id}/apply", response_model=ProposalApplyResponse)
async def apply_proposal(
    trip_id: UUID,
    proposal_id: UUID,
    session: SessionDependency,
    owner_id: OwnerDependency,
    settings: Annotated[Settings, Depends(get_settings)],
    expected_revision: Annotated[int, Header(alias="X-Expected-Revision", ge=0)],
) -> ProposalApplyResponse:
    return await ProposalService(session, owner_id, settings).apply(
        trip_id, proposal_id, expected_revision=expected_revision
    )


@router.post("/{proposal_id}/reject", response_model=ProposalDetailResponse)
async def reject_proposal(
    trip_id: UUID,
    proposal_id: UUID,
    session: SessionDependency,
    owner_id: OwnerDependency,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProposalDetailResponse:
    return await ProposalService(session, owner_id, settings).reject(trip_id, proposal_id)
