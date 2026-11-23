"""Private plan source and journal checks against disposable migrated PostgreSQL."""

import hashlib
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_private_plan_delegation import CONVERSATIONS, OWNER, SUBJECT, SignedPlanFixture

from personal_travel.api.routes.personal_ai_context import (
    _bind_sidecar_conversation,
    _require_sidecar_anchor,
)
from personal_travel.api.routes.private_plan import mount_private_plan_source
from personal_travel.api.schemas.private_plan import PRIVATE_PLAN_SOURCE_PATH
from personal_travel.auth.contracts import VerifiedPrincipal
from personal_travel.clients.private_plan import (
    PlanDestination,
    PlanReceipt,
    PlanReceiptRegistration,
    create_body,
    preview_body,
)
from personal_travel.clients.task_wire import canonical_bytes
from personal_travel.config import Settings
from personal_travel.models.auth import AuthIdentity
from personal_travel.models.private_plan import PrivatePlanBinding, PrivatePlanIntent
from personal_travel.models.sidecar_anchor import PersonalAISidecarAnchor
from personal_travel.models.trip import Trip
from personal_travel.services.errors import DomainError
from personal_travel.services.private_plan import PlanRecovery, PrivatePlanJournal

TRIPS = (UUID(int=201), UUID(int=202))
CREATES = (UUID(int=301), UUID(int=302))
ORIGINAL_KEY = UUID(int=401)
OTHER_OWNER = "another-fabricated-owner"
TITLES = ("Fabricated plan trip A", "Fabricated plan trip B")
REVISIONS = (0, 9)


@dataclass
class Fixture:
    engine: Engine
    signed: SignedPlanFixture
    settings: Settings
    receipt_key: Ed25519PrivateKey

    @property
    def destination(self) -> PlanDestination:
        return PlanDestination(
            application="travel",
            provider="ChatGPT",
            model="fake-model",
            runtime="fake-runtime",
            recipient="fake-account",
        )

    def receipt(
        self,
        conversation_id,
        key,
        title_or_content,
        *,
        revision=None,
        expiry=None,
        owner=OWNER,
        subject=SUBJECT,
        destination=None,
        **scope_override,
    ):
        if revision is None:
            request = create_body(key, title_or_content)
            payload = {"conversation_id": str(conversation_id)}
        else:
            request = preview_body(key, title_or_content)
            payload = {
                "confirmation": "1" * 64,
                "destination": (destination or self.destination).model_dump(),
                "purpose": "foreground_application_plan",
                "selected_context": [
                    {"category": "conversation_history", "turns": 0},
                    {"category": "domain_current", "fields": ["trip_revision", "trip_title"]},
                    {"category": "ai_memory", "fields": ["content"]},
                ],
                "input_tokens_estimated": 10,
                "expires_at": (expiry or datetime.now(UTC) + timedelta(seconds=20)).isoformat(),
            }
        body = canonical_bytes(payload)
        statement = {
            "schema_version": "travel-application-plan-receipt-v1",
            "operation": "create" if revision is None else "preview",
            "target_id": self.signed.trust.target_id,
            "client_namespace": self.signed.trust.client_namespace,
            "application_id": "travel",
            "workspace_id": None,
            "owner_id": owner,
            "issuer": self.signed.trust.human_issuer,
            "subject": subject,
            "conversation_id": str(conversation_id),
            "request_digest": hashlib.sha256(request).hexdigest(),
            "response_digest": hashlib.sha256(body).hexdigest(),
            "source_revision": str(revision) if revision is not None else None,
        } | scope_override
        return PlanReceipt(body, self.receipt_key.sign(canonical_bytes(statement)).hex())

    def session(self) -> Session:
        return Session(self.engine, autoflush=False, expire_on_commit=False)

    def journal(
        self, session: Session, *, owner: str = OWNER, subject: str = SUBJECT
    ) -> PrivatePlanJournal:
        now = int(datetime.now(UTC).timestamp())
        return PrivatePlanJournal(
            session,
            settings=self.settings,
            trust=self.signed.trust,
            receipts=PlanReceiptRegistration(self.receipt_key.public_key(), self.destination),
            principal=VerifiedPrincipal(
                issuer=self.signed.trust.human_issuer,
                subject=subject,
                owner_id=owner,
                email="fabricated@example.invalid",
                issued_at=now,
                expires_at=now + 30,
            ),
        )

    def app(self, *, settings: Settings | None = None) -> FastAPI:
        app = FastAPI()
        mount_private_plan_source(
            app,
            settings=settings or self.settings,
            trust=self.signed.trust,
            sessions=self.session,
            verify_service=self.signed.verify_service,
        )
        return app


@pytest.fixture
def plan_fixture(database_engine: Engine, clean_database: None) -> Iterator[Fixture]:
    fixture = Fixture(
        database_engine,
        SignedPlanFixture(),
        Settings(_env_file=None, personal_ai_plan_enabled=True),
        Ed25519PrivateKey.generate(),
    )
    with fixture.session() as session, session.begin():
        session.add(
            AuthIdentity(
                owner_id=OWNER,
                issuer=fixture.signed.trust.human_issuer,
                subject=SUBJECT,
                email="fabricated@example.invalid",
            )
        )
        for trip_id, title, revision in zip(TRIPS, TITLES, REVISIONS, strict=True):
            session.add(
                Trip(
                    id=trip_id,
                    owner_id=OWNER,
                    title=title,
                    start_date=date(2026, 11, 22),
                    end_date=date(2026, 11, 23),
                    timezone="UTC",
                    revision=revision,
                )
            )
    yield fixture


def bind_fabricated_conversations(fixture: Fixture) -> None:
    with fixture.session() as session:
        journal = fixture.journal(session)
        for trip_id, conversation, create_key in zip(TRIPS, CONVERSATIONS, CREATES, strict=True):
            reserved = journal.reserve_create(
                trip_id=trip_id, create_key=create_key, title="Travel assistant"
            )
            assert reserved.newly_reserved and reserved.conversation_id is None
            journal.bind_created_conversation(
                trip_id=trip_id,
                create_key=create_key,
                receipt=fixture.receipt(conversation, create_key, "Travel assistant"),
            )


def test_plan_source_default_off(plan_fixture: Fixture) -> None:
    settings = Settings(_env_file=None)
    assert not settings.personal_ai_context_enabled and not settings.personal_ai_plan_enabled
    with TestClient(plan_fixture.app(settings=settings)) as client:
        assert client.post(PRIVATE_PLAN_SOURCE_PATH, json={}).status_code == 404


def test_interleaved_same_owner_projections_keep_original_v2_provenance(
    plan_fixture: Fixture,
) -> None:
    bind_fabricated_conversations(plan_fixture)
    signed = plan_fixture.signed
    with TestClient(plan_fixture.app()) as client, ThreadPoolExecutor(max_workers=2) as executor:
        barrier = Barrier(2)

        def read(index: int) -> None:
            body = signed.body(CONVERSATIONS[index])
            for _ in range(2):
                barrier.wait(timeout=5)
                response = client.post(
                    PRIVATE_PLAN_SOURCE_PATH, json=body, headers=signed.headers(body)
                )
                assert response.status_code == 200
                assert response.headers["cache-control"] == "no-store"
                assert len(response.content) < 4096
                payload = response.json()
                assert payload["schema_version"] == "travel-context-read-v2"
                assert payload["application_id"] == "travel" and payload["workspace_id"] is None
                (record,) = payload["items"]
                assert record["entity_id"] == str(TRIPS[index])
                assert record["source_version"] == str(REVISIONS[index])
                assert record["observed_at"] and record["expires_at"] is None
                fields = {"trip_title": TITLES[index], "trip_revision": REVISIONS[index]}
                assert record["payload"] == fields
                opaque = hashlib.sha256(f"trip\0{TRIPS[index]}".encode()).hexdigest()
                assert record["source_id"] == f"travel:{opaque}"
                assert record["sensitivity"] == "sensitive"
                assert {
                    (item["field"], item["sensitivity"]) for item in record["field_sensitivity"]
                } == {
                    ("trip_title", "sensitive"),
                    ("trip_revision", "sensitive"),
                }
                assert record["permission_dependencies"][0]["permission_id"] == (
                    f"travel_context_read:{TRIPS[index]}"
                )
                assert record["permission_dependencies"][0]["version"] == str(REVISIONS[index])
                expected_fingerprint = hashlib.sha256(
                    json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                assert record["source_refs"][0]["fingerprint"] == expected_fingerprint

        a, b = (executor.submit(read, index) for index in range(2))
        a.result(timeout=15)
        b.result(timeout=15)


def test_source_denies_scope_conflicts_revocation_and_changed_revision(
    plan_fixture: Fixture,
) -> None:
    bind_fabricated_conversations(plan_fixture)
    signed = plan_fixture.signed
    body = signed.body()
    verify = body | {"operation": "verify", "expected_source_version": "0"}
    with TestClient(plan_fixture.app()) as client:
        response = client.post(
            PRIVATE_PLAN_SOURCE_PATH, json=verify, headers=signed.headers(verify)
        )
        assert response.status_code == 200
        assert response.json()["items"][0]["payload"] == {"trip_revision": 0}

        for forged in (
            body | {"lane": "automatic"},
            body | {"client_namespace": "b" * 64},
            body | {"target_id": "unregistered"},
            body | {"entity_refs": [{"entity_type": "trip", "entity_id": str(TRIPS[1])}]},
        ):
            response = client.post(
                PRIVATE_PLAN_SOURCE_PATH, json=forged, headers=signed.headers(forged)
            )
            assert response.status_code == 401
            assert not any(title in response.text for title in TITLES)
        expired = client.post(
            PRIVATE_PLAN_SOURCE_PATH, json=body, headers=signed.headers(body, age=90)
        )
        assert expired.status_code == 401

        with plan_fixture.session() as session, session.begin():
            trip = session.get(Trip, TRIPS[0])
            assert trip is not None
            trip.revision += 1
        stale = client.post(PRIVATE_PLAN_SOURCE_PATH, json=verify, headers=signed.headers(verify))
        assert stale.status_code == 409 and TITLES[0] not in stale.text

        with plan_fixture.session() as session, session.begin():
            trip = session.get(Trip, TRIPS[0])
            assert trip is not None
            trip.owner_id = OTHER_OWNER
        denied = client.post(PRIVATE_PLAN_SOURCE_PATH, json=body, headers=signed.headers(body))
        assert denied.status_code == 404 and TITLES[0] not in denied.text

        with plan_fixture.session() as session, session.begin():
            identity = session.get(AuthIdentity, OWNER)
            assert identity is not None
            identity.status = "disabled"
        body = signed.body(CONVERSATIONS[1])
        denied = client.post(PRIVATE_PLAN_SOURCE_PATH, json=body, headers=signed.headers(body))
        assert denied.status_code == 404 and TITLES[1] not in denied.text


def test_binding_is_write_once_and_disjoint_from_automatic_sidecar(
    plan_fixture: Fixture,
) -> None:
    bind_fabricated_conversations(plan_fixture)
    legacy_id = UUID(int=555)
    with plan_fixture.session() as session:
        _bind_sidecar_conversation(
            session, owner_id=OWNER, trip_id=TRIPS[0], conversation_id=legacy_id
        )
        _require_sidecar_anchor(
            session, owner_id=OWNER, trip_id=TRIPS[0], conversation_id=legacy_id
        )
        with pytest.raises(DomainError) as wrong_lane:
            _require_sidecar_anchor(
                session, owner_id=OWNER, trip_id=TRIPS[0], conversation_id=CONVERSATIONS[0]
            )
        assert wrong_lane.value.status_code == 404
        with pytest.raises(DomainError) as legacy_create:
            _bind_sidecar_conversation(
                session, owner_id=OWNER, trip_id=TRIPS[0], conversation_id=CONVERSATIONS[0]
            )
        assert legacy_create.value.status_code == 404
    with plan_fixture.session() as session:
        journal = plan_fixture.journal(session)
        match = journal.reserve_create(
            trip_id=TRIPS[0], create_key=CREATES[0], title="Travel assistant"
        )
        assert match.conversation_id == CONVERSATIONS[0] and not match.newly_reserved
        with pytest.raises(DomainError) as conflicting_result:
            journal.bind_created_conversation(
                trip_id=TRIPS[0],
                create_key=CREATES[0],
                receipt=plan_fixture.receipt(CONVERSATIONS[1], CREATES[0], "Travel assistant"),
            )
        assert conflicting_result.value.status_code == 409

    with plan_fixture.session() as session:
        with pytest.raises(IntegrityError), session.begin():
            anchor = session.get(PersonalAISidecarAnchor, CONVERSATIONS[0])
            assert anchor is not None
            anchor.lane = "automatic"
        with pytest.raises(IntegrityError), session.begin():
            binding = session.scalar(
                select(PrivatePlanBinding).where(PrivatePlanBinding.create_key == CREATES[0])
            )
            assert binding is not None
            binding.client_namespace = "b" * 64

    with plan_fixture.session() as session, session.begin():
        binding = session.scalar(
            select(PrivatePlanBinding).where(PrivatePlanBinding.create_key == CREATES[1])
        )
        assert binding is not None
        session.delete(binding)
    with plan_fixture.session() as session:
        journal = plan_fixture.journal(session)
        journal.reserve_create(trip_id=TRIPS[1], create_key=CREATES[1], title="Travel assistant")
        with pytest.raises(DomainError) as automatic_collision:
            journal.bind_created_conversation(
                trip_id=TRIPS[1],
                create_key=CREATES[1],
                receipt=plan_fixture.receipt(legacy_id, CREATES[1], "Travel assistant"),
            )
        assert automatic_collision.value.status_code == 409

    signed = plan_fixture.signed
    body = signed.body(legacy_id)
    with TestClient(plan_fixture.app()) as client:
        response = client.post(PRIVATE_PLAN_SOURCE_PATH, json=body, headers=signed.headers(body))
        assert response.status_code == 404 and TITLES[0] not in response.text


def test_original_key_concurrent_reservation_and_ambiguous_lookup(
    plan_fixture: Fixture,
) -> None:
    bind_fabricated_conversations(plan_fixture)
    args = {
        "trip_id": TRIPS[0],
        "conversation_id": CONVERSATIONS[0],
        "original_key": ORIGINAL_KEY,
    }
    content = "A fabricated original preview request"
    barrier = Barrier(2)

    def reserve() -> str:
        with plan_fixture.session() as session:
            barrier.wait(timeout=5)
            return plan_fixture.journal(session).reserve_preview(**args, content=content)

    with ThreadPoolExecutor(max_workers=2) as executor:
        one, two = executor.submit(reserve), executor.submit(reserve)
        assert {one.result(timeout=10), two.result(timeout=10)} == {"reserved", "join"}

    with plan_fixture.session() as session:
        journal = plan_fixture.journal(session)
        journal.record_preview_unknown(**args)
        with pytest.raises(DomainError):
            journal.reserve_preview(**args, content=content + " changed")
        changed_key = args | {"original_key": UUID(int=777)}
        with pytest.raises(DomainError):
            journal.reserve_preview(**changed_key, content=content)
        with pytest.raises(DomainError):
            journal.reserve_preview(**(args | {"trip_id": TRIPS[1]}), content=content)

    with plan_fixture.session() as session:
        restored_journal = plan_fixture.journal(session)
        assert restored_journal.reserve_preview(**args, content=content) == "reconcile_only"
        assert restored_journal.original_status(**args) == PlanRecovery(
            CONVERSATIONS[0], ORIGINAL_KEY
        )
        assert restored_journal.original_status(**changed_key) is None
        intent = session.scalar(select(PrivatePlanIntent))
        assert intent is not None and len(intent.request_digest) == 64
        assert content not in intent.request_digest

    with plan_fixture.session() as session:
        with pytest.raises(DomainError) as wrong_owner:
            plan_fixture.journal(session, owner=OTHER_OWNER).original_status(**args)
        assert wrong_owner.value.status_code == 404
        receipt_args = {
            "trip_id": TRIPS[1],
            "conversation_id": CONVERSATIONS[1],
            "original_key": UUID(int=402),
        }
        journal = plan_fixture.journal(session)
        assert journal.reserve_preview(**receipt_args, content=content) == "reserved"
        journal.record_preview(
            **receipt_args,
            receipt=plan_fixture.receipt(
                CONVERSATIONS[1], UUID(int=402), content, revision=REVISIONS[1]
            ),
        )
        assert journal.original_status(**receipt_args) == PlanRecovery(
            CONVERSATIONS[1], UUID(int=402)
        )


def test_same_keys_and_email_under_a_distinct_owner_are_isolated(plan_fixture: Fixture) -> None:
    bind_fabricated_conversations(plan_fixture)
    source = {
        "trip_id": TRIPS[0],
        "conversation_id": CONVERSATIONS[0],
        "original_key": ORIGINAL_KEY,
    }
    other_subject = "another-fabricated-subject"
    other_trip = UUID(int=705)
    other_conversation = UUID(int=706)
    with plan_fixture.session() as session:
        with session.begin():
            session.add(
                AuthIdentity(
                    owner_id=OTHER_OWNER,
                    issuer=plan_fixture.signed.trust.human_issuer,
                    subject=other_subject,
                    email="fabricated@example.invalid",
                )
            )
            session.add(
                Trip(
                    id=other_trip,
                    owner_id=OTHER_OWNER,
                    title="Another fabricated trip",
                    start_date=date(2026, 11, 22),
                    end_date=date(2026, 11, 23),
                    timezone="UTC",
                )
            )
        original = plan_fixture.journal(session)
        assert original.reserve_preview(**source, content="First fabricated intent") == "reserved"
        other = plan_fixture.journal(session, owner=OTHER_OWNER, subject=other_subject)
        reserved = other.reserve_create(
            trip_id=other_trip, create_key=CREATES[0], title="Other Travel assistant"
        )
        assert reserved.newly_reserved
        other.bind_created_conversation(
            trip_id=other_trip,
            create_key=CREATES[0],
            receipt=plan_fixture.receipt(
                other_conversation,
                CREATES[0],
                "Other Travel assistant",
                owner=OTHER_OWNER,
                subject=other_subject,
            ),
        )
        assert (
            other.reserve_preview(
                trip_id=other_trip,
                conversation_id=other_conversation,
                original_key=ORIGINAL_KEY,
                content="Second fabricated intent",
            )
            == "reserved"
        )
        with pytest.raises(DomainError) as wrong_owner:
            other.original_status(**source)
        assert wrong_owner.value.status_code == 404

    body = plan_fixture.signed.body()
    with TestClient(plan_fixture.app()) as client:
        denied = client.post(
            PRIVATE_PLAN_SOURCE_PATH,
            json=body,
            headers=plan_fixture.signed.headers(body, subject=other_subject),
        )
        assert denied.status_code == 404 and TITLES[0] not in denied.text


def test_bindings_without_receipt_provenance_stay_quarantined(plan_fixture: Fixture) -> None:
    conversation_id = CONVERSATIONS[0]
    with plan_fixture.session() as session, session.begin():
        # Represents a completed 0021 binding restored from before receipt verification.
        session.add(
            PersonalAISidecarAnchor(
                conversation_id=conversation_id,
                owner_id=OWNER,
                trip_id=TRIPS[0],
                lane="user_owned_plan",
            )
        )
        session.flush()
        session.add(
            PrivatePlanBinding(
                create_key=CREATES[0],
                owner_id=OWNER,
                trip_id=TRIPS[0],
                issuer=plan_fixture.signed.trust.human_issuer,
                subject=SUBJECT,
                target_id=plan_fixture.signed.trust.target_id,
                client_namespace=plan_fixture.signed.trust.client_namespace,
                title="Travel assistant",
                conversation_id=conversation_id,
            )
        )

    args = {"trip_id": TRIPS[0], "create_key": CREATES[0]}
    with plan_fixture.session() as session:
        journal = plan_fixture.journal(session)
        with pytest.raises(DomainError) as replay:
            journal.reserve_create(**args, title="Travel assistant")
        assert replay.value.status_code == 404
        with pytest.raises(DomainError) as bind:
            journal.bind_created_conversation(
                **args,
                receipt=plan_fixture.receipt(conversation_id, CREATES[0], "Travel assistant"),
            )
        assert bind.value.status_code == 404
        with pytest.raises(DomainError) as recovery:
            journal.original_status(
                trip_id=TRIPS[0], conversation_id=conversation_id, original_key=ORIGINAL_KEY
            )
        assert recovery.value.status_code == 404

    signed = plan_fixture.signed
    body = signed.body(conversation_id)
    with TestClient(plan_fixture.app()) as client:
        result = client.post(PRIVATE_PLAN_SOURCE_PATH, json=body, headers=signed.headers(body))
        assert result.status_code == 404
        assert TITLES[0] not in result.text
