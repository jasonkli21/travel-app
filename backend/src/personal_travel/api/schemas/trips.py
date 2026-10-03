from datetime import date, datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from .common import _trim_optional
from .itinerary import ItineraryItemResponse


class TripCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date
    timezone: str = Field(default="UTC", min_length=1, max_length=64)


class TripUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Trip title; omit to preserve it, or provide a non-null replacement.",
    )
    start_date: date | None = Field(
        default=None,
        description="Inclusive first calendar date; omit to preserve it.",
    )
    end_date: date | None = Field(
        default=None,
        description="Inclusive last calendar date; omit to preserve it.",
    )
    timezone: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="IANA timezone name; omit to preserve it, or provide a non-null replacement.",
    )


class DayUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class TripDayResponse(BaseModel):
    id: UUID
    day_index: int
    date: date
    title: str | None
    items: list[ItineraryItemResponse]


class TripSummaryResponse(BaseModel):
    id: UUID
    title: str
    start_date: date
    end_date: date
    timezone: str
    day_count: int
    item_count: int
    created_at: datetime
    updated_at: datetime


class TripDetailResponse(TripSummaryResponse):
    days: list[TripDayResponse]
