from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import time
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import personal_travel.api.routes.imports as import_routes
from personal_travel.api.schemas.booking_imports import (
    ImportConfirmationEntry,
    ImportConfirmRequest,
)
from personal_travel.auth.contracts import VerifiedPrincipal
from personal_travel.auth.google_oidc import stable_google_owner_id
from personal_travel.auth.sessions import create_session
from personal_travel.clients.personal_ai import (
    PersonalAIExtractionError,
    PersonalAIExtractionUnknown,
)
from personal_travel.config import Settings
from personal_travel.domain.upstream_extractions import (
    UpstreamBookingCandidate,
    UpstreamBookingExtractionResult,
)
from personal_travel.models.import_source import BookingImport
from personal_travel.models.reservation import Reservation
from personal_travel.models.trip import Trip
from personal_travel.services.booking_imports import BookingImportService
from personal_travel.services.errors import DomainError
from personal_travel.services.source_store import LocalSourceStore


@pytest.fixture
def private_import_client(
    api_client: TestClient,
    database_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    settings = Settings(
        travel_auth_mode="google_oidc",
        google_oauth_client_id="synthetic-client.apps.googleusercontent.com",
        google_oauth_client_secret="synthetic-secret",
        google_oauth_redirect_uri="https://travel.test/auth/google/callback",
        google_oauth_allowed_email="owner@gmail.com",
        private_imports_enabled=True,
        private_source_dir=str(root),
    )
    settings.personal_ai_extractions_enabled = True
    monkeypatch.setattr(import_routes, "get_settings", lambda: settings)
    api_client.app.state.auth_settings_provider = lambda: settings
    subject = "synthetic-booking-import-test"
    principal = VerifiedPrincipal(
        issuer="https://accounts.google.com",
        subject=subject,
        owner_id=stable_google_owner_id("https://accounts.google.com", subject),
        email="owner@gmail.com",
        issued_at=int(time()),
        expires_at=int(time()) + 3600,
    )
    with Session(database_engine) as session, session.begin():
        token, csrf, _ = create_session(session, principal, ttl_seconds=3600)
    api_client.cookies.set("__Host-travel_session", token)
    api_client.cookies.set("__Host-travel_csrf", csrf)
    headers = {
        "Origin": "http://localhost:3000",
        "X-CSRF-Token": csrf,
        "X-Import-Request-Key": "request_booking_001",
        "Content-Type": "text/plain",
    }
    trip = api_client.post(
        "/v1/trips",
        headers={key: value for key, value in headers.items() if key != "Content-Type"},
        json={
            "title": "Synthetic booking review",
            "start_date": "2026-10-04",
            "end_date": "2026-10-05",
            "timezone": "UTC",
        },
    )
    assert trip.status_code == 201, trip.text
    return api_client, database_engine, root, principal.owner_id, trip.json()["id"], headers


def extraction_settings(root: Path) -> Settings:
    settings = Settings(
        travel_auth_mode="google_oidc",
        google_oauth_client_id="synthetic-client.apps.googleusercontent.com",
        google_oauth_client_secret="synthetic-secret",
        google_oauth_redirect_uri="https://travel.test/auth/google/callback",
        google_oauth_allowed_email="owner@gmail.com",
        private_imports_enabled=True,
        private_source_dir=str(root),
    )
    settings.personal_ai_extractions_enabled = True
    return settings


class FakeExtractionClient:
    def __init__(self, *, fail_first_post: bool = False, candidate_count: int = 1):
        self.fail_first_post = fail_first_post
        self.candidate_count = candidate_count
        self.posts = 0
        self.gets = 0
        self.deletes = 0
        self.fail_deletes = 0
        self.result: UpstreamBookingExtractionResult | None = None

    async def create_booking_extraction(self, *, payload, idempotency_key, source_sha256):
        self.posts += 1
        assert payload["source_sha256"] == source_sha256
        assert payload["synthetic_fixture"] is False
        if self.fail_first_post and self.posts == 1:
            self.result = self._result(
                idempotency_key, source_sha256, str(payload["document_text"])
            )
            raise PersonalAIExtractionUnknown("unknown")
        self.result = self._result(idempotency_key, source_sha256, str(payload["document_text"]))
        return self.result

    async def get_booking_extraction_by_key(self, key, source_sha256):
        self.gets += 1
        if self.result is not None and self.result.source_sha256 == source_sha256:
            return self.result
        return None

    async def delete_booking_extraction_by_key(self, key, source_sha256):
        self.deletes += 1
        if self.fail_deletes:
            self.fail_deletes -= 1
            raise PersonalAIExtractionError("synthetic upstream unavailable")
        assert self.result is not None
        assert self.result.idempotency_key == key
        assert self.result.source_sha256 == source_sha256
        return self.result.model_copy(update={"state": "deleted", "candidates": ()})

    def _result(self, key: UUID, source_hash: str, text: str):
        candidates = []
        for index in range(self.candidate_count):
            candidates.append(
                UpstreamBookingCandidate(
                    candidate_id=f"c_{index + 1:020x}",
                    reservation_type="lodging",
                    provider_name="Synthetic Hotel",
                    confirmation_code=f"HX-{index + 1}",
                    starts_at_text="2026-10-05 09:30 +09:00",
                    starts_at_date=date(2026, 10, 5),
                    starts_at_time="09:30",
                    starts_at_timezone="+09:00",
                    ends_at_text=None,
                    ends_at_date=None,
                    ends_at_time=None,
                    ends_at_timezone=None,
                    source_start=0,
                    source_end=len(text),
                    source_excerpt=text.strip(),
                    uncertain_fields=(),
                )
            )
        now = datetime.now(UTC)
        return UpstreamBookingExtractionResult(
            extraction_id=uuid4(),
            idempotency_key=key,
            source_sha256=source_hash,
            state="completed",
            candidates=tuple(candidates),
            created_at=now,
            expires_at=now + timedelta(days=7),
        )


def _service(engine: Engine, root: Path, fake: FakeExtractionClient) -> BookingImportService:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return BookingImportService(
        factory,
        extraction_settings(root),
        client=fake,
        source_store_factory=lambda: LocalSourceStore(root),
    )


def _import_from_upload(private_import_client):
    client, engine, root, owner, trip_id, headers = private_import_client
    text = "Synthetic Hotel booking HX-1 for 2026-10-05 09:30 +09:00"
    upload = client.post(
        f"/v1/trips/{trip_id}/imports",
        headers=headers | {"X-Source-Retention": "keep_until_expiry"},
        content=text.encode(),
    )
    assert upload.status_code == 200, upload.text
    return client, engine, root, owner, UUID(trip_id), upload.json(), text


@pytest.mark.anyio
async def test_extraction_posts_once_then_gets_same_key_after_unknown_and_converts_timezone(
    private_import_client,
):
    _, engine, root, owner, trip_id, uploaded, text = _import_from_upload(private_import_client)
    with Session(engine) as session, session.begin():
        trip = session.get(Trip, trip_id)
        assert trip is not None
        trip.timezone = "America/Los_Angeles"
    fake = FakeExtractionClient(fail_first_post=True)
    service = _service(engine, root, fake)

    unknown = await service.extract(owner, trip_id, UUID(uploaded["id"]))
    assert unknown["state"] == "extracting"
    assert unknown["outcome_unknown"] is True
    recovered = await service.extract(owner, trip_id, UUID(uploaded["id"]))

    assert fake.posts == 1
    assert fake.gets == 1
    assert recovered["state"] == "review_ready"
    assert recovered["upstream_revision"] == "ece8cfc3db044aab3b275709c12e71eb17f2520d"
    assert recovered["candidates"][0]["starts_at_trip_local"] == {
        "date": "2026-10-04",
        "time": "17:30",
    }
    with Session(engine) as session:
        item = session.get(BookingImport, UUID(uploaded["id"]))
        assert item is not None and item.extraction_post_attempted
        assert item.extraction_key is not None
        assert item.upstream_extraction_id == fake.result.extraction_id


def test_mounted_extract_response_keeps_source_lifecycle_metadata(
    private_import_client,
    monkeypatch: pytest.MonkeyPatch,
):
    client, engine, root, _, trip_id, headers = private_import_client
    text = "Booking confirmation: Synthetic Hotel"
    upload = client.post(
        f"/v1/trips/{trip_id}/imports",
        headers=headers | {"X-Source-Retention": "keep_until_expiry"},
        content=text.encode(),
    )
    assert upload.status_code == 200, upload.text
    uploaded = upload.json()
    service = _service(engine, root, FakeExtractionClient())
    monkeypatch.setattr(import_routes, "_booking_service", lambda _request: service)

    response = client.post(f"/v1/trips/{trip_id}/imports/{uploaded['id']}/extract", headers=headers)

    assert response.status_code == 200, response.text
    review = response.json()
    assert review["state"] == "review_ready"
    assert review["source_id"] == uploaded["source_id"]
    assert review["source_state"] == "ready"
    assert review["candidates"][0]["candidate_id"]


@pytest.mark.anyio
async def test_confirmation_is_once_only_atomic_and_retains_outcome_after_source_delete(
    private_import_client,
    monkeypatch: pytest.MonkeyPatch,
):
    client, engine, root, owner, trip_id, uploaded, _ = _import_from_upload(private_import_client)
    fake = FakeExtractionClient()
    service = _service(engine, root, fake)
    detail = await service.extract(owner, trip_id, UUID(uploaded["id"]))
    candidate_id = detail["candidates"][0]["candidate_id"]
    payload = ImportConfirmRequest(
        confirmation_key=uuid4(),
        expected_trip_revision=0,
        expected_import_revision=detail["review_revision"],
        entries=(ImportConfirmationEntry(candidate_id=candidate_id, decision="create_separate"),),
    )

    first = service.confirm(owner, trip_id, UUID(uploaded["id"]), payload)
    replay = service.confirm(owner, trip_id, UUID(uploaded["id"]), payload)
    assert replay == first
    assert first["outcomes"][0]["outcome"] == "created"
    assert first["upstream_revision"] == "ece8cfc3db044aab3b275709c12e71eb17f2520d"
    monkeypatch.setattr(import_routes, "_booking_service", lambda request: service)
    assert (
        client.delete(
            f"/v1/trips/{trip_id}/imports/{uploaded['id']}/source",
            headers=private_import_client[5],
        ).status_code
        == 204
    )
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Reservation).where(Reservation.trip_id == trip_id)
            )
            == 1
        )
        imported = session.get(BookingImport, UUID(uploaded["id"]))
        assert imported is not None
        assert imported.source_id is None and imported.candidate_snapshot is None
        assert imported.confirmation_outcome == first
        assert imported.upstream_delete_pending is False
    assert fake.deletes == 1

    changed = payload.model_copy(
        update={"entries": (ImportConfirmationEntry(candidate_id=candidate_id, decision="skip"),)}
    )
    with pytest.raises(DomainError) as duplicate:
        service.confirm(owner, trip_id, UUID(uploaded["id"]), changed)
    assert duplicate.value.code == "confirmation_already_final"
    assert (
        client.get(f"/v1/trips/{trip_id}/imports/{uploaded['id']}").json()["confirmation_outcome"]
        == first
    )


@pytest.mark.anyio
async def test_concurrent_confirmation_replays_one_atomic_outcome(private_import_client):
    _, engine, root, owner, trip_id, uploaded, _ = _import_from_upload(private_import_client)
    service = _service(engine, root, FakeExtractionClient())
    detail = await service.extract(owner, trip_id, UUID(uploaded["id"]))
    payload = ImportConfirmRequest(
        confirmation_key=uuid4(),
        expected_trip_revision=0,
        expected_import_revision=detail["review_revision"],
        entries=(
            ImportConfirmationEntry(
                candidate_id=detail["candidates"][0]["candidate_id"],
                decision="create_separate",
            ),
        ),
    )
    barrier = threading.Barrier(2)

    def confirm_together():
        barrier.wait(timeout=5)
        return service.confirm(owner, trip_id, UUID(uploaded["id"]), payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: confirm_together(), range(2)))

    assert outcomes[0] == outcomes[1]
    assert outcomes[0]["outcomes"][0]["outcome"] == "created"
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Reservation).where(Reservation.trip_id == trip_id)
            )
            == 1
        )


@pytest.mark.anyio
async def test_upstream_candidate_delete_retries_after_local_source_deletion(
    private_import_client,
    monkeypatch: pytest.MonkeyPatch,
):
    client, engine, root, owner, trip_id, uploaded, _ = _import_from_upload(private_import_client)
    fake = FakeExtractionClient()
    fake.fail_deletes = 1
    service = _service(engine, root, fake)
    await service.extract(owner, trip_id, UUID(uploaded["id"]))
    monkeypatch.setattr(import_routes, "_booking_service", lambda request: service)
    endpoint = f"/v1/trips/{trip_id}/imports/{uploaded['id']}/source"
    headers = private_import_client[5]

    first = client.delete(endpoint, headers=headers)
    assert first.status_code == 503
    with Session(engine) as session:
        imported = session.get(BookingImport, UUID(uploaded["id"]))
        assert imported is not None
        assert imported.source_id is None
        assert imported.candidate_snapshot == {"failure_code": "source_deleted"}
        assert imported.upstream_delete_pending is True
    detail = client.get(f"/v1/trips/{trip_id}/imports/{uploaded['id']}")
    assert detail.json()["upstream_delete_pending"] is True

    second = client.delete(endpoint, headers=headers)
    assert second.status_code == 204
    assert fake.deletes == 2
    with Session(engine) as session:
        imported = session.get(BookingImport, UUID(uploaded["id"]))
        assert imported is not None and imported.upstream_delete_pending is False


@pytest.mark.anyio
async def test_selected_batch_rolls_back_on_late_invalid_place_and_dst_gap_is_rejected(
    private_import_client,
):
    _, engine, root, owner, trip_id, uploaded, _ = _import_from_upload(private_import_client)
    fake = FakeExtractionClient(candidate_count=2)
    service = _service(engine, root, fake)
    detail = await service.extract(owner, trip_id, UUID(uploaded["id"]))
    first_id, second_id = [candidate["candidate_id"] for candidate in detail["candidates"]]
    payload = ImportConfirmRequest(
        confirmation_key=uuid4(),
        expected_trip_revision=0,
        expected_import_revision=detail["review_revision"],
        entries=(
            ImportConfirmationEntry(candidate_id=first_id, decision="create_separate"),
            ImportConfirmationEntry(
                candidate_id=second_id, decision="create_separate", place_id=uuid4()
            ),
        ),
    )
    with pytest.raises(DomainError):
        service.confirm(owner, trip_id, UUID(uploaded["id"]), payload)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Reservation)) == 0
        item = session.get(BookingImport, UUID(uploaded["id"]))
        assert item is not None and item.state == "review_ready"

    gap = payload.model_copy(
        update={
            "entries": (
                ImportConfirmationEntry(
                    candidate_id=first_id,
                    decision="create_separate",
                    starts_at_date="2026-03-08",
                    starts_at_time="02:30",
                    starts_at_timezone="America/Los_Angeles",
                ),
                ImportConfirmationEntry(candidate_id=second_id, decision="skip"),
            )
        }
    )
    with Session(engine) as session, session.begin():
        trip = session.get(Trip, trip_id)
        assert trip is not None
        trip.timezone = "America/Los_Angeles"
    gap = gap.model_copy(update={"expected_import_revision": detail["review_revision"]})
    with pytest.raises(DomainError, match="daylight-saving-time gap"):
        service.confirm(owner, trip_id, UUID(uploaded["id"]), gap)


def test_schedule_requires_explicit_timezone_and_rejects_ambiguous_dst():
    from personal_travel.services.booking_imports import _source_instant

    with pytest.raises(DomainError, match="Choose a timezone"):
        _source_instant("2026-10-05", "09:30", None, field="start time")
    with pytest.raises(DomainError, match="ambiguous"):
        _source_instant("2026-11-01", "01:30", "America/Los_Angeles", field="start time")


@pytest.mark.anyio
async def test_confirmation_requires_an_explicit_choice_for_every_candidate(private_import_client):
    _, engine, root, owner, trip_id, uploaded, _ = _import_from_upload(private_import_client)
    service = _service(engine, root, FakeExtractionClient(candidate_count=2))
    detail = await service.extract(owner, trip_id, UUID(uploaded["id"]))
    first = detail["candidates"][0]["candidate_id"]
    payload = ImportConfirmRequest(
        confirmation_key=uuid4(),
        expected_trip_revision=0,
        expected_import_revision=detail["review_revision"],
        entries=(ImportConfirmationEntry(candidate_id=first, decision="skip"),),
    )
    with pytest.raises(DomainError) as missing_choice:
        service.confirm(owner, trip_id, UUID(uploaded["id"]), payload)
    assert missing_choice.value.code == "invalid_confirmation"
    with Session(engine) as session:
        imported = session.get(BookingImport, UUID(uploaded["id"]))
        assert imported is not None and imported.confirmation_key is None
