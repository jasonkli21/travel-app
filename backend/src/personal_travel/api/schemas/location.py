from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from personal_travel.domain.types import GeoapifyRouteMode
from personal_travel.domain.urls import validate_http_url as _http_url

from .common import _trim_optional


class PlaceSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider_place_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=240)
    address: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=120)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    provider_source_name: str = Field(min_length=1, max_length=128)
    provider_source_attribution: str = Field(min_length=1, max_length=500)
    provider_source_license: str | None = Field(default=None, max_length=255)
    provider_source_url: str | None = Field(default=None, max_length=500)

    @field_validator("provider_source_url")
    @classmethod
    def validate_provider_source_url(cls, value: str | None) -> str | None:
        return _http_url(value)


class PlaceSearchResponse(PlaceSearchResult):
    provider: Literal["geoapify"] = "geoapify"


class PlaceImportRequest(PlaceSearchResult):
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class LogisticsEstimateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_id: UUID
    mode: GeoapifyRouteMode = "walk"
    buffer_minutes: int = Field(default=15, ge=0, le=120)


class LogisticsLegResponse(BaseModel):
    origin_item_id: UUID
    origin_title: str
    destination_item_id: UUID
    destination_title: str
    duration_seconds: int = Field(ge=0)
    distance_meters: float = Field(ge=0)
    available_gap_seconds: int
    buffer_minutes: int = Field(ge=0)
    warning: bool
    geometry: list[list[float]] = Field(min_length=2)


class LogisticsEstimateResponse(BaseModel):
    provider: Literal["geoapify"] = "geoapify"
    day_id: UUID
    mode: GeoapifyRouteMode
    buffer_minutes: int = Field(ge=0)
    generated_at: datetime
    legs: list[LogisticsLegResponse]
