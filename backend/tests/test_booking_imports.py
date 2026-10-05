from __future__ import annotations

import asyncio
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import time
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import personal_travel.api.routes.imports as import_routes
from personal_travel.api.schemas.booking_imports import (
    ImportCandidateEdit,
    ImportConfirmationEntry,
    ImportConfirmRequest,
    ImportEditsRequest,
)
from personal_travel.auth.contracts import VerifiedPrincipal
from personal_travel.auth.google_oidc import stable_google_owner_id
from personal_travel.auth.sessions import create_session
from personal_travel.clients.personal_ai import (
    PersonalAIExtractionError,
    PersonalAIExtractionRejected,
    PersonalAIExtractionUnknown,
)
from personal_travel.config import Settings
from personal_travel.domain.upstream_extractions import (
    UpstreamBookingCandidate,
    UpstreamBookingExtractionResult,
)
from personal_travel.models.import_source import (
    BookingDeletionIntent,
    BookingImport,
    SourceAttachment,
)
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.reservation import Reservation
from personal_travel.models.trip import Trip
from personal_travel.services.booking_imports import BookingImportService
from personal_travel.services.errors import DomainError
from personal_travel.services.private_deletion import PrivateDeletionService
from personal_travel.services.source_store import LocalSourceStore
from personal_travel.services.trips import TripService


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
    def __init__(
        self,
        *,
        fail_first_post: bool = False,
        candidate_count: int = 1,
        reject_first_post: bool = False,
    ):
        self.fail_first_post = fail_first_post
        self.candidate_count = candidate_count
        self.reject_first_post = reject_first_post
        self.posts = 0
        self.gets = 0
        self.deletes = 0
        self.fail_deletes = 0
        self.result: UpstreamBookingExtractionResult | None = None
        self.last_payload: dict[str, object] | None = None
        self.on_create = None

    async def create_booking_extraction(self, *, payload, idempotency_key, source_sha256):
        self.posts += 1
        self.last_payload = payload
        assert payload["source_sha256"] == source_sha256
        assert payload["synthetic_fixture"] is False
        if self.reject_first_post and self.posts == 1:
            raise PersonalAIExtractionRejected(422)
        if self.fail_first_post and self.posts == 1:
            self.result = self._result(
                idempotency_key, source_sha256, str(payload["document_text"])
            )
            raise PersonalAIExtractionUnknown("unknown")
        self.result = self._result(idempotency_key, source_sha256, str(payload["document_text"]))
        if self.on_create is not None:
            self.on_create()
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
        if self.result is not None:
            assert self.result.idempotency_key == key
            assert self.result.source_sha256 == source_sha256
            return self.result.model_copy(update={"state": "deleted", "candidates": ()})
        now = datetime.now(UTC)
        return UpstreamBookingExtractionResult(
            extraction_id=uuid4(),
            idempotency_key=key,
            source_sha256=source_sha256,
            state="deleted",
            candidates=(),
            created_at=now,
            expires_at=now + timedelta(days=7),
        )

    def _result(self, key: UUID, source_hash: str, text: str):
        candidates = []
        lines = text.splitlines()
        offset = 0
        for index, excerpt in enumerate(lines[: self.candidate_count]):
            start = text.find(excerpt, offset)
            offset = start + len(excerpt)
            confirmation_code = f"HX-{index + 1}"
            if confirmation_code not in excerpt:
                confirmation_code = None
            schedule_text = "2026-10-05 09:30 +09:00"
            if schedule_text not in excerpt:
                schedule_text = None
            candidates.append(
                UpstreamBookingCandidate(
                    candidate_id=f"c_{index + 1:020x}",
                    reservation_type="lodging",
                    provider_name="Synthetic Hotel",
                    confirmation_code=confirmation_code,
                    starts_at_text=schedule_text,
                    starts_at_date=date(2026, 10, 5) if schedule_text else None,
                    starts_at_time="09:30" if schedule_text else None,
                    starts_at_timezone="+09:00" if schedule_text else None,
                    ends_at_text=None,
                    ends_at_date=None,
                    ends_at_time=None,
                    ends_at_timezone=None,
                    source_start=start,
                    source_end=start + len(excerpt),
                    source_excerpt=excerpt,
                    uncertain_fields=(
                        (("confirmation_code",) if confirmation_code is None else ())
                        + (("starts_at", "starts_at_timezone") if schedule_text is None else ())
                        + ("ends_at", "ends_at_timezone")
                    ),
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


def _write_synthetic_pdf(path: Path, text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=72, height=72)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 10 Tf 1 1 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as stream:
        writer.write(stream)
    return path.read_bytes()


def _import_from_upload(private_import_client, text: str | None = None):
    client, engine, root, owner, trip_id, headers = private_import_client
    text = text or "Synthetic Hotel booking HX-1 for 2026-10-05 09:30 +09:00"
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
    assert recovered["upstream_revision"] == "ebd00a8e2fb2d8b59a5fb5fa3aa44268e5e79b63"
    assert recovered["candidates"][0]["starts_at_trip_local"] == {
        "date": "2026-10-04",
        "time": "17:30",
    }
    with Session(engine) as session:
        item = session.get(BookingImport, UUID(uploaded["id"]))
        assert item is not None and item.extraction_post_attempted
        assert item.extraction_key is not None
        assert item.upstream_extraction_id == fake.result.extraction_id


@pytest.mark.anyio
async def test_pre_dispatch_recovery_reposts_exact_payload_with_existing_key(private_import_client):
    _, engine, root, owner, trip_id, uploaded, text = _import_from_upload(private_import_client)
    key = uuid4()
    with Session(engine) as session, session.begin():
        item = session.get(BookingImport, UUID(uploaded["id"]))
        assert item is not None
        item.extraction_key = key
        item.extraction_text_sha256 = hashlib.sha256(text.encode()).hexdigest()
        item.extraction_post_attempted = True
        item.state = "extracting"
    fake = FakeExtractionClient()
    recovered = await _service(engine, root, fake).extract(owner, trip_id, UUID(uploaded["id"]))
    assert fake.gets == 1
    assert fake.posts == 1
    assert fake.last_payload is not None
    assert fake.last_payload["idempotency_key"] == str(key)
    assert fake.last_payload["document_text"] == text
    assert recovered["state"] == "review_ready"


@pytest.mark.anyio
async def test_expired_upstream_key_becomes_terminal_without_reposting(private_import_client):
    _, engine, root, owner, trip_id, uploaded, text = _import_from_upload(private_import_client)
    key = uuid4()
    with Session(engine) as session, session.begin():
        item = session.get(BookingImport, UUID(uploaded["id"]))
        assert item is not None
        item.extraction_key = key
        item.extraction_key_created_at = datetime.now(UTC) - timedelta(days=8)
        item.extraction_text_sha256 = hashlib.sha256(text.encode()).hexdigest()
        item.extraction_post_attempted = True
        item.state = "extracting"
    fake = FakeExtractionClient()
    result = await _service(engine, root, fake).extract(owner, trip_id, UUID(uploaded["id"]))
    assert result["state"] == "failed"
    assert result["failure_code"] == "upstream_result_expired"
    assert fake.gets == 0 and fake.posts == 0
    assert result["upstream_delete_pending"] is True
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BookingDeletionIntent)) == 1


@pytest.mark.anyio
async def test_zero_candidate_review_has_a_consistent_duplicate_shape(private_import_client):
    _, engine, root, owner, trip_id, uploaded, _ = _import_from_upload(private_import_client)
    result = await _service(engine, root, FakeExtractionClient(candidate_count=0)).extract(
        owner, trip_id, UUID(uploaded["id"])
    )
    assert result["state"] == "review_ready"
    assert result["candidates"] == []
    assert result["duplicate_suggestions"] == []


def test_travel_result_rejects_timezone_outside_candidate_evidence():
    source_text = "Synthetic Hotel booking HX-1 for 2026-10-05 09:30 +09:00"
    text_hash = hashlib.sha256(source_text.encode()).hexdigest()
    key = uuid4()
    valid = FakeExtractionClient()._result(key, text_hash, source_text)
    candidate = valid.candidates[0].model_copy(update={"starts_at_timezone": "America/New_York"})
    unsupported = valid.model_copy(update={"candidates": (candidate,)})
    with pytest.raises(DomainError, match="timezone lacked literal evidence"):
        BookingImportService._validate_result(unsupported, source_text, key, text_hash)


@pytest.mark.anyio
async def test_actual_pdf_uses_distinct_original_and_extracted_text_digests_through_confirmation(
    private_import_client,
    tmp_path: Path,
):
    client, engine, root, owner, trip_id, headers = private_import_client
    source_text = "Synthetic Hotel booking HX-1 for 2026-10-05 09:30 +09:00"
    pdf = _write_synthetic_pdf(tmp_path / "synthetic-booking.pdf", source_text)
    upload = client.post(
        f"/v1/trips/{trip_id}/imports",
        headers=headers | {"Content-Type": "application/pdf", "X-Source-Filename": "synthetic.pdf"},
        content=pdf,
    )
    assert upload.status_code == 200, upload.text
    uploaded = upload.json()
    original_digest = hashlib.sha256(pdf).hexdigest()
    assert uploaded["sha256"] == original_digest

    fake = FakeExtractionClient()
    service = _service(engine, root, fake)
    review = await service.extract(owner, UUID(trip_id), UUID(uploaded["id"]))
    assert review["state"] == "review_ready"
    assert fake.last_payload is not None
    extracted_text = str(fake.last_payload["document_text"])
    text_digest = hashlib.sha256(extracted_text.encode()).hexdigest()
    assert extracted_text == source_text
    assert fake.last_payload["source_sha256"] == text_digest
    assert text_digest != original_digest
    with Session(engine) as session:
        item = session.get(BookingImport, UUID(uploaded["id"]))
        assert item is not None
        assert item.source_sha256 == original_digest
        assert item.extraction_text_sha256 == text_digest
    candidate = review["candidates"][0]
    outcome = service.confirm(
        owner,
        UUID(trip_id),
        UUID(uploaded["id"]),
        ImportConfirmRequest(
            confirmation_key=uuid4(),
            expected_trip_revision=0,
            expected_import_revision=review["review_revision"],
            entries=(
                ImportConfirmationEntry(
                    candidate_id=candidate["candidate_id"],
                    decision="create_separate",
                    reservation_status="confirmed",
                ),
            ),
        ),
    )
    assert outcome["outcomes"][0]["outcome"] == "created"
    with Session(engine) as session:
        reservation = session.scalar(select(Reservation).where(Reservation.trip_id == trip_id))
        assert reservation is not None and reservation.status == "confirmed"


@pytest.mark.anyio
async def test_rejection_keeps_source_choice_but_retries_durable_upstream_deletion(
    private_import_client,
):
    _, engine, root, owner, trip_id, uploaded, _ = _import_from_upload(private_import_client)
    fake = FakeExtractionClient(reject_first_post=True)
    service = _service(engine, root, fake)
    failed = await service.extract(owner, trip_id, UUID(uploaded["id"]))
    assert failed["state"] == "failed"
    rejected = service.reject(owner, trip_id, UUID(uploaded["id"]))
    assert rejected["state"] == "rejected"
    with Session(engine) as session:
        item = session.get(BookingImport, UUID(uploaded["id"]))
        assert item is not None and item.upstream_delete_pending
        assert item.source_id is not None
        assert session.scalar(select(func.count()).select_from(BookingDeletionIntent)) == 1
        source = session.get(SourceAttachment, item.source_id)
        assert source is not None and source.state == "ready"

    fake.fail_deletes = 1
    with pytest.raises(DomainError) as pending:
        await asyncio.to_thread(
            service.delete_upstream_extraction, owner, trip_id, UUID(uploaded["id"])
        )
    assert pending.value.code == "upstream_delete_pending"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BookingDeletionIntent)) == 1
        item = session.get(BookingImport, UUID(uploaded["id"]))
        assert item is not None and item.upstream_delete_pending

    await asyncio.to_thread(
        service.delete_upstream_extraction, owner, trip_id, UUID(uploaded["id"])
    )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BookingDeletionIntent)) == 0
        item = session.get(BookingImport, UUID(uploaded["id"]))
        assert item is not None and not item.upstream_delete_pending and item.source_id is not None


@pytest.mark.anyio
async def test_late_extraction_result_after_rejection_never_restores_candidate_text(
    private_import_client,
):
    _, engine, root, owner, trip_id, uploaded, _ = _import_from_upload(private_import_client)
    fake = FakeExtractionClient()
    service = _service(engine, root, fake)
    fake.on_create = lambda: service.reject(owner, trip_id, UUID(uploaded["id"]))
    late = await service.extract(owner, trip_id, UUID(uploaded["id"]))
    assert late["state"] == "rejected"
    assert late["candidates"] == []
    assert late["upstream_delete_pending"] is True
    with Session(engine) as session:
        item = session.get(BookingImport, UUID(uploaded["id"]))
        assert item is not None and item.candidate_snapshot is None
        assert session.scalar(select(func.count()).select_from(BookingDeletionIntent)) == 1
    await asyncio.to_thread(
        service.delete_upstream_extraction, owner, trip_id, UUID(uploaded["id"])
    )
    with Session(engine) as session:
        item = session.get(BookingImport, UUID(uploaded["id"]))
        assert item is not None and item.state == "rejected" and item.candidate_snapshot is None
        assert not item.upstream_delete_pending


@pytest.mark.anyio
async def test_trip_deletion_preserves_orphaned_upstream_cleanup_intent(private_import_client):
    _, engine, root, owner, trip_id, uploaded, _ = _import_from_upload(private_import_client)
    fake = FakeExtractionClient()
    service = _service(engine, root, fake)
    await service.extract(owner, trip_id, UUID(uploaded["id"]))
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session(engine) as session:
        TripService(session, owner).delete(trip_id, expected_revision=0)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BookingImport)) == 0
        intent = session.scalar(select(BookingDeletionIntent))
        assert intent is not None and intent.owner_id == owner
    cleaned = await PrivateDeletionService(factory, fake).retry_pending(owner)
    assert cleaned["deleted"] == 1 and cleaned["pending"] == 0
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BookingDeletionIntent)) == 0


@pytest.mark.anyio
async def test_saved_corrections_preserve_explicit_null_and_recompute_trip_local_projection(
    private_import_client,
):
    _, engine, root, owner, trip_id, uploaded, _ = _import_from_upload(private_import_client)
    with Session(engine) as session, session.begin():
        trip = session.get(Trip, trip_id)
        assert trip is not None
        trip.timezone = "America/Los_Angeles"
    service = _service(engine, root, FakeExtractionClient())
    review = await service.extract(owner, trip_id, UUID(uploaded["id"]))
    candidate = review["candidates"][0]
    assert candidate["starts_at_trip_local"] == {"date": "2026-10-04", "time": "17:30"}
    corrected = service.update_edits(
        owner,
        trip_id,
        UUID(uploaded["id"]),
        ImportEditsRequest(
            expected_import_revision=review["review_revision"],
            edits=(
                ImportCandidateEdit(
                    candidate_id=candidate["candidate_id"],
                    starts_at_date=date(2026, 10, 5),
                    starts_at_time="12:30",
                    starts_at_timezone="-04:00",
                    reservation_status="confirmed",
                ),
            ),
        ),
    )
    corrected_candidate = corrected["candidates"][0]
    assert corrected_candidate["starts_at_trip_local"] == {"date": "2026-10-04", "time": "17:30"}
    assert corrected_candidate["current"]["starts_at_trip_local"] == {
        "date": "2026-10-05",
        "time": "09:30",
    }
    cleared = service.update_edits(
        owner,
        trip_id,
        UUID(uploaded["id"]),
        ImportEditsRequest(
            expected_import_revision=corrected["review_revision"],
            edits=(
                ImportCandidateEdit(
                    candidate_id=candidate["candidate_id"],
                    provider_name=None,
                    starts_at_date=None,
                    starts_at_time=None,
                    starts_at_timezone=None,
                ),
            ),
        ),
    )
    assert cleared["candidates"][0]["current"]["starts_at_trip_local"] is None
    reopened = service.detail(owner, trip_id, UUID(uploaded["id"]))
    saved_candidate = reopened["candidates"][0]
    assert saved_candidate["provider_name"] == "Synthetic Hotel"
    assert saved_candidate["current"]["provider_name"] is None
    assert saved_candidate["starts_at_trip_local"] == {"date": "2026-10-04", "time": "17:30"}
    assert saved_candidate["current"]["starts_at_trip_local"] is None
    assert saved_candidate["current"]["starts_at_date"] is None
    assert saved_candidate["current"]["reservation_status"] == "confirmed"


@pytest.mark.anyio
async def test_batch_rejects_one_itinerary_item_mapped_to_two_new_reservations(
    private_import_client,
):
    _, engine, root, owner, trip_id, uploaded, _ = _import_from_upload(
        private_import_client,
        "Synthetic Hotel booking HX-1 for 2026-10-05 09:30 +09:00\n"
        "Synthetic Hotel booking HX-2 for 2026-10-05 09:30 +09:00",
    )
    with Session(engine) as session, session.begin():
        trip = session.get(Trip, trip_id)
        assert trip is not None and trip.days
        item = ItineraryItem(
            trip_day_id=trip.days[0].id,
            item_type="activity",
            title="Synthetic itinerary item",
            sort_order=0,
            status="planned",
        )
        session.add(item)
        session.flush()
        item_id = item.id
    service = _service(engine, root, FakeExtractionClient(candidate_count=2))
    review = await service.extract(owner, trip_id, UUID(uploaded["id"]))
    entries = tuple(
        ImportConfirmationEntry(
            candidate_id=candidate["candidate_id"],
            decision="create_separate",
            reservation_status="tentative",
            itinerary_item_id=item_id,
        )
        for candidate in review["candidates"]
    )
    payload = ImportConfirmRequest(
        confirmation_key=uuid4(),
        expected_trip_revision=0,
        expected_import_revision=review["review_revision"],
        entries=entries,
    )
    with pytest.raises(DomainError) as conflict:
        service.confirm(owner, trip_id, UUID(uploaded["id"]), payload)
    assert conflict.value.code == "item_link_conflict"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Reservation)) == 0
        item = session.get(ItineraryItem, item_id)
        assert item is not None and item.reservation_id is None
        trip = session.get(Trip, trip_id)
        assert trip is not None and trip.revision == 0


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
        entries=(
            ImportConfirmationEntry(
                candidate_id=candidate_id,
                decision="create_separate",
                reservation_status="tentative",
            ),
        ),
    )

    first = service.confirm(owner, trip_id, UUID(uploaded["id"]), payload)
    replay = service.confirm(owner, trip_id, UUID(uploaded["id"]), payload)
    assert replay == first
    assert first["outcomes"][0]["outcome"] == "created"
    assert first["upstream_revision"] == "ebd00a8e2fb2d8b59a5fb5fa3aa44268e5e79b63"
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

    changed_status = payload.model_copy(
        update={
            "entries": (
                ImportConfirmationEntry(
                    candidate_id=candidate_id,
                    decision="create_separate",
                    reservation_status="confirmed",
                ),
            )
        }
    )
    with pytest.raises(DomainError) as changed_status_replay:
        service.confirm(owner, trip_id, UUID(uploaded["id"]), changed_status)
    assert changed_status_replay.value.code == "confirmation_already_final"

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
                reservation_status="tentative",
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
    _, engine, root, owner, trip_id, uploaded, _ = _import_from_upload(
        private_import_client,
        "Synthetic Hotel booking HX-1 for 2026-10-05 09:30 +09:00\n"
        "Synthetic Hotel booking HX-2 for 2026-10-05 09:30 +09:00",
    )
    fake = FakeExtractionClient(candidate_count=2)
    service = _service(engine, root, fake)
    detail = await service.extract(owner, trip_id, UUID(uploaded["id"]))
    first_id, second_id = [candidate["candidate_id"] for candidate in detail["candidates"]]
    payload = ImportConfirmRequest(
        confirmation_key=uuid4(),
        expected_trip_revision=0,
        expected_import_revision=detail["review_revision"],
        entries=(
            ImportConfirmationEntry(
                candidate_id=first_id,
                decision="create_separate",
                reservation_status="tentative",
            ),
            ImportConfirmationEntry(
                candidate_id=second_id,
                decision="create_separate",
                reservation_status="tentative",
                place_id=uuid4(),
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
                    reservation_status="tentative",
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
    _, engine, root, owner, trip_id, uploaded, _ = _import_from_upload(
        private_import_client,
        "Synthetic Hotel booking HX-1 for 2026-10-05 09:30 +09:00\n"
        "Synthetic Hotel booking HX-2 for 2026-10-05 09:30 +09:00",
    )
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
