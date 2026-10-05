"""Owner-reviewed booking extraction and confirmation contracts."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_travel.domain.types import ReservationType

ZoneName = str | None
ReservationStatus = Literal["tentative", "confirmed"]


class ImportCandidateEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_id: str = Field(min_length=12, max_length=32, pattern=r"^c_[a-f0-9]{10,30}$")
    reservation_type: ReservationType | None = None
    reservation_status: ReservationStatus | None = None
    provider_name: str | None = Field(default=None, max_length=200)
    confirmation_code: str | None = Field(default=None, max_length=160)
    starts_at_date: date | None = None
    starts_at_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    starts_at_timezone: ZoneName = Field(default=None, max_length=64)
    ends_at_date: date | None = None
    ends_at_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    ends_at_timezone: ZoneName = Field(default=None, max_length=64)


class ImportEditsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_import_revision: int = Field(ge=0)
    edits: tuple[ImportCandidateEdit, ...] = Field(max_length=10)


class ImportConfirmationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_id: str = Field(min_length=12, max_length=32, pattern=r"^c_[a-f0-9]{10,30}$")
    decision: Literal["create_separate", "link_existing", "skip"]
    existing_reservation_id: UUID | None = None
    place_id: UUID | None = None
    itinerary_item_id: UUID | None = None
    provider_name: str | None = Field(default=None, max_length=200)
    confirmation_code: str | None = Field(default=None, max_length=160)
    reservation_type: ReservationType | None = None
    reservation_status: ReservationStatus | None = None
    starts_at_date: date | None = None
    starts_at_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    starts_at_timezone: ZoneName = Field(default=None, max_length=64)
    ends_at_date: date | None = None
    ends_at_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    ends_at_timezone: ZoneName = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def valid_choice(self) -> "ImportConfirmationEntry":
        if self.decision == "link_existing" and self.existing_reservation_id is None:
            raise ValueError("link_existing requires an existing_reservation_id")
        if self.decision != "link_existing" and self.existing_reservation_id is not None:
            raise ValueError("existing_reservation_id is only valid for link_existing")
        if self.decision == "create_separate" and self.reservation_status is None:
            raise ValueError("create_separate requires an explicit reservation_status")
        if self.decision != "create_separate" and self.reservation_status is not None:
            raise ValueError("reservation_status is only valid for create_separate")
        return self


class ImportConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    confirmation_key: UUID
    expected_trip_revision: int = Field(ge=0)
    expected_import_revision: int = Field(ge=0)
    entries: tuple[ImportConfirmationEntry, ...] = Field(max_length=10)
