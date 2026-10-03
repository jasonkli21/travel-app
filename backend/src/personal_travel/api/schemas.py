from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

ItemType = Literal["activity", "food", "lodging", "transport", "flight", "note"]
ItemStatus = Literal["tentative", "planned", "booked", "completed", "cancelled"]
LocalTime = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"),
]


def _trim_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class TripCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date
    timezone: str = Field(default="UTC", min_length=1, max_length=64)


class TripUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)


class DayUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class PlaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=240)
    address: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def validate_coordinates(self) -> "PlaceCreate":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self

    @field_validator("address")
    @classmethod
    def normalize_address(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    item_type: ItemType = "activity"
    title: str = Field(min_length=1, max_length=240)
    notes: str | None = None
    start_time: LocalTime | None = None
    end_time: LocalTime | None = None
    status: ItemStatus = "tentative"
    place_id: UUID | None = None

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class ItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    item_type: ItemType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    notes: str | None = None
    start_time: LocalTime | None = None
    end_time: LocalTime | None = None
    status: ItemStatus | None = None
    place_id: UUID | None = None

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class MoveItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_day_id: UUID
    position: int = Field(ge=0)


class PlaceSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    address: str | None
    latitude: float | None
    longitude: float | None


class ItineraryItemResponse(BaseModel):
    id: UUID
    item_type: ItemType
    title: str
    notes: str | None
    start_time: str | None
    end_time: str | None
    sort_order: int
    status: ItemStatus
    place: PlaceSummaryResponse | None


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


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, object] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
