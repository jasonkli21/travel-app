from datetime import date

import pytest
from pydantic import ValidationError

from personal_travel.api.schemas import (
    PlaceCreate,
    PlaceUpdate,
    ReservationCreate,
    ReservationUpdate,
)


def test_reservation_schedule_requires_complete_local_pairs() -> None:
    scheduled = ReservationCreate(
        provider_name="Railway",
        start_date=date(2026, 5, 10),
        start_time="23:30",
        end_date=date(2026, 5, 11),
        end_time="01:00",
    )
    assert scheduled.start_date == date(2026, 5, 10)

    with pytest.raises(ValidationError, match="start_date and start_time"):
        ReservationCreate(provider_name="Railway", start_date=date(2026, 5, 10))
    with pytest.raises(ValidationError, match="end_date and end_time"):
        ReservationCreate(
            provider_name="Railway",
            start_date=date(2026, 5, 10),
            start_time="10:00",
            end_date=date(2026, 5, 10),
        )
    with pytest.raises(ValidationError, match="on or after"):
        ReservationCreate(
            provider_name="Railway",
            start_date=date(2026, 5, 11),
            start_time="10:00",
            end_date=date(2026, 5, 10),
            end_time="11:00",
        )


def test_reservation_schedule_patch_is_atomic_and_can_clear() -> None:
    with pytest.raises(ValidationError, match="provided together"):
        ReservationUpdate(start_date=date(2026, 5, 10))

    cleared = ReservationUpdate(
        start_date=None,
        start_time=None,
        end_date=None,
        end_time=None,
    )
    assert cleared.model_fields_set == {"start_date", "start_time", "end_date", "end_time"}


def test_place_metadata_and_coordinate_updates_are_validated() -> None:
    place = PlaceCreate(
        name="  Museum  ",
        category="  Culture ",
        phone="  +1 555 0100 ",
        website_url=" https://museum.example ",
    )
    assert place.name == "Museum"
    assert place.category == "Culture"
    assert place.website_url == "https://museum.example"

    with pytest.raises(ValidationError, match="provided together"):
        PlaceUpdate(latitude=35.0)
