from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from personal_travel.api.schemas import LogisticsEstimateRequest, PlaceImportRequest
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.place import Place
from personal_travel.models.trip import TripDay
from personal_travel.services.location import (
    LocationService,
    _EligibleLeg,
    has_logistics_warning,
)


def test_provider_place_import_requires_valid_coordinates_and_normalizes_note() -> None:
    result = PlaceImportRequest(
        provider_place_id="osm:node:42",
        name="Example Museum",
        latitude=37.7,
        longitude=-122.4,
        provider_source_name="openstreetmap",
        provider_source_attribution="© OpenStreetMap contributors",
        provider_source_license="Open Database License",
        provider_source_url="https://www.openstreetmap.org/copyright",
        note="  Visit if there is time  ",
    )
    assert result.note == "Visit if there is time"

    with pytest.raises(ValidationError):
        PlaceImportRequest(
            provider_place_id="osm:node:bad",
            name="Invalid point",
            latitude=91,
            longitude=0,
        )


def test_logistics_contract_bounds_buffer_and_mode() -> None:
    day_id = uuid4()
    assert LogisticsEstimateRequest(day_id=day_id).buffer_minutes == 15
    with pytest.raises(ValidationError):
        LogisticsEstimateRequest(day_id=day_id, buffer_minutes=121)
    with pytest.raises(ValidationError):
        LogisticsEstimateRequest(day_id=day_id, mode="scooter")


def test_transfer_warning_compares_gap_with_duration_and_buffer() -> None:
    assert not has_logistics_warning(20 * 60, 5 * 60, 15)
    assert has_logistics_warning(20 * 60 - 1, 5 * 60, 15)
    assert has_logistics_warning(-60, 5 * 60, 0)


def test_route_call_budget_rejects_fragmented_days_before_provider_work() -> None:
    now = datetime(2026, 10, 3, 9, tzinfo=UTC)
    legs = [
        _EligibleLeg(
            origin_item_id=uuid4(),
            origin_title="A",
            origin_ends_at=now,
            origin_coordinates=(37.7, -122.4),
            destination_item_id=uuid4(),
            destination_title="B",
            destination_starts_at=now,
            destination_coordinates=(37.8, -122.3),
        )
        for _ in range(50)
    ]
    groups = LocationService._group_legs(legs)
    assert len(groups) == 50
    with pytest.raises(Exception, match="more than six route-provider requests"):
        LocationService._require_bounded_route_calls(groups)


def test_logistics_only_considers_consecutive_scheduled_items_with_coordinates() -> None:
    day = TripDay(
        id=uuid4(),
        trip_id=uuid4(),
        day_index=1,
        date=date(2026, 10, 3),
    )
    start = datetime(2026, 10, 3, 9, tzinfo=UTC)
    middle = datetime(2026, 10, 3, 10, tzinfo=UTC)
    end = datetime(2026, 10, 3, 11, tzinfo=UTC)

    first = ItineraryItem(
        id=uuid4(),
        trip_day_id=day.id,
        item_type="activity",
        title="Museum",
        starts_at=start,
        ends_at=middle,
        sort_order=0,
        status="planned",
        place=Place(
            id=uuid4(),
            owner_id="local",
            name="Museum",
            latitude="37.7",
            longitude="-122.4",
        ),
    )
    second = ItineraryItem(
        id=uuid4(),
        trip_day_id=day.id,
        item_type="food",
        title="Lunch",
        starts_at=middle,
        ends_at=end,
        sort_order=1,
        status="planned",
    )
    third = ItineraryItem(
        id=uuid4(),
        trip_day_id=day.id,
        item_type="activity",
        title="Gallery",
        starts_at=end,
        ends_at=None,
        sort_order=2,
        status="planned",
        place=Place(
            id=uuid4(),
            owner_id="local",
            name="Gallery",
            latitude="37.8",
            longitude="-122.3",
        ),
    )
    day.items = [first, second, third]

    assert LocationService._eligible_legs(day) == []

    second.place = Place(
        id=uuid4(),
        owner_id="local",
        name="Lunch",
        latitude="37.75",
        longitude="-122.35",
    )
    eligible = LocationService._eligible_legs(day)
    assert [(leg.origin_title, leg.destination_title) for leg in eligible] == [
        ("Museum", "Lunch"),
        ("Lunch", "Gallery"),
    ]

    second.status = "cancelled"
    assert LocationService._eligible_legs(day) == []
