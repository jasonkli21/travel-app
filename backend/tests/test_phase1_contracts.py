from datetime import date, time

import pytest
from pydantic import ValidationError

from personal_travel.api.schemas import ItemCreate, PlaceCreate, TripCreate, TripUpdate
from personal_travel.services.errors import DomainError
from personal_travel.services.time_utils import get_zoneinfo, resolve_local_datetime
from personal_travel.services.trips import MAX_TRIP_DAYS, validate_trip_range


def test_trip_range_is_inclusive_and_bounded() -> None:
    validate_trip_range(date(2026, 1, 1), date(2026, 12, 31))

    with pytest.raises(DomainError, match="at most"):
        validate_trip_range(date(2026, 1, 1), date(2027, 1, 2))

    assert MAX_TRIP_DAYS == 366


def test_request_contracts_trim_and_reject_invalid_values() -> None:
    trip = TripCreate(
        title="  Kyoto  ",
        start_date=date(2026, 3, 10),
        end_date=date(2026, 3, 12),
        timezone=" Asia/Tokyo ",
    )
    assert trip.title == "Kyoto"
    assert trip.timezone == "Asia/Tokyo"

    item = ItemCreate(title=" Museum ", start_time="09:00", end_time="10:30")
    assert item.title == "Museum"

    with pytest.raises(ValidationError):
        ItemCreate(title="Museum", start_time="9:00")
    with pytest.raises(ValidationError):
        PlaceCreate(name="Museum", latitude=35.0)


def test_patch_contract_preserves_missing_vs_explicit_null() -> None:
    omitted = TripUpdate(title="New title")
    cleared = TripUpdate(timezone=None)

    assert omitted.model_fields_set == {"title"}
    assert cleared.model_fields_set == {"timezone"}


def test_dst_gaps_and_folds_are_rejected() -> None:
    zone = get_zoneinfo("America/New_York")

    with pytest.raises(DomainError, match="gap"):
        resolve_local_datetime(date(2026, 3, 8), time(2, 30), zone, field_name="start_time")

    with pytest.raises(DomainError, match="ambiguous"):
        resolve_local_datetime(date(2026, 11, 1), time(1, 30), zone, field_name="start_time")
