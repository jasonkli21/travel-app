from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import _trim_optional
from .places import PlaceSummaryResponse


class SavedPlaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    place_id: UUID
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class SavedPlaceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    note: str | None = Field(default=None, max_length=1000)

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
