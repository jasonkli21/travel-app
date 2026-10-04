"""Strict internal DTOs for local itinerary proposal preview groundwork.

These types are not an accepted personal-ai-system wire contract and are not
mounted on an HTTP route.
"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from personal_travel.domain.types import ItemStatus, ItemType, ReservationStatus

ProposalHandle = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=10,
        max_length=66,
        pattern=r"^h_[A-Za-z0-9_-]{8,64}$",
    ),
]
LocalTimeValue = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"),
]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class StrictProposalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProposalAddItem(StrictProposalModel):
    kind: Literal["add_item"]
    day_handle: ProposalHandle
    candidate_handle: ProposalHandle
    item_type: ItemType
    position: NonNegativeInt
    start_time: LocalTimeValue | None = None
    end_time: LocalTimeValue | None = None


class ProposalMoveItem(StrictProposalModel):
    kind: Literal["move_item"]
    item_handle: ProposalHandle
    day_handle: ProposalHandle
    position: NonNegativeInt


class ProposalSetItemTimes(StrictProposalModel):
    kind: Literal["set_item_times"]
    item_handle: ProposalHandle
    start_time: LocalTimeValue | None = None
    end_time: LocalTimeValue | None = None

    @model_validator(mode="after")
    def require_time_field(self) -> "ProposalSetItemTimes":
        if not {"start_time", "end_time"}.intersection(self.model_fields_set):
            raise ValueError("at least one local time field must be supplied")
        return self


class ProposalRemoveItem(StrictProposalModel):
    kind: Literal["remove_item"]
    item_handle: ProposalHandle


ProposalOperation = Annotated[
    ProposalAddItem | ProposalMoveItem | ProposalSetItemTimes | ProposalRemoveItem,
    Field(discriminator="kind"),
]


class LocalProposalDraft(StrictProposalModel):
    """Untrusted operation-shaped data for the internal preview validator only."""

    schema_version: Literal["travel-itinerary-patch-local-v1"]
    trip_handle: ProposalHandle
    operations: tuple[ProposalOperation, ...] = Field(min_length=1, max_length=25)


class ProposalPlaceSnapshot(StrictProposalModel):
    place_id: UUID
    owner_id: str = Field(min_length=1, max_length=128)
    handle: ProposalHandle
    revision: NonNegativeInt
    name: str = Field(min_length=1, max_length=240)


class ProposalCandidateSnapshot(StrictProposalModel):
    candidate_id: UUID
    owner_id: str = Field(min_length=1, max_length=128)
    trip_id: UUID
    handle: ProposalHandle
    place_handle: ProposalHandle


class ProposalReservationSnapshot(StrictProposalModel):
    reservation_id: UUID
    owner_id: str = Field(min_length=1, max_length=128)
    trip_id: UUID
    handle: ProposalHandle
    status: ReservationStatus
    starts_at: datetime | None
    ends_at: datetime | None
    place_handle: ProposalHandle | None


class ProposalItemSnapshot(StrictProposalModel):
    item_id: UUID
    owner_id: str = Field(min_length=1, max_length=128)
    trip_id: UUID
    handle: ProposalHandle
    item_type: ItemType
    title: str = Field(min_length=1, max_length=240)
    status: ItemStatus
    sort_order: NonNegativeInt
    starts_at: datetime | None
    ends_at: datetime | None
    place_handle: ProposalHandle | None
    reservation_handle: ProposalHandle | None


class ProposalDaySnapshot(StrictProposalModel):
    day_id: UUID
    trip_id: UUID
    handle: ProposalHandle
    day_index: Annotated[int, Field(strict=True, ge=1, le=366)]
    date: date
    title: str | None = Field(max_length=200)
    items: tuple[ProposalItemSnapshot, ...] = Field(max_length=5000)


class ProposalTripSnapshot(StrictProposalModel):
    """Owner-scoped internal projection; record IDs never appear in a draft."""

    schema_version: Literal["travel-preview-context-local-v1"]
    owner_id: str = Field(min_length=1, max_length=128)
    trip_id: UUID
    trip_handle: ProposalHandle
    trip_revision: NonNegativeInt
    title: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date
    timezone: str = Field(min_length=1, max_length=64)
    days: tuple[ProposalDaySnapshot, ...] = Field(min_length=1, max_length=366)
    places: tuple[ProposalPlaceSnapshot, ...] = Field(max_length=1000)
    candidates: tuple[ProposalCandidateSnapshot, ...] = Field(max_length=500)
    reservations: tuple[ProposalReservationSnapshot, ...] = Field(max_length=500)
    removable_item_handles: tuple[ProposalHandle, ...] = Field(max_length=5000)

    @model_validator(mode="after")
    def bound_total_items(self) -> "ProposalTripSnapshot":
        if sum(len(day.items) for day in self.days) > 5000:
            raise ValueError("preview context contains too many itinerary items")
        return self
