from collections.abc import Iterator
from os import environ

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from personal_travel.api.schemas import ManualSavedPlaceCreate, TripCreate
from personal_travel.db.base import Base
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.place import Place
from personal_travel.models.reservation import Reservation, SavedPlace
from personal_travel.models.trip import Trip, TripDay
from personal_travel.services.errors import DomainError
from personal_travel.services.saved_places import SavedPlaceService
from personal_travel.services.trips import TripService

TEST_DATABASE_URL = environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="set TEST_DATABASE_URL to run PostgreSQL-backed Phase 4 tests",
)


@pytest.fixture(scope="session")
def database_engine() -> Iterator[Engine]:
    assert TEST_DATABASE_URL is not None
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(database_engine: Engine) -> Iterator[None]:
    with database_engine.begin() as connection:
        connection.execute(delete(SavedPlace))
        connection.execute(delete(ItineraryItem))
        connection.execute(delete(Reservation))
        connection.execute(delete(TripDay))
        connection.execute(delete(Trip))
        connection.execute(delete(Place))
    yield


def test_manual_saved_place_creates_owner_place_and_trip_candidate_atomically(
    database_engine: Engine,
) -> None:
    with Session(database_engine, expire_on_commit=False) as session:
        trip = TripService(session, "local").create(
            TripCreate(
                title="Manual candidate test",
                start_date="2026-10-03",
                end_date="2026-10-03",
                timezone="UTC",
            )
        )
        saved = SavedPlaceService(session, "local").create_manual(
            trip.id,
            ManualSavedPlaceCreate(
                name="Traveler-entered cafe",
                address="Example City",
                category="Cafe",
                note="Check opening hours",
            ),
        )
        place_id = saved.place_id
        saved_place_id = saved.id

    with Session(database_engine) as session:
        place = session.get(Place, place_id)
        saved_place = session.get(SavedPlace, saved_place_id)
        assert place is not None
        assert saved_place is not None
        assert place.owner_id == "local"
        assert place.provider is None
        assert place.name == "Traveler-entered cafe"
        assert saved_place.owner_id == "local"
        assert saved_place.trip_id == trip.id
        assert saved_place.place_id == place.id
        assert saved_place.note == "Check opening hours"
        assert session.scalar(select(func.count()).select_from(Place)) == 1
        assert session.scalar(select(func.count()).select_from(SavedPlace)) == 1


def test_manual_saved_place_rejects_foreign_trip_without_creating_place(
    database_engine: Engine,
) -> None:
    with Session(database_engine) as session:
        foreign_trip = TripService(session, "another-owner").create(
            TripCreate(
                title="Foreign trip",
                start_date="2026-10-03",
                end_date="2026-10-03",
                timezone="UTC",
            )
        )
        with pytest.raises(DomainError) as error:
            SavedPlaceService(session, "local").create_manual(
                foreign_trip.id,
                ManualSavedPlaceCreate(name="Must not be created"),
            )
        assert error.value.status_code == 404

    with Session(database_engine) as session:
        assert session.scalar(select(func.count()).select_from(Place)) == 0
        assert session.scalar(select(func.count()).select_from(SavedPlace)) == 0
