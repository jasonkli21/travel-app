from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from personal_travel.domain.upstream_comparisons import TravelComparisonCategory
from personal_travel.domain.urls import validate_http_url

ComparisonOutcome = Literal["pass", "fail", "unknown"]
TravelComparisonState = Literal[
    "recommended",
    "eligible_unranked",
    "research_needed",
    "no_verified_match",
    "insufficient",
    "expired",
]


class TravelComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: TravelComparisonCategory
    query: str = Field(min_length=1, max_length=180)
    reference_place_id: UUID
    reference_place_revision: int = Field(ge=0)
    reference_latitude: float = Field(ge=-90, le=90)
    reference_longitude: float = Field(ge=-180, le=180)
    trip_revision: int = Field(ge=0)
    radius_km: float = Field(default=5, gt=0, le=20)
    max_results: int = Field(default=8, ge=1, le=10)
    idempotency_key: UUID

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized or any(ord(char) < 32 for char in normalized):
            raise ValueError("query must contain printable text")
        return normalized


class TravelComparisonSourceResponse(BaseModel):
    evidence_id: UUID
    source_observation_id: UUID
    provider: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=300)
    attribution: str = Field(min_length=1, max_length=500)
    policy_url: str | None = Field(default=None, max_length=2048)
    observed_at: datetime
    expires_at: datetime

    @field_validator("url", "policy_url")
    @classmethod
    def public_url(cls, value: str | None) -> str | None:
        return validate_http_url(value)

    @field_validator("observed_at", "expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source timestamps require a timezone")
        return value

    @model_validator(mode="after")
    def source_expiry_is_ordered(self) -> Self:
        if self.expires_at <= self.observed_at:
            raise ValueError("source expiry must follow observation time")
        return self


class TravelComparisonConstraintResponse(BaseModel):
    name: Literal["category", "distance"]
    label: str = Field(min_length=1, max_length=120)
    outcome: ComparisonOutcome
    detail: str = Field(min_length=1, max_length=240)


class TravelComparisonCandidateResponse(BaseModel):
    candidate_id: UUID
    name: str = Field(min_length=1, max_length=300)
    category: TravelComparisonCategory
    address: str | None = Field(default=None, max_length=300)
    place_type: str | None = Field(default=None, max_length=100)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    distance_km: float | None = Field(default=None, ge=0, le=20_100)
    eligible: bool
    rank: int | None = Field(default=None, ge=1)
    score: float | None = Field(default=None, ge=0, le=1)
    exclusion_reasons: tuple[str, ...] = Field(default=(), max_length=10)
    constraints: tuple[TravelComparisonConstraintResponse, ...] = Field(max_length=4)
    sources: tuple[TravelComparisonSourceResponse, ...] = Field(max_length=12)

    @model_validator(mode="after")
    def coordinate_pair(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("candidate coordinates must be paired")
        return self


class TravelComparisonResponse(BaseModel):
    schema_version: Literal["travel-research-comparison-v1"] = "travel-research-comparison-v1"
    comparison_id: UUID
    category: TravelComparisonCategory
    state: TravelComparisonState
    trip_revision: int = Field(ge=0)
    reference_place_id: UUID
    reference_place_revision: int = Field(ge=0)
    reference_place_name: str = Field(min_length=1, max_length=240)
    radius_km: float = Field(gt=0, le=20)
    generated_at: datetime
    expires_at: datetime | None
    candidates: tuple[TravelComparisonCandidateResponse, ...] = Field(max_length=10)

    @field_validator("generated_at", "expires_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("comparison timestamps require a timezone")
        return value

    @model_validator(mode="after")
    def bounded_comparison(self) -> Self:
        if len({candidate.candidate_id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("comparison candidates must be unique")
        if any(candidate.category != self.category for candidate in self.candidates):
            raise ValueError("comparison candidate category mismatch")
        return self


class TravelComparisonCandidateSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=240)
    address: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=1000)
    trip_revision: int = Field(ge=0)
    reference_place_id: UUID
    reference_place_revision: int = Field(ge=0)

    @field_validator("name", "address", "category", "note")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.split()) or None

    @model_validator(mode="after")
    def require_name(self) -> Self:
        if self.name is None or not self.name:
            raise ValueError("name is required")
        return self
