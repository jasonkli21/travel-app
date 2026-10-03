from datetime import date, datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
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
ReservationType = Literal["lodging", "flight", "train", "car_rental", "activity", "dining", "other"]
ReservationStatus = Literal["tentative", "confirmed", "cancelled"]
GeoapifyRouteMode = Literal["walk", "drive", "bicycle", "transit"]
ResearchFreshness = Literal["general", "current"]
ResearchState = Literal["pending", "running", "completed", "insufficient", "failed", "expired"]
LocalTime = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"),
    Field(description="Local wall-clock time on the owning trip day, formatted as HH:MM."),
]
LOCAL_TIME_DESCRIPTION = (
    "Local wall-clock time on the owning trip day, formatted as HH:MM; "
    "null means the item is untimed."
)


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


class PlaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=240)
    address: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=64)
    website_url: str | None = Field(default=None, max_length=500)
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

    @field_validator("category", "phone", "website_url")
    @classmethod
    def normalize_optional_metadata(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class ManualSavedPlaceCreate(PlaceCreate):
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class PlaceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=240)
    address: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=64)
    website_url: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def validate_coordinates(self) -> "PlaceUpdate":
        coordinate_fields = {"latitude", "longitude"}
        if coordinate_fields.intersection(self.model_fields_set) and not coordinate_fields.issubset(
            self.model_fields_set
        ):
            raise ValueError("latitude and longitude must be provided together in an update")
        return self

    @field_validator("name", "address", "category", "phone", "website_url")
    @classmethod
    def normalize_optional_metadata(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    item_type: ItemType = "activity"
    title: str = Field(min_length=1, max_length=240)
    notes: str | None = None
    start_time: LocalTime | None = Field(default=None, description=LOCAL_TIME_DESCRIPTION)
    end_time: LocalTime | None = Field(default=None, description=LOCAL_TIME_DESCRIPTION)
    status: ItemStatus = "tentative"
    place_id: UUID | None = None
    reservation_id: UUID | None = None

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class ItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    item_type: ItemType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    notes: str | None = None
    start_time: LocalTime | None = Field(default=None, description=LOCAL_TIME_DESCRIPTION)
    end_time: LocalTime | None = Field(default=None, description=LOCAL_TIME_DESCRIPTION)
    status: ItemStatus | None = None
    place_id: UUID | None = None
    reservation_id: UUID | None = None

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class MoveItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_day_id: UUID
    position: int = Field(ge=0)


def _validate_reservation_schedule(
    start_date: date | None,
    start_time: str | None,
    end_date: date | None,
    end_time: str | None,
) -> None:
    if (start_date is None) != (start_time is None):
        raise ValueError("start_date and start_time must be provided together")
    if (end_date is None) != (end_time is None):
        raise ValueError("end_date and end_time must be provided together")
    if end_date is not None and start_date is None:
        raise ValueError("an end schedule requires a start schedule")
    if start_date is not None and end_date is not None:
        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")
        if end_date == start_date and start_time is not None and end_time is not None:
            if start_time > end_time:
                raise ValueError("end_time must be on or after start_time on the same date")


class ReservationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reservation_type: ReservationType = "other"
    status: ReservationStatus = "tentative"
    provider_name: str = Field(min_length=1, max_length=200)
    confirmation_code: str | None = Field(default=None, max_length=160)
    start_date: date | None = None
    start_time: LocalTime | None = None
    end_date: date | None = None
    end_time: LocalTime | None = None
    place_id: UUID | None = None
    source_reference: str | None = Field(default=None, max_length=500)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_schedule(self) -> "ReservationCreate":
        _validate_reservation_schedule(
            self.start_date, self.start_time, self.end_date, self.end_time
        )
        return self

    @field_validator("confirmation_code", "source_reference", "notes")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class ReservationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reservation_type: ReservationType | None = None
    status: ReservationStatus | None = None
    provider_name: str | None = Field(default=None, min_length=1, max_length=200)
    confirmation_code: str | None = Field(default=None, max_length=160)
    start_date: date | None = None
    start_time: LocalTime | None = None
    end_date: date | None = None
    end_time: LocalTime | None = None
    place_id: UUID | None = None
    source_reference: str | None = Field(default=None, max_length=500)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_schedule_patch(self) -> "ReservationUpdate":
        schedule_fields = {"start_date", "start_time", "end_date", "end_time"}
        present = schedule_fields.intersection(self.model_fields_set)
        if present and present != schedule_fields:
            raise ValueError("reservation schedule fields must be provided together in an update")
        if present:
            _validate_reservation_schedule(
                self.start_date, self.start_time, self.end_date, self.end_time
            )
        return self

    @field_validator("provider_name", "confirmation_code", "source_reference", "notes")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class PlaceSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    address: str | None
    category: str | None
    phone: str | None
    website_url: str | None
    latitude: float | None
    longitude: float | None
    provider: str | None
    provider_place_id: str | None
    provider_source_name: str | None
    provider_source_attribution: str | None
    provider_source_license: str | None
    provider_source_url: str | None


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
        if value is None:
            return None
        try:
            parsed = urlsplit(value)
        except ValueError:
            raise ValueError("provider_source_url must be an HTTP or HTTPS URL") from None
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("provider_source_url must be an HTTP or HTTPS URL")
        return value


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


class ReservationSummaryResponse(BaseModel):
    id: UUID
    reservation_type: ReservationType
    status: ReservationStatus
    provider_name: str
    confirmation_code: str | None
    conflict_count: int = Field(ge=0)


class ItineraryItemResponse(BaseModel):
    id: UUID
    item_type: ItemType
    title: str
    notes: str | None
    start_time: str | None = Field(
        default=None,
        description=(
            "Local wall-clock start time on the item's trip day, or null for date-only items."
        ),
    )
    end_time: str | None = Field(
        default=None,
        description=(
            "Local wall-clock end time on the item's trip day, "
            "or null for open-ended/date-only items."
        ),
    )
    sort_order: int
    status: ItemStatus
    place: PlaceSummaryResponse | None
    reservation: ReservationSummaryResponse | None = None


class ReservationLinkedItemResponse(BaseModel):
    id: UUID
    title: str
    day_id: UUID
    day_index: int
    date: date


class ReservationConflictResponse(BaseModel):
    item_id: UUID
    day_id: UUID
    day_index: int
    date: date
    title: str
    start_time: str | None
    end_time: str | None
    reason: str


class ReservationResponse(BaseModel):
    id: UUID
    reservation_type: ReservationType
    status: ReservationStatus
    provider_name: str
    confirmation_code: str | None
    start_date: date | None
    start_time: str | None
    end_date: date | None
    end_time: str | None
    place: PlaceSummaryResponse | None
    source_reference: str | None
    notes: str | None
    linked_items: list[ReservationLinkedItemResponse]
    conflicts: list[ReservationConflictResponse]
    created_at: datetime
    updated_at: datetime


class SavedPlaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    place_id: UUID
    note: str | None = None

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class SavedPlaceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    note: str | None = None

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class SavedPlaceResponse(BaseModel):
    id: UUID
    note: str | None
    place: PlaceSummaryResponse
    created_at: datetime
    updated_at: datetime


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
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
        except ValueError:
            raise ValueError("citation URL must be HTTP or HTTPS") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("citation URL must be HTTP or HTTPS")
        return value


class TripResearchResponse(BaseModel):
    schema_version: Literal["trip-research-v1"] = "trip-research-v1"
    session_id: UUID
    state: ResearchState
    answer: str | None = Field(default=None, max_length=20000)
    failure_code: str | None = Field(default=None, max_length=80)
    expires_at: datetime
    citations: list[ResearchCitationResponse] = Field(default_factory=list, max_length=144)


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
    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Safe human-readable error message.")
    details: dict[str, object] | None = Field(
        default=None,
        description="Optional structured details safe for clients to display or inspect.",
    )


class ErrorResponse(BaseModel):
    error: ErrorBody


COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "model": ErrorResponse,
        "description": "The requested resource was not found for the configured owner.",
    },
    409: {
        "model": ErrorResponse,
        "description": "The requested change conflicts with existing travel data.",
    },
    422: {
        "model": ErrorResponse,
        "description": "The request body or domain values are invalid.",
    },
}
