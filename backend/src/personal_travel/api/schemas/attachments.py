from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from personal_travel.api.schemas.common import _trim_optional


class AttachmentResponse(BaseModel):
    id: UUID
    trip_id: UUID | None
    reservation_id: UUID | None
    display_filename: str
    media_type: Literal["text/plain", "application/pdf", "image/jpeg", "image/png"]
    byte_size: int = Field(gt=0, le=10 * 1024 * 1024)
    state: Literal["pending", "ready", "deleting", "missing"]
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    trip_revision: int = Field(ge=0)
    download_available: bool = False


class AttachmentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_filename: str | None = Field(default=None, max_length=120)
    reservation_id: UUID | None = None

    @field_validator("display_filename")
    @classmethod
    def normalize_filename(cls, value: str | None) -> str | None:
        return _trim_optional(value)

    @model_validator(mode="after")
    def require_change(self) -> "AttachmentUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("Provide a document label or reservation link to update.")
        return self


class TripExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["ics", "html", "json"]
    start_date: date | None = None
    end_date: date | None = None
    include_private_fields: bool = False
    include_documents: bool = False
    include_linked_reservations: bool = False

    @model_validator(mode="after")
    def validate_scope(self) -> "TripExportRequest":
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("start_date and end_date must be provided together.")
        if self.start_date is not None and self.end_date is not None:
            if self.start_date > self.end_date:
                raise ValueError("start_date must be on or before end_date.")
        if self.include_linked_reservations and self.format != "ics":
            raise ValueError("include_linked_reservations is only supported for ICS exports.")
        return self
