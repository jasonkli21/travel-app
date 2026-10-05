"""Strict consumer models for the accepted Phase 7 domain comparison contract."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from personal_travel.domain.urls import validate_http_url


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _TextValue(_StrictModel):
    kind: Literal["text"] = "text"
    value: str = Field(min_length=1, max_length=500)


class _LocationValue(_StrictModel):
    kind: Literal["location"] = "location"
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class _NumberValue(_StrictModel):
    kind: Literal["number"] = "number"
    value: Decimal

    @field_validator("value")
    @classmethod
    def finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("typed number must be finite")
        return value


class _BooleanValue(_StrictModel):
    kind: Literal["boolean"] = "boolean"
    value: bool


class _DateValue(_StrictModel):
    kind: Literal["date"] = "date"
    value: date


class _DateWindowValue(_StrictModel):
    kind: Literal["date_window"] = "date_window"
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end < self.start:
            raise ValueError("typed date window is invalid")
        return self


class _AvailabilityValue(_StrictModel):
    kind: Literal["availability"] = "availability"
    value: Literal["available", "unavailable", "unknown"]


class _MoneyValue(_StrictModel):
    kind: Literal["money"] = "money"
    amount: Decimal = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("amount")
    @classmethod
    def finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("typed amount must be finite")
        return value


class _QuantityValue(_StrictModel):
    kind: Literal["quantity"] = "quantity"
    amount: Decimal = Field(ge=0)
    unit: str = Field(min_length=1, max_length=30)

    @field_validator("amount")
    @classmethod
    def finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("typed quantity must be finite")
        return value


class _DateTimeValue(_StrictModel):
    kind: Literal["datetime"] = "datetime"
    value: datetime


_TypedValue = Annotated[
    _TextValue
    | _LocationValue
    | _NumberValue
    | _BooleanValue
    | _DateValue
    | _DateWindowValue
    | _AvailabilityValue
    | _MoneyValue
    | _QuantityValue
    | _DateTimeValue,
    Field(discriminator="kind"),
]


class _RankingPolicy(_StrictModel):
    policy_version: Literal["rank-v1"] = "rank-v1"
    feature_weights: dict[str, float]
    normalization: Literal["weighted_mean-v1"] = "weighted_mean-v1"
    tie_breakers: tuple[Literal["score_desc", "normalized_name_asc", "entity_id_asc"], ...] = (
        "score_desc",
        "normalized_name_asc",
        "entity_id_asc",
    )


class _PolicyVersions(_StrictModel):
    identity: Literal["identity-v1"] = "identity-v1"
    resolution: Literal["resolve-v1", "resolve-v2"] = "resolve-v2"
    claim_verification: Literal["claim-verification-v1", "claim-verification-v2"] = (
        "claim-verification-v1"
    )
    constraints: Literal["constraint-v1"] = "constraint-v1"
    ranking: Literal["rank-v1"] = "rank-v1"
    domain_features: str | None = Field(default=None, max_length=100)
    evidence_snapshot: Literal["evidence-snapshot-v1"] = "evidence-snapshot-v1"
    entity_match_threshold: float = Field(ge=0, le=1)
    ranking_policy: _RankingPolicy
    max_candidates: int = Field(ge=0, le=24)
    max_comparison_rows: int = Field(ge=0, le=24)


class _Constraint(_StrictModel):
    id: UUID
    attribute: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]*$")
    operator: Literal[
        "exact",
        "maximum",
        "minimum",
        "range",
        "set",
        "geospatial",
        "date_window",
        "availability",
    ]
    value: _TypedValue | None = None
    upper_value: _TypedValue | None = None
    allowed_values: tuple[_TypedValue, ...] = Field(default=(), max_length=20)
    radius_km: float | None = Field(default=None, gt=0, le=20_000)
    required: bool = True
    missing_policy: Literal["fail_closed", "allow_unknown"] = "fail_closed"
    source: Literal["user", "system"] = "user"
    source_record_id: str | None = Field(default=None, max_length=200)
    scope: str | None = Field(default=None, max_length=100)


class _Preference(_StrictModel):
    attribute: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]*$")
    target: _TypedValue
    weight: float = Field(ge=0, le=1)
    source: Literal["user"]
    scope: str | None = Field(default=None, max_length=100)


class _DomainField(_StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$", max_length=100)
    label: str = Field(min_length=1, max_length=100)
    value_kinds: tuple[str, ...] = Field(min_length=1, max_length=10)
    mutable: bool = True
    required_for_recommendation: bool = False


class _Registration(_StrictModel):
    schema_version: Literal["domain-module-v1"] = "domain-module-v1"
    domain_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,40}$")
    supported_entity_types: tuple[str, ...] = Field(min_length=1, max_length=8)
    supported_constraints: tuple[str, ...] = Field(max_length=40)
    supported_features: tuple[str, ...] = Field(max_length=20)
    source_policy_version: str = Field(min_length=1, max_length=100)
    field_schema_version: str = Field(min_length=1, max_length=100)
    feature_policy_version: str = Field(min_length=1, max_length=100)
    fields: tuple[_DomainField, ...] = Field(min_length=1, max_length=32)
    source_adapters: tuple[str, ...] = Field(min_length=1, max_length=12)
    privacy_policy: str = Field(min_length=1, max_length=500)
    retention_policy: str = Field(min_length=1, max_length=500)
    enabled: bool = False


class _ComparisonSource(_StrictModel):
    evidence_id: UUID
    source_observation_id: UUID
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=300)
    attribution: str | None = Field(default=None, max_length=500)
    policy_url: str | None = Field(default=None, max_length=2048)
    observed_at: datetime
    expires_at: datetime

    @field_validator("url", "policy_url")
    @classmethod
    def public_url(cls, value: str | None) -> str | None:
        return validate_http_url(value)

    @field_validator("observed_at", "expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("comparison timestamps require a timezone")
        return value


class _ComparisonCell(_StrictModel):
    field: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=100)
    value: str | None = None
    status: Literal[
        "verified",
        "conflicting",
        "stale",
        "missing",
        "unverified",
        "derived",
    ]
    note: str | None = Field(default=None, max_length=300)
    claim_ids: tuple[UUID, ...] = Field(default=(), max_length=40)
    sources: tuple[_ComparisonSource, ...] = Field(default=(), max_length=24)


class _FeatureScore(_StrictModel):
    name: str = Field(min_length=1, max_length=100)
    value: float | None = Field(ge=0, le=1)
    weight: float = Field(ge=0, le=1)
    evidence_ids: tuple[UUID, ...] = Field(default=(), max_length=250)
    missing_treatment: Literal["zero", "omit"] = "zero"


class _ComparisonRow(_StrictModel):
    candidate_id: UUID
    name: str = Field(min_length=1, max_length=300)
    eligible: bool
    selected: bool
    rank: int | None = None
    score: float | None = Field(default=None, ge=0, le=1)
    exclusion_reasons: tuple[str, ...] = Field(default=(), max_length=30)
    cells: tuple[_ComparisonCell, ...] = Field(max_length=32)
    features: tuple[_FeatureScore, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def eligibility_is_consistent(self) -> Self:
        if self.eligible and self.exclusion_reasons:
            raise ValueError("eligible comparison row has exclusion reasons")
        if not self.eligible and self.rank is not None:
            raise ValueError("ineligible comparison row has a rank")
        return self


class _ComparisonSnapshot(_StrictModel):
    id: UUID
    decision_id: UUID
    owner_id: str
    domain_id: str
    candidate_ids: tuple[UUID, ...] = Field(max_length=24)
    field_schema_version: str
    feature_policy_version: str
    decision_policy_versions: _PolicyVersions
    constraints: tuple[_Constraint, ...] = Field(default=(), max_length=30)
    preferences: tuple[_Preference, ...] = Field(default=(), max_length=20)
    rendered_at: datetime
    state: Literal[
        "recommended",
        "eligible_unranked",
        "research_needed",
        "no_verified_match",
    ]
    rows: tuple[_ComparisonRow, ...] = Field(max_length=24)
    available_filters: tuple[str, ...] = Field(
        default=("eligible", "fresh", "conflicts"), max_length=12
    )

    @model_validator(mode="after")
    def rows_match_candidates(self) -> Self:
        if self.candidate_ids != tuple(row.candidate_id for row in self.rows):
            raise ValueError("comparison rows must match candidate IDs")
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("comparison candidate IDs must be unique")
        return self


class _ProviderObservation(_StrictModel):
    source_observation_id: UUID
    evidence_id: UUID
    owner_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,80}$")
    provider_object_id: str | None = Field(default=None, max_length=300)
    entity_kind: str = Field(min_length=1, max_length=100)
    adapter_version: str = Field(min_length=1, max_length=100)
    attribution: str = Field(min_length=1, max_length=500)
    policy_url: str | None = Field(default=None, max_length=2048)
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=300)
    observed_at: datetime
    expires_at: datetime

    @field_validator("url", "policy_url")
    @classmethod
    def public_url(cls, value: str | None) -> str | None:
        return validate_http_url(value)

    @field_validator("observed_at", "expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("provider timestamps require a timezone")
        return value

    @model_validator(mode="after")
    def valid_observation_lifecycle(self) -> Self:
        if self.expires_at <= self.observed_at:
            raise ValueError("provider observation lifecycle is invalid")
        return self


class _DomainClaim(_StrictModel):
    claim_id: UUID
    domain_id: str
    attribute: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]*$")
    typed_value: _TypedValue
    original_value: str = Field(min_length=1, max_length=500)
    evidence_ids: tuple[UUID, ...] = Field(min_length=1, max_length=12)
    observed_at: datetime
    expires_at: datetime
    schema_version: Literal["domain-claim-v1"] = "domain-claim-v1"

    @field_validator("observed_at", "expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("claim timestamps require a timezone")
        return value

    @model_validator(mode="after")
    def valid_claim_lifecycle(self) -> Self:
        if self.expires_at <= self.observed_at or len(set(self.evidence_ids)) != len(
            self.evidence_ids
        ):
            raise ValueError("comparison claim lifecycle is invalid")
        return self


class UpstreamTravelComparison(_StrictModel):
    schema_version: Literal["domain-comparison-v1"]
    registration: _Registration
    comparison: _ComparisonSnapshot
    provider_observations: tuple[_ProviderObservation, ...] = Field(max_length=24)
    domain_claims: tuple[_DomainClaim, ...] = Field(max_length=200)


class VerifiedPlaceSource(_StrictModel):
    """Provider identity that has passed the accepted OSM rights contract."""

    provider: Literal["osm_nominatim"]
    provider_place_id: str = Field(pattern=r"^(node|way|relation)/[0-9]{1,30}$", max_length=40)
    provider_source_name: Literal["OpenStreetMap"] = "OpenStreetMap"
    provider_source_attribution: Literal["© OpenStreetMap contributors, ODbL 1.0"]
    provider_source_license: Literal["ODbL 1.0"] = "ODbL 1.0"
    provider_source_url: str = Field(min_length=1, max_length=500)
    policy_url: Literal["https://operations.osmfoundation.org/policies/nominatim/"]

    @field_validator("provider_source_url")
    @classmethod
    def openstreetmap_url(cls, value: str) -> str:
        validate_http_url(value)
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname != "www.openstreetmap.org":
            raise ValueError("unexpected OpenStreetMap source URL")
        return value


TravelComparisonCategory = Literal["food", "activity", "neighborhood", "day_trip"]


class TravelComparisonCategorySpec(_StrictModel):
    category: TravelComparisonCategory
    allowed_place_types: tuple[str, ...]
    search_hint: str


TRAVEL_COMPARISON_CATEGORIES: dict[TravelComparisonCategory, TravelComparisonCategorySpec] = {
    "food": TravelComparisonCategorySpec(
        category="food",
        allowed_place_types=(
            "amenity/restaurant",
            "amenity/cafe",
            "amenity/bar",
            "amenity/pub",
            "amenity/fast_food",
            "amenity/food_court",
            "shop/bakery",
            "shop/ice_cream",
            "restaurant",
            "cafe",
            "bar",
            "pub",
            "fast_food",
            "food_court",
            "bakery",
            "ice_cream",
        ),
        search_hint="food places",
    ),
    "activity": TravelComparisonCategorySpec(
        category="activity",
        allowed_place_types=(
            "tourism/attraction",
            "tourism/museum",
            "leisure/park",
            "tourism/viewpoint",
            "tourism/zoo",
            "tourism/aquarium",
            "amenity/theatre",
            "leisure/theme_park",
            "tourism/gallery",
            "historic/monument",
            "attraction",
            "museum",
            "park",
            "viewpoint",
            "zoo",
            "aquarium",
            "theatre",
            "theme_park",
            "gallery",
            "monument",
        ),
        search_hint="activities and attractions",
    ),
    "neighborhood": TravelComparisonCategorySpec(
        category="neighborhood",
        allowed_place_types=(
            "place/neighbourhood",
            "place/neighborhood",
            "place/suburb",
            "place/quarter",
            "place/city_district",
            "place/borough",
            "neighbourhood",
            "neighborhood",
            "suburb",
            "quarter",
            "city_district",
            "borough",
        ),
        search_hint="neighborhoods and districts",
    ),
    "day_trip": TravelComparisonCategorySpec(
        category="day_trip",
        allowed_place_types=(
            "tourism/attraction",
            "leisure/park",
            "tourism/museum",
            "tourism/viewpoint",
            "tourism/zoo",
            "tourism/aquarium",
            "leisure/theme_park",
            "historic/monument",
            "attraction",
            "park",
            "museum",
            "viewpoint",
            "zoo",
            "aquarium",
            "theme_park",
            "monument",
        ),
        search_hint="day trip places and attractions",
    ),
}


def require_accepted_travel_contract(result: UpstreamTravelComparison, owner_id: str) -> None:
    registration = result.registration
    comparison = result.comparison
    if (
        not registration.enabled
        or registration.domain_id != "travel"
        or registration.source_policy_version != "travel-sources-v1"
        or registration.field_schema_version != "travel-comparison-v1"
        or registration.feature_policy_version != "travel-features-v2"
        or comparison.domain_id != "travel"
        or comparison.owner_id != owner_id
        or comparison.field_schema_version != "travel-comparison-v1"
        or comparison.feature_policy_version != "travel-features-v2"
        or comparison.decision_policy_versions.domain_features != "travel-features-v2"
    ):
        raise ValueError("unsupported or mismatched travel comparison contract")
    if not {"place_type", "location"}.issubset(registration.supported_constraints):
        raise ValueError("travel comparison contract lacks required constraints")
    if "place" not in registration.supported_entity_types:
        raise ValueError("travel comparison contract lacks place candidates")
    fields = {item.key: item for item in registration.fields}
    if (
        fields.get("place_type") is None
        or "text" not in fields["place_type"].value_kinds
        or fields.get("location") is None
        or "location" not in fields["location"].value_kinds
        or "osm_nominatim" not in registration.source_adapters
        or "fake" not in registration.source_adapters
    ):
        raise ValueError(
            "travel comparison contract lacks the accepted evidence fields or adapters"
        )
    if len(comparison.rows) > 10:
        raise ValueError("travel comparison returned too many candidates")
    if any(item.owner_id != owner_id for item in result.provider_observations):
        raise ValueError("travel comparison returned a foreign source owner")
    if any(item.domain_id != "travel" for item in result.domain_claims):
        raise ValueError("travel comparison returned a foreign domain claim")
    claim_ids = [claim.claim_id for claim in result.domain_claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("travel comparison returned duplicate claims")
    observation_ids = [
        observation.source_observation_id for observation in result.provider_observations
    ]
    evidence_ids = [observation.evidence_id for observation in result.provider_observations]
    if len(observation_ids) != len(set(observation_ids)) or len(evidence_ids) != len(
        set(evidence_ids)
    ):
        raise ValueError("travel comparison returned duplicate provider observations")
