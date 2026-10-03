from datetime import date, datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from personal_travel.domain.types import (
    ReservationStatus,
    ReservationType,
)

from .common import LocalTime, _trim_optional
from .places import PlaceSummaryResponse


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
    notes: str | None = Field(default=None, max_length=10000)

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
    notes: str | None = Field(default=None, max_length=10000)

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


class ReservationSummaryResponse(BaseModel):
    id: UUID
    reservation_type: ReservationType
    status: ReservationStatus
    provider_name: str
    confirmation_code: str | None
    conflict_count: int = Field(ge=0)


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
