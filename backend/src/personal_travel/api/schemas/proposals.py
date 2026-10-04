from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from personal_travel.domain.proposals import ProposalOperation


class ProposalGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    idempotency_key: UUID
    instruction: str = Field(min_length=1, max_length=2000)
    removable_item_ids: tuple[UUID, ...] = Field(default=(), max_length=25)
    research_session_ids: tuple[UUID, ...] = Field(default=(), max_length=3)

    @field_validator("instruction")
    @classmethod
    def normalize_instruction(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("instruction must contain printable text")
        return value

    @field_validator("removable_item_ids", "research_session_ids")
    @classmethod
    def unique_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate identifier")
        return tuple(sorted(value, key=str))


class ProposalCitationResponse(BaseModel):
    evidence_handle: str
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=300)
    observed_at: datetime
    expires_at: datetime


class OperationEvidenceSupportResponse(BaseModel):
    operation_index: int = Field(ge=0, le=24)
    evidence_handles: list[str] = Field(max_length=24)


class ProposalDetailResponse(BaseModel):
    proposal_id: UUID
    state: Literal[
        "generating",
        "outcome_unknown",
        "ready",
        "stale",
        "expired",
        "failed",
        "applied",
        "rejected",
    ]
    lifecycle_state: Literal[
        "generating", "outcome_unknown", "ready", "failed", "applied", "rejected"
    ]
    support_mode: Literal["context_only", "research_evidence"]
    upstream_revision: str = Field(min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$")
    trip_handle: str
    created_at: datetime
    expires_at: datetime | None
    base_trip_revision: int
    current_trip_revision: int | None
    base_place_revisions: list[dict[str, Any]]
    current_place_revisions: list[dict[str, Any]]
    operations: list[ProposalOperation]
    operation_support: list[OperationEvidenceSupportResponse]
    citations: list[ProposalCitationResponse]
    preview: dict[str, Any] | None
    applied_outcome: dict[str, Any] | None
    failure_code: str | None


class ProposalApplyResponse(BaseModel):
    proposal_id: UUID
    state: Literal["applied"] = "applied"
    applied_revision: int
    applied_at: datetime
    preview: dict[str, Any]
