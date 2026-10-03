from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from personal_travel.domain.types import ItemStatus, ItemType

from .common import LOCAL_TIME_DESCRIPTION, LocalTime, _trim_optional
from .places import PlaceSummaryResponse
from .reservations import ReservationSummaryResponse


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    item_type: ItemType = "activity"
    title: str = Field(min_length=1, max_length=240)
    notes: str | None = Field(default=None, max_length=10000)
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
    notes: str | None = Field(default=None, max_length=10000)
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
