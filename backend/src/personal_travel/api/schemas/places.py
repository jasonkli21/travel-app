from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from personal_travel.domain.urls import validate_http_url as _http_url

from .common import _trim_optional


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

    @field_validator("website_url")
    @classmethod
    def validate_website(cls, value: str | None) -> str | None:
        return _http_url(_trim_optional(value))

    @field_validator("category", "phone")
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
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must both be values or both be null")
        return self

    @field_validator("website_url")
    @classmethod
    def validate_website(cls, value: str | None) -> str | None:
        return _http_url(_trim_optional(value))

    @field_validator("name", "address", "category", "phone")
    @classmethod
    def normalize_optional_metadata(cls, value: str | None) -> str | None:
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
