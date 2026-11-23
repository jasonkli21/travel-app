"""Travel journal and fake PAS HTTP observations on migrated PostgreSQL."""

import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from os import environ
from shutil import which
from subprocess import run
from threading import Barrier
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_private_plan_postgres import (
    CONVERSATIONS,
    CREATES,
    ORIGINAL_KEY,
    OWNER,
    REVISIONS,
    SUBJECT,
    TRIPS,
    Fixture,
)
from test_private_plan_postgres import plan_fixture as plan_fixture

from personal_travel.clients.private_dispatch import (
    DISPATCH_RECEIPT,
    MEDIA_TYPE,
    DispatchBinding,
    DispatchDestination,
    PrivateDispatchHTTP,
    SigningRole,
    canonical,
    digest,
)
from personal_travel.clients.private_plan import RECEIPT_HEADER, create_body, preview_body
from personal_travel.models.auth import AuthSession
from personal_travel.models.private_plan import PrivatePlanConfirmation
from personal_travel.models.trip import Trip
from personal_travel.services.errors import DomainError
from personal_travel.services.owner_erasure import freeze_owner
from personal_travel.services.private_dispatch import (
    DispatchRegistration,
    LiveDispatchGrants,
    PrivateDispatchCaller,
    PrivateDispatchJournal,
    RegisteredTravelSource,
    source_commitment,
)

CONTENT = "Synthetic selection for trip A"
TITLE = "Synthetic conversation"
SESSION = "a" * 64


def _sign(key, claims):
    return base64.urlsafe_b64encode(key.sign(canonical(claims))).decode().rstrip("=")


class FakePAS:
    def __init__(self, fixture: Fixture):
        self.fixture = fixture
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.preview_key = Ed25519PrivateKey.generate()
        self.terminal_key = Ed25519PrivateKey.generate()
        self.create = fixture.receipt(CONVERSATIONS[0], CREATES[0], TITLE)
        self.preview = fixture.receipt(
            CONVERSATIONS[0],
            ORIGINAL_KEY,
            CONTENT,
            revision=REVISIONS[0],
            expiry=self.now + timedelta(seconds=40),
            destination=fixture.destination.model_copy(update={"provider": "fake"}),
        )
        self.calls = Counter()
        self.fail_confirmation = None
        self.status_kind = "unknown"
        self.status_hook = lambda: None
        self.detail_hook = lambda: None
        self.result = "validated synthetic result"
        self.continuity = True
        self.preview_active = True
        self.terminal_active = True
        self.grants = LiveDispatchGrants(
            permission_epoch=3,
            policy="synthetic",
            branch="1" * 64,
            memory="2" * 64,
            source_grant="trip-v2",
            model_grant="fake-model-grant",
            runtime_grant="fake-runtime-grant",
            destination=DispatchDestination(
                account="fake-account",
                connection="fixture-connection",
                endpoint="loopback-fixture",
                model="fake-model",
                provider="fake",
                revision=2,
                runtime="fake-runtime",
            ),
            expires=int(self.now.timestamp()) + 100,
        )
        self.registration = DispatchRegistration(
            travel_owner=OWNER,
            pas_owner="pas-fixture-owner",
            pas_client="pas-fixture-client",
            tenant="local-fixture",
            installation="travel-fixture",
            destination=self.grants.destination,
            source_key=b"f" * 32,
            session_token_digest=SESSION,
            role_active=self._role_active,
            preview_role=SigningRole("fixture-pas", "p1", "travel", self.preview_key.public_key()),
            terminal_role=SigningRole(
                "fixture-runtime", "r1", "fixture-pas", self.terminal_key.public_key()
            ),
            grants=self._grants,
            continuity=self._continuity,
        )
        with fixture.session() as session, session.begin():
            session.add(
                AuthSession(
                    token_hash=SESSION,
                    owner_id=OWNER,
                    csrf_token_hash="b" * 64,
                    issued_at=self.now,
                    expires_at=self.now + timedelta(minutes=10),
                )
            )
            trip = session.get(Trip, TRIPS[0])
            source = source_commitment(self.registration.source_key, trip, self.grants)
        self.binding = DispatchBinding(
            actor_issuer=fixture.signed.trust.human_issuer,
            app="travel",
            attempt=str(UUID(int=915)),
            branch=self.grants.branch,
            client=self.registration.pas_client,
            conversation=str(CONVERSATIONS[0]),
            create_key=str(CREATES[0]),
            create_receipt=digest(self.create.signature.encode()),
            destination=self.grants.destination,
            epoch=self.grants.permission_epoch,
            installation=self.registration.installation,
            memory=self.grants.memory,
            original_key=str(ORIGINAL_KEY),
            owner=self.registration.pas_owner,
            policy=self.grants.policy,
            preview_receipt=digest(self.preview.signature.encode()),
            root=str(UUID(int=914)),
            source=source,
            subject=SUBJECT,
            target=fixture.signed.trust.target_id,
            tenant=self.registration.tenant,
            workspace=None,
        )
        self.body = None

    def _role_active(self, role):
        return self.preview_active if role.kid == "p1" else self.terminal_active

    def _grants(self, _session, _trip, _binding, _phase):
        return self.grants

    def _continuity(self, _session, _binding, _body_digest):
        return self.continuity

    def _headers(self, **extra):
        return {"content-type": MEDIA_TYPE, "cache-control": "no-store"} | extra

    def _handle(self, request):
        self.calls[request.method, request.url.path] += 1
        assert request.url.host == "127.0.0.1"
        assert request.headers["authorization"] == "Bearer synthetic-pas"
        if request.url.path.endswith("context-preview"):
            assert request.headers["accept"] == MEDIA_TYPE
            assert request.content == preview_body(ORIGINAL_KEY, CONTENT)
            claims = {
                "audience": "travel",
                "binding_sha256": self.binding.commitment(),
                "expires": int(self.now.timestamp()) + 20,
                "issuer": "fixture-pas",
                "kid": "p1",
                "payload_sha256": "4" * 64,
                "purpose": "preview",
                "v": "pas-dispatch/1",
            }
            envelope = {"claims": claims, "signature": _sign(self.preview_key, claims)}
            return httpx.Response(
                200,
                headers=self._headers(
                    **{
                        RECEIPT_HEADER: self.preview.signature,
                        DISPATCH_RECEIPT: canonical(envelope).decode("ascii"),
                    }
                ),
                content=self.preview.body,
            )
        if request.url.path.endswith("continue"):
            self.body = request.content
            assert request.headers["content-type"] == MEDIA_TYPE
            # A distinct connection observes row and exact body before transport runs.
            with self.fixture.session() as db:
                row = db.scalar(select(PrivatePlanConfirmation))
                assert row is not None
                assert row.body == request.content and row.body_digest == digest(request.content)
                assert row.submitted_at is not None
            if self.fail_confirmation is not None:
                return self.fail_confirmation(request)
            return httpx.Response(202)
        if request.method == "POST":
            assert request.content == create_body(CREATES[0], TITLE)
            return httpx.Response(
                201,
                headers={
                    "content-type": "application/json",
                    "cache-control": "no-store",
                    RECEIPT_HEADER: self.create.signature,
                },
                content=self.create.body,
            )
        assert request.headers["accept"] == MEDIA_TYPE
        if "/continuations/" in request.url.path:
            self.status_hook()
            if self.status_kind == "partial":
                return httpx.Response(200, headers=self._headers(), content=b'{"state":"comp')
            with self.fixture.session() as db:
                submitted = db.scalar(select(PrivatePlanConfirmation))
                assert submitted is not None
                body_digest = submitted.body_digest
            status = {
                "state": "unknown",
                "original_key": self.binding.original_key,
                "attempt": self.binding.attempt,
                "root": self.binding.root,
                "body_sha256": body_digest,
                "message_id": None,
                "terminal": None,
                "signature": None,
            }
            if self.status_kind in ("completed", "wrong_root"):
                message_id = str(UUID(int=916))
                claims = {
                    "attempt": self.binding.attempt,
                    "audience": "fixture-pas",
                    "binding_sha256": self.binding.commitment(),
                    "body_sha256": body_digest,
                    "expires": int(self.now.timestamp()) + 30,
                    "issuer": "fixture-runtime",
                    "kid": "r1",
                    "message_id": message_id,
                    "purpose": "terminal",
                    "result_sha256": digest(b"validated synthetic result"),
                    "v": "pas-dispatch/1",
                    "root": self.binding.root
                    if self.status_kind == "completed"
                    else str(UUID(int=100)),
                }
                status |= {
                    "state": "completed",
                    "message_id": message_id,
                    "terminal": claims,
                    "signature": _sign(self.terminal_key, claims),
                }
            return httpx.Response(200, headers=self._headers(), content=canonical(status))
        assert request.headers["x-pas-original-key"] == self.binding.original_key
        self.detail_hook()
        return httpx.Response(
            200,
            headers=self._headers(),
            content=canonical(
                {
                    "message_id": str(UUID(int=916)),
                    "attempt": self.binding.attempt,
                    "root": self.binding.root,
                    "original_key": self.binding.original_key,
                    "result": self.result,
                }
            ),
        )

    def journal(self, session):
        return PrivateDispatchJournal(self.fixture.journal(session), self.registration)

    def caller(self, session):
        return PrivateDispatchCaller(
            self.journal(session),
            PrivateDispatchHTTP(
                "https://127.0.0.1:8444",
                bearer="synthetic-pas",
                transport=httpx.MockTransport(self._handle),
            ),
        )

    def create_and_preview(self):
        with self.fixture.session() as session:
            caller = self.caller(session)
            created = caller.create(trip_id=TRIPS[0], create_key=CREATES[0], title=TITLE)
            return caller.preview(
                trip_id=TRIPS[0],
                created=created,
                original_key=ORIGINAL_KEY,
                content=CONTENT,
                expected=self.binding.model_copy(update={"preview_receipt": "0" * 64}),
            )

    def confirm(self, prepared):
        with self.fixture.session() as session:
            return self.caller(session).confirm(
                trip_id=TRIPS[0],
                prepared=prepared,
                human_confirmation=prepared.confirmation_body,
            )

    def observe(self):
        with self.fixture.session() as session:
            return self.caller(session).observe(trip_id=TRIPS[0], binding=self.binding)

    def confirm_count(self):
        return sum(
            count
            for (method, path), count in self.calls.items()
            if method == "POST" and path.endswith("/continue")
        )

    def read_count(self):
        return sum(count for (method, _), count in self.calls.items() if method == "GET")


def test_explicit_confirmation_single_commit_and_concurrent_join(plan_fixture):
    pas = FakePAS(plan_fixture)
    prepared = pas.create_and_preview()
    assert prepared is not None
    assert prepared.destination.provider == "fake"
    prepared.metadata.selected_context.clear()
    assert len(prepared.metadata.selected_context) == 3
    assert not pas.confirm_count()
    with plan_fixture.session() as session:
        assert session.scalar(select(PrivatePlanConfirmation)) is None
    with plan_fixture.session() as session:
        with pytest.raises(DomainError) as error:
            pas.caller(session).confirm(trip_id=TRIPS[0], prepared=prepared, human_confirmation=b"")
        assert error.value.status_code == 409
    barrier = Barrier(2)

    def worker():
        barrier.wait()
        return pas.confirm(prepared)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(worker), pool.submit(worker)]
        assert all(result.result(timeout=10).state == "unknown" for result in results)
    assert pas.confirm_count() == 1
    assert pas.read_count() == 1
    assert pas.body == prepared.confirmation_body
    with plan_fixture.session() as session:
        with pytest.raises(DomainError) as error:
            pas.caller(session).confirm(
                trip_id=TRIPS[0],
                prepared=prepared,
                human_confirmation=b"{}",
            )
        assert error.value.status_code == 409
    assert pas.confirm_count() == 1


@pytest.mark.parametrize(
    "failure", ["timeout", "partial_sse", "429", "cancelled", "partial_status"]
)
def test_ambiguous_confirmation_and_restart_are_get_only(plan_fixture, failure):
    pas = FakePAS(plan_fixture)
    prepared = pas.create_and_preview()

    class Crash(BaseException):
        pass

    def fail(request):
        if failure == "cancelled":
            raise Crash
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic read timeout", request=request)
        if failure == "partial_sse":
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=b"data:"
            )
        return httpx.Response(429)

    pas.fail_confirmation = fail
    if failure == "cancelled":
        with pytest.raises(Crash):
            pas.confirm(prepared)
    else:
        assert pas.confirm(prepared).state == "unknown"
    if failure == "partial_status":
        pas.status_kind = "partial"
    assert pas.confirm(prepared).state == "unknown"
    pas.status_kind = "completed"
    assert pas.observe().result == pas.result
    assert pas.confirm_count() == 1


def test_wrong_trip_receipt_same_email_and_revoked_session_deny_before_post(plan_fixture: Fixture):
    pas = FakePAS(plan_fixture)
    prepared = pas.create_and_preview()
    crossed = plan_fixture.receipt(CONVERSATIONS[1], ORIGINAL_KEY, CONTENT, revision=REVISIONS[1])
    with plan_fixture.session() as session:
        caller = pas.caller(session)
        with pytest.raises(DomainError) as error:
            caller.confirm(
                trip_id=TRIPS[1], prepared=prepared, human_confirmation=prepared.confirmation_body
            )
        assert error.value.status_code == 404
        with pytest.raises(DomainError):
            caller.confirm(
                trip_id=TRIPS[0],
                prepared=replace(prepared, preview_receipt=crossed),
                human_confirmation=prepared.confirmation_body,
            )
        other = PrivateDispatchJournal(
            plan_fixture.journal(session, owner="cross-owner", subject="another-subject"),
            pas.registration,
        )
        with pytest.raises(DomainError):
            other.submit(
                trip_id=TRIPS[0], prepared=prepared, human_confirmation=prepared.confirmation_body
            )
    with plan_fixture.session() as session, session.begin():
        session.get(AuthSession, SESSION).revoked_at = pas.now
    with pytest.raises(DomainError):
        pas.confirm(prepared)
    assert pas.confirm_count() == 0


def test_revocation_after_durable_mark_consumes_its_send_opportunity(plan_fixture):
    pas = FakePAS(plan_fixture)
    prepared = pas.create_and_preview()
    decision = pas.registration.continuity

    def continuity(session, binding, body_digest):
        if body_digest is not None:
            return False
        return decision(session, binding, body_digest)

    pas.registration = replace(pas.registration, continuity=continuity)
    assert pas.confirm(prepared).state == "unknown"
    assert pas.confirm_count() == 0
    pas.registration = replace(pas.registration, continuity=decision)
    # Recovery may query status but repeat confirmation cannot issue even after recovery.
    with plan_fixture.session() as session:
        assert session.scalar(select(PrivatePlanConfirmation)).body == prepared.confirmation_body


@pytest.mark.parametrize("stale_at", ["before_status", "after_status", "after_detail"])
def test_current_trip_revision_and_grants_gate_each_read(plan_fixture, stale_at):
    pas = FakePAS(plan_fixture)
    prepared = pas.create_and_preview()
    pas.confirm(prepared)
    pas.status_kind = "completed"

    def revoke():
        if stale_at == "after_detail":
            pas.grants = replace(pas.grants, permission_epoch=pas.grants.permission_epoch + 1)
        else:
            with plan_fixture.session() as session, session.begin():
                session.get(Trip, TRIPS[0]).revision += 1

    if stale_at == "before_status":
        revoke()
        with pytest.raises(DomainError):
            pas.observe()
        assert pas.read_count() == 0
    else:
        if stale_at == "after_status":
            pas.status_hook = revoke
        else:
            pas.detail_hook = revoke
        assert pas.observe().result is None
        assert pas.read_count() == (1 if stale_at == "after_status" else 2)
        with plan_fixture.session() as session:
            assert session.scalar(select(PrivatePlanConfirmation)).quarantined_at is not None
    assert pas.confirm_count() == 1


@pytest.mark.parametrize("invalid", ["wrong_root", "wrong_result"])
def test_terminal_and_detail_are_bound_to_original_attempt(plan_fixture, invalid):
    pas = FakePAS(plan_fixture)
    prepared = pas.create_and_preview()
    pas.confirm(prepared)
    pas.status_kind = "wrong_root" if invalid == "wrong_root" else "completed"
    if invalid == "wrong_result":
        pas.result = "wrong detail"
    assert pas.observe().state == "quarantined"
    assert pas.read_count() == (1 if invalid == "wrong_root" else 2)
    assert pas.confirm_count() == 1


def test_authenticated_current_source_consumes_nonce_and_blocks_unknown_continuity(plan_fixture):
    pas = FakePAS(plan_fixture)
    prepared = pas.create_and_preview()
    source = RegisteredTravelSource(lambda: pas.journal(plan_fixture.session()), lambda: pas.now)
    nonce = str(uuid4())
    assert source.current(pas.binding, "preview", nonce).current
    assert not source.current(pas.binding, "preview", nonce).current
    assert not source.current(pas.binding, "send", str(uuid4())).current
    pas.confirm(prepared)
    assert source.current(pas.binding, "send", str(uuid4())).current
    pas.continuity = False
    assert not source.current(pas.binding, "disclose", str(uuid4())).current
    with pytest.raises(DomainError) as error:
        pas.observe()
    assert error.value.status_code == 503


def test_no_private_dispatch_without_explicit_registration(plan_fixture):
    with plan_fixture.session() as session:
        journal = PrivateDispatchJournal(plan_fixture.journal(session))
        with pytest.raises(DomainError) as error:
            journal._registration()
        assert error.value.status_code == 404


def test_older_postgresql_restore_uses_independently_retained_submit_checkpoint(
    plan_fixture, tmp_path
):
    """Restore a real pre-submission database backup into an independent database."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import URL, create_engine, text
    from sqlalchemy.exc import IntegrityError

    pg_dump = which("pg_dump")
    pg_restore = which("pg_restore")
    database = plan_fixture.engine.url
    if not pg_dump or not pg_restore:
        pytest.skip("disposable PostgreSQL restore requires pg_dump and pg_restore")
    if database.host != "127.0.0.1" or not database.database.startswith("test_"):
        pytest.skip("restore uses a dedicated loopback test database")

    pas = FakePAS(plan_fixture)
    prepared = pas.create_and_preview()
    checkpoint = tmp_path / "retained-checkpoint"
    checkpoint.write_text("unsubmitted")

    def continuity(_session, binding, submitted):
        return binding.commitment() == pas.binding.commitment() and checkpoint.read_text() == (
            submitted or "unsubmitted"
        )

    # The independently retained witness sees an advancing mark at the postcommit check.
    def allow_known_forward_progress(session, binding, submitted):
        return (
            session.bind is plan_fixture.engine
            and binding.commitment() == pas.binding.commitment()
            and (submitted is None or submitted == digest(prepared.confirmation_body))
        ) or continuity(session, binding, submitted)

    pas.registration = replace(pas.registration, continuity=allow_known_forward_progress)
    with plan_fixture.engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    base = URL.create(
        "postgresql",
        username=database.username,
        host=database.host,
        port=database.port,
        database=database.database,
    )
    pg_env = dict(environ, PGPASSWORD=database.password or "")
    archive = tmp_path / "old-native-db.dump"
    run(
        [
            pg_dump,
            "--no-password",
            "--format=custom",
            "--schema",
            schema,
            "--file",
            str(archive),
            "--dbname",
            base.render_as_string(),
        ],
        check=True,
        env=pg_env,
        timeout=15,
    )
    assert pas.confirm(prepared).state == "unknown"
    checkpoint.write_text(digest(pas.body))
    pas.registration = replace(pas.registration, continuity=continuity)
    assert pas.confirm_count() == 1

    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    with plan_fixture.engine.begin() as connection:
        config.attributes["connection"] = connection
        with pytest.raises(RuntimeError, match="retained private plan submissions"):
            command.downgrade(config, "0022")
    with plan_fixture.engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text("UPDATE personal_ai_private_plan_confirmations SET body_digest = :wrong"),
            {"wrong": "0" * 64},
        )

    restored_name = f"test_travel_dispatch_restore_{uuid4().hex}"
    admin = create_engine(database, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{restored_name}"')
    restored = create_engine(
        database.set(database=restored_name), connect_args={"options": f"-csearch_path={schema}"}
    )
    try:
        run(
            [
                pg_restore,
                "--no-password",
                "--exit-on-error",
                "--dbname",
                base.set(database=restored_name).render_as_string(),
                str(archive),
            ],
            check=True,
            env=pg_env,
            timeout=15,
        )
        with restored.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM personal_ai_private_plan_confirmations")
                )
                == 0
            )
            config.attributes["connection"] = connection
            command.downgrade(config, "0022")
            command.upgrade(config, "0023")
            command.check(config)
        # A new session on the restored database sees a valid preview but the missing durable
        # submission conflicts with the outside checkpoint. No POST or disclosure can follow.
        with Session(restored, expire_on_commit=False) as session:
            with pytest.raises(DomainError) as error:
                pas.caller(session).confirm(
                    trip_id=TRIPS[0],
                    prepared=prepared,
                    human_confirmation=prepared.confirmation_body,
                )
            assert error.value.status_code == 503
        assert pas.confirm_count() == 1 and pas.read_count() == 0
    finally:
        restored.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{restored_name}"')
        admin.dispose()


def test_expired_confirmation_and_erased_owner_issue_no_post(plan_fixture):
    pas = FakePAS(plan_fixture)
    prepared = pas.create_and_preview()
    make_journal = pas.journal

    def later(session):
        journal = make_journal(session)
        journal.legacy.clock = lambda: pas.now + timedelta(seconds=21)
        return journal

    pas.journal = later
    with pytest.raises(DomainError) as expired:
        pas.confirm(prepared)
    assert expired.value.status_code == 410
    pas.journal = make_journal
    with plan_fixture.session() as session, session.begin():
        freeze_owner(session, OWNER)
    with pytest.raises(DomainError) as erased:
        pas.confirm(prepared)
    assert erased.value.status_code == 404
    assert pas.confirm_count() == 0


def test_preview_or_terminal_key_revocation_blocks_only_current_authority(plan_fixture):
    pas = FakePAS(plan_fixture)
    prepared = pas.create_and_preview()
    pas.preview_active = False
    with pytest.raises(DomainError):
        pas.confirm(prepared)
    assert pas.confirm_count() == 0
    pas.preview_active = True
    pas.confirm(prepared)
    pas.terminal_active = False
    with pytest.raises(DomainError):
        pas.observe()
    assert pas.confirm_count() == 1
    assert pas.read_count() == 0
