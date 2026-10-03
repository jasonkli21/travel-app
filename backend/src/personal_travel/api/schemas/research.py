from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from personal_travel.domain.types import ResearchFreshness, ResearchState
from personal_travel.domain.urls import validate_http_url as _http_url


class TripResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    day_id: UUID
    question: str = Field(min_length=1, max_length=300)
    freshness: ResearchFreshness = "current"
    idempotency_key: UUID

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized or any(ord(char) < 32 for char in normalized):
            raise ValueError("question must contain printable text")
        return normalized


class ResearchCitationResponse(BaseModel):
    number: int = Field(ge=1)
    evidence_id: UUID
    source_observation_id: UUID
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=500)
    observed_at: datetime
    expires_at: datetime

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        _http_url(value)
        return value


class TripResearchResponse(BaseModel):
    schema_version: Literal["trip-research-v1"] = "trip-research-v1"
    session_id: UUID
    state: ResearchState
    answer: str | None = Field(default=None, max_length=20000)
    failure_code: str | None = Field(default=None, max_length=80)
    expires_at: datetime
    citations: list[ResearchCitationResponse] = Field(default_factory=list, max_length=144)
