"""Pinned strict DTOs for the candidate booking-document-extraction-v1 contract."""

import json
from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

UPSTREAM_REVISION = "ece8cfc3db044aab3b275709c12e71eb17f2520d"
SCHEMA_VERSION = "booking-document-extraction-v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class UpstreamBookingCandidate(StrictModel):
    candidate_id: str = Field(min_length=12, max_length=32, pattern=r"^c_[a-f0-9]{10,30}$")
    reservation_type: Literal["flight", "lodging", "rail", "car", "activity", "other"] | None
    provider_name: str | None = Field(default=None, max_length=200)
    confirmation_code: str | None = Field(default=None, max_length=160)
    starts_at_text: str | None = Field(default=None, max_length=80)
    starts_at_date: date | None = None
    starts_at_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    starts_at_timezone: str | None = Field(default=None, max_length=64)
    ends_at_text: str | None = Field(default=None, max_length=80)
    ends_at_date: date | None = None
    ends_at_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    ends_at_timezone: str | None = Field(default=None, max_length=64)
    source_start: int = Field(ge=0, le=200_000)
    source_end: int = Field(gt=0, le=200_000)
    source_excerpt: str = Field(max_length=240)
    uncertain_fields: tuple[
        Literal[
            "reservation_type",
            "provider_name",
            "confirmation_code",
            "starts_at",
            "ends_at",
            "starts_at_timezone",
            "ends_at_timezone",
        ],
        ...,
    ] = Field(max_length=7)


class UpstreamBookingExtractionResult(StrictModel):
    schema_version: Literal["booking-document-extraction-v1"] = "booking-document-extraction-v1"
    extraction_id: UUID
    idempotency_key: UUID
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["running", "completed", "failed", "deleted", "expired"]
    candidates: tuple[UpstreamBookingCandidate, ...] = Field(max_length=10)
    failure_code: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_]+$")
    created_at: datetime
    expires_at: datetime

    @field_validator("created_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("upstream timestamps require a timezone")
        return value.astimezone(UTC)

    @classmethod
    def from_payload(cls, payload: object) -> "UpstreamBookingExtractionResult":
        return cls.model_validate_json(json.dumps(payload, ensure_ascii=False))
