from __future__ import annotations

import asyncio
import hashlib
import os
import resource
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, get_ident
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

import personal_travel.api.middleware as middleware
import personal_travel.api.routes.imports as import_routes
import personal_travel.services.source_parser as source_parser
from personal_travel.auth.contracts import VerifiedPrincipal
from personal_travel.auth.google_oidc import stable_google_owner_id
from personal_travel.auth.sessions import create_session
from personal_travel.config import Settings
from personal_travel.models import BookingImport, SourceAttachment
from personal_travel.services.source_cleanup import cleanup
from personal_travel.services.source_parser import SourceParseError, parse_pdf
from personal_travel.services.source_store import LocalSourceStore


def private_settings(root: Path) -> Settings:
    return Settings(
        travel_auth_mode="google_oidc",
        google_oauth_client_id="synthetic-client.apps.googleusercontent.com",
        google_oauth_client_secret="synthetic-secret",
        google_oauth_redirect_uri="https://travel.test/auth/google/callback",
        google_oauth_allowed_email="owner@gmail.com",
        private_imports_enabled=True,
        private_source_dir=str(root),
    )


def make_principal(subject: str = "synthetic") -> VerifiedPrincipal:
    return VerifiedPrincipal(
        issuer="https://accounts.google.com",
        subject=subject,
        owner_id=stable_google_owner_id("https://accounts.google.com", subject),
        email="owner@gmail.com",
        issued_at=int(time.time()),
        expires_at=int(time.time()) + 3600,
    )


def source_session_factory(engine: Engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class ThreadOwnedSession(Session):
    def __init__(self, *args, **kwargs) -> None:
        self.creator_thread = get_ident()
        super().__init__(*args, **kwargs)

    def get_bind(self, *args, **kwargs):
        if get_ident() != self.creator_thread:
            raise AssertionError("a SQLAlchemy Session crossed worker threads")
        return super().get_bind(*args, **kwargs)


def write_pdf(
    path: Path, *, pages: int = 1, text: str | None = None, password: str | None = None
) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=72, height=72)
        if text is not None:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            resources = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): writer._add_object(font)}
                    )
                }
            )
            page[NameObject("/Resources")] = resources
            content = DecodedStreamObject()
            content.set_data(f"BT /F1 10 Tf 1 1 Td ({text}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(content)
    if password is not None:
        writer.encrypt(password)
    with path.open("wb") as stream:
        writer.write(stream)


def allocation_limit_worker(_path, connection) -> None:
    if source_parser.sys.platform != "darwin":
        source_parser._set_limit(resource.RLIMIT_DATA, source_parser.PARSER_MEMORY_BYTES)
    blocks: list[bytearray] = []
    block_size = 8 * 1024 * 1024
    try:
        for _ in range(source_parser.PARSER_MEMORY_BYTES // block_size + 16):
            block = bytearray(block_size)
            for offset in range(0, block_size, 4096):
                block[offset] = 1
            blocks.append(block)
            time.sleep(0.01)
    except MemoryError:
        connection.send(("allocation_limit_reached", ""))
        connection.close()
        return
    connection.send(("allocation_probe_under_limit", ""))
    connection.close()


@pytest.fixture
def private_client(
    api_client: TestClient, database_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    settings = private_settings(root)
    monkeypatch.setattr(import_routes, "get_settings", lambda: settings)
    api_client.app.state.auth_settings_provider = lambda: settings
    principal = make_principal()
    with Session(database_engine) as session, session.begin():
        token, csrf, _ = create_session(session, principal, ttl_seconds=3600)
    api_client.cookies.set("__Host-travel_session", token)
    api_client.cookies.set("__Host-travel_csrf", csrf)
    headers = {
        "Origin": "http://localhost:3000",
        "X-CSRF-Token": csrf,
        "X-Import-Request-Key": "request_001",
        "Content-Type": "text/plain",
    }
    trip = api_client.post(
        "/v1/trips",
        headers={key: value for key, value in headers.items() if key != "Content-Type"},
        json={
            "title": "Synthetic",
            "start_date": "2026-10-04",
            "end_date": "2026-10-05",
            "timezone": "UTC",
        },
    )
    assert trip.status_code == 201, trip.text
    return api_client, database_engine, root, principal.owner_id, trip.json()["id"], headers


def test_upload_dedupe_download_delete_and_trip_tombstone(private_client) -> None:
    client, engine, root, owner, trip_id, headers = private_client
    url = f"/v1/trips/{trip_id}/imports"
    uploaded = client.post(
        url,
        headers=headers | {"X-Source-Filename": "../../secret\\name.txt"},
        content=b"Booking synthetic reservation",
    )
    assert uploaded.status_code == 200, uploaded.text
    item = uploaded.json()
    assert item["state"] == "received" and item["source_state"] == "ready"
    assert item["display_filename"] == "name.txt"
    assert import_routes._display_filename("folder/secr\u00e9t.txt") == "secr_t.txt"
    assert (
        client.post(url, headers=headers, content=b"Booking synthetic reservation").json()["id"]
        == item["id"]
    )
    alias = client.post(
        url,
        headers=headers | {"X-Import-Request-Key": "request_002"},
        content=b"Booking synthetic reservation",
    )
    assert alias.status_code == 409
    assert alias.json()["error"]["code"] == "source_already_imported"
    assert "original request key" in alias.json()["error"]["message"]
    assert client.post(url, headers=headers, content=b"Different source").status_code == 409
    source_url = f"{url}/{item['id']}/source"
    download = client.get(source_url)
    assert download.content == b"Booking synthetic reservation"
    assert download.headers["Content-Disposition"].startswith("attachment")
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    with Session(engine) as session:
        source = session.get(SourceAttachment, UUID(item["source_id"]))
        assert source and source.owner_id == owner and source.trip_id is not None
        assert (root / source.object_key).is_file()
        assert os.stat(root / source.object_key).st_mode & 0o777 == 0o600
    assert client.delete(source_url, headers=headers).status_code == 204
    assert client.delete(source_url, headers=headers).status_code == 204
    assert client.get(source_url).status_code == 404
    retained = client.get(f"{url}/{item['id']}")
    assert retained.status_code == 200
    assert retained.json()["source_state"] == "deleted"
    assert retained.json()["source_id"] is None
    assert retained.json()["sha256"] == hashlib.sha256(b"Booking synthetic reservation").hexdigest()
    assert (
        client.post(url, headers=headers, content=b"Booking synthetic reservation").json()["id"]
        == item["id"]
    )

    other = client.post(
        url,
        headers=headers | {"X-Import-Request-Key": "request_003"},
        content=b"Another synthetic source",
    )
    assert other.status_code == 200, other.text
    with Session(engine) as session, session.begin():
        pending_source = session.get(SourceAttachment, UUID(other.json()["source_id"]))
        assert pending_source is not None
        pending_source.state = "pending"
        pending_source.created_at = datetime.now(UTC) - timedelta(hours=2)
    assert client.delete(f"/v1/trips/{trip_id}", headers=headers).status_code == 204
    with Session(engine) as session:
        source = session.get(SourceAttachment, UUID(other.json()["source_id"]))
        assert source and source.trip_id is None and source.owner_id == owner
        assert source.object_key and (root / source.object_key).exists()
        assert session.get(BookingImport, UUID(other.json()["id"])) is None
    store = LocalSourceStore(root)
    try:
        first = cleanup(source_session_factory(engine), store)
        second = cleanup(source_session_factory(engine), store)
        assert first.reconciled >= 1
        assert second.reconciled == 0
    finally:
        store.close()
    with Session(engine) as session:
        assert session.get(SourceAttachment, UUID(other.json()["source_id"])) is None


def test_upload_auth_precedes_body_access_and_local_mode_stays_disabled(private_client) -> None:
    client, _, _, _, trip_id, headers = private_client
    url = f"/v1/trips/{trip_id}/imports"
    client.cookies.clear()
    assert client.post(url, content=b"x" * (1024 * 1024 + 1), headers=headers).status_code == 401

    receive_calls = 0
    sent = []

    async def run_unauthenticated_request() -> None:
        async def receive():
            nonlocal receive_calls
            receive_calls += 1
            raise AssertionError("the unauthenticated request body was touched")

        async def send(message):
            sent.append(message)

        path = f"/v1/trips/{trip_id}/imports"
        scope = {
            "type": "http",
            "app": client.app,
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"localhost"),
                (b"origin", b"http://localhost:3000"),
                (b"content-type", b"text/plain"),
                (b"x-import-request-key", b"unauthenticated_1"),
            ],
            "server": ("localhost", 80),
            "client": ("127.0.0.1", 1234),
        }
        await client.app.middleware_stack(scope, receive, send)

    asyncio.run(run_unauthenticated_request())
    assert receive_calls == 0
    assert (
        next(message["status"] for message in sent if message["type"] == "http.response.start")
        == 401
    )

    local = Settings()
    client.app.state.auth_settings_provider = lambda: local
    monkeypatch_settings = import_routes.get_settings
    import_routes.get_settings = lambda: local
    try:
        assert client.post(url, content=b"private", headers=headers).status_code == 404
    finally:
        import_routes.get_settings = monkeypatch_settings


def test_slow_chunked_upload_hits_receive_deadline(private_client, monkeypatch) -> None:
    client, engine, root, _, trip_id, headers = private_client
    monkeypatch.setattr(middleware, "UPLOAD_SECONDS", 0.05)
    path = f"/v1/trips/{trip_id}/imports"
    cookie = "; ".join(f"{name}={value}" for name, value in client.cookies.items())
    sent = []

    async def run_slow_request() -> None:
        receives = 0

        async def receive():
            nonlocal receives
            receives += 1
            if receives == 1:
                return {"type": "http.request", "body": b"first chunk", "more_body": True}
            await asyncio.sleep(0.15)
            return {"type": "http.request", "body": b"second chunk", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "app": client.app,
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"localhost"),
                (b"origin", b"http://localhost:3000"),
                (b"cookie", cookie.encode()),
                (b"x-csrf-token", headers["X-CSRF-Token"].encode()),
                (b"content-type", b"text/plain"),
                (b"x-import-request-key", b"slow_chunked_1"),
            ],
            "server": ("localhost", 80),
            "client": ("127.0.0.1", 1234),
        }
        await client.app.middleware_stack(scope, receive, send)

    asyncio.run(run_slow_request())
    response_status = next(
        message["status"] for message in sent if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    assert response_status == 408
    assert b'"upload_timeout"' in response_body
    assert not list(root.iterdir())
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BookingImport)) == 0
        assert session.scalar(select(func.count()).select_from(SourceAttachment)) == 0


def test_disconnected_upload_receive_cleans_pending_temp(private_client) -> None:
    client, engine, root, _, trip_id, headers = private_client
    path = f"/v1/trips/{trip_id}/imports"
    cookie = "; ".join(f"{name}={value}" for name, value in client.cookies.items())
    messages = [
        {"type": "http.request", "body": b"partial", "more_body": True},
        {"type": "http.disconnect"},
    ]
    sent = []

    async def run_disconnected_request() -> None:
        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "app": client.app,
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"localhost"),
                (b"origin", b"http://localhost:3000"),
                (b"cookie", cookie.encode()),
                (b"x-csrf-token", headers["X-CSRF-Token"].encode()),
                (b"content-type", b"text/plain"),
                (b"x-import-request-key", b"disconnect_1"),
            ],
            "server": ("localhost", 80),
            "client": ("127.0.0.1", 1234),
        }
        try:
            await client.app.middleware_stack(scope, receive, send)
        except Exception:
            # Starlette may finish the disconnected exchange with its safe 500
            # handler; the lifecycle invariant is that no row or temp survives.
            pass

    asyncio.run(run_disconnected_request())
    assert not list(root.iterdir())
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BookingImport)) == 0
        assert session.scalar(select(func.count()).select_from(SourceAttachment)) == 0


@pytest.mark.parametrize("blocked_work", ["fsync", "sql"])
def test_blocked_upload_work_does_not_stall_health_or_share_sessions(
    private_client, monkeypatch: pytest.MonkeyPatch, blocked_work: str
) -> None:
    client, engine, _, _, trip_id, headers = private_client
    client.app.state.auth_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        class_=ThreadOwnedSession,
    )
    entered = Event()
    release = Event()
    original_fsync = os.fsync

    def blocked_fsync(fd: int) -> None:
        entered.set()
        if not release.wait(5):
            raise TimeoutError("fsync test release timed out")
        original_fsync(fd)

    def blocked_sql(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        if "INSERT INTO source_attachments" in statement:
            entered.set()
            if not release.wait(5):
                raise TimeoutError("SQL test release timed out")

    if blocked_work == "fsync":
        monkeypatch.setattr(import_routes.os, "fsync", blocked_fsync)
    else:
        event.listen(engine, "before_cursor_execute", blocked_sql)

    cookie = "; ".join(f"{name}={value}" for name, value in client.cookies.items())

    async def exchange(path: str, body: bytes = b"", *, request_key: str | None = None) -> int:
        sent = []
        first_receive = True

        async def receive():
            nonlocal first_receive
            if first_receive:
                first_receive = False
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        request_headers = [(b"host", b"localhost")]
        if request_key is not None:
            request_headers.extend(
                [
                    (b"origin", b"http://localhost:3000"),
                    (b"cookie", cookie.encode()),
                    (b"x-csrf-token", headers["X-CSRF-Token"].encode()),
                    (b"content-type", b"text/plain"),
                    (b"x-import-request-key", request_key.encode()),
                ]
            )
        scope = {
            "type": "http",
            "app": client.app,
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST" if request_key is not None else "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": request_headers,
            "server": ("localhost", 80),
            "client": ("127.0.0.1", 1234),
        }
        await client.app.middleware_stack(scope, receive, send)
        return next(
            message["status"] for message in sent if message["type"] == "http.response.start"
        )

    async def run_concurrently() -> tuple[int, int, float]:
        upload_task = asyncio.create_task(
            exchange(
                f"/v1/trips/{trip_id}/imports",
                b"Blocked upload synthetic source",
                request_key=f"blocked_{blocked_work}_1",
            )
        )
        if not await asyncio.to_thread(entered.wait, 5):
            release.set()
            await upload_task
            raise AssertionError("the upload did not reach the blocked operation")
        started = time.monotonic()
        health_status = await asyncio.wait_for(exchange("/health"), timeout=1)
        health_elapsed = time.monotonic() - started
        release.set()
        upload_status = await asyncio.wait_for(upload_task, timeout=5)
        return health_status, upload_status, health_elapsed

    try:
        health_status, upload_status, health_elapsed = asyncio.run(run_concurrently())
    finally:
        release.set()
        if blocked_work == "sql":
            event.remove(engine, "before_cursor_execute", blocked_sql)
    assert health_status == 200
    assert upload_status == 200
    assert health_elapsed < 0.5


def test_upload_content_type_magic_and_size_bounds(private_client) -> None:
    client, _, _, _, trip_id, headers = private_client
    url = f"/v1/trips/{trip_id}/imports"
    mislabeled = client.post(url, headers=headers, content=b"%PDF-1.7 fake")
    assert mislabeled.status_code == 400, mislabeled.text
    malformed_pdf = client.post(
        url,
        headers=headers
        | {"X-Import-Request-Key": "request_005", "Content-Type": "application/pdf"},
        content=b"%PDF-1.7\nnot a valid PDF document",
    )
    assert malformed_pdf.status_code == 400
    assert malformed_pdf.json()["error"]["code"] == "invalid_pdf"
    assert (
        client.post(
            url,
            headers=headers
            | {"X-Import-Request-Key": "request_002", "Content-Type": "application/pdf"},
            content=b"plain text",
        ).status_code
        == 400
    )
    assert (
        client.post(
            url,
            headers=headers
            | {"X-Import-Request-Key": "request_003", "Content-Type": "application/zip"},
            content=b"archive",
        ).status_code
        == 415
    )
    assert (
        client.post(
            url,
            headers=headers | {"X-Import-Request-Key": "request_004"},
            content=b"x" * (1024 * 1024 + 1),
        ).status_code
        == 413
    )


def test_upload_owner_scope_and_foreign_owner_cleanup(private_client) -> None:
    client, engine, root, owner, trip_id, headers = private_client
    url = f"/v1/trips/{trip_id}/imports"
    uploaded = client.post(url, headers=headers, content=b"Owner-only synthetic source")
    import_id = UUID(uploaded.json()["id"])
    foreign = make_principal("different-synthetic")
    with Session(engine) as session, session.begin():
        token, csrf, _ = create_session(session, foreign, ttl_seconds=3600)
    client.cookies.set("__Host-travel_session", token)
    client.cookies.set("__Host-travel_csrf", csrf)
    assert client.get(f"{url}/{import_id}").status_code == 404
    assert client.get(f"{url}/{import_id}/source").status_code == 404

    store = LocalSourceStore(root)
    own_key, foreign_key = store.new_key(), store.new_key()
    store.write_temp(own_key, b"own expired bytes")
    store.promote(own_key)
    store.write_temp(foreign_key, b"foreign expired bytes")
    store.promote(foreign_key)
    try:
        with Session(engine) as session, session.begin():
            for key, source_owner in ((own_key, owner), (foreign_key, foreign.owner_id)):
                session.add(
                    SourceAttachment(
                        owner_id=source_owner,
                        trip_id=None,
                        object_key=key,
                        sha256=hashlib.sha256(b"expired").hexdigest(),
                        media_type="text/plain",
                        byte_size=17,
                        display_filename=None,
                        state="deleting",
                        expires_at=datetime.now(UTC) - timedelta(days=1),
                    )
                )
        result = cleanup(source_session_factory(engine), store, owner_id=owner, limit=10)
        assert result.reconciled == 1
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(SourceAttachment.id).where(SourceAttachment.object_key == foreign_key)
                )
                is not None
            )
        assert not store.exists(own_key)
        assert store.exists(foreign_key)
    finally:
        store.close()


def test_cleanup_sweeps_orphans_but_keeps_other_owner_blob(private_client) -> None:
    _, engine, root, _, trip_id, _ = private_client
    store = LocalSourceStore(root)
    foreign_key = store.new_key()
    orphan_key = store.new_key()
    store.write_temp(foreign_key, b"foreign active source")
    store.promote(foreign_key)
    store.write_temp(orphan_key, b"abandoned temp bytes")
    old = time.time() - 7200
    os.utime(store.temp_path(orphan_key), (old, old))
    try:
        with Session(engine) as session, session.begin():
            session.add(
                SourceAttachment(
                    owner_id="another-verified-owner",
                    trip_id=UUID(trip_id),
                    object_key=foreign_key,
                    sha256=hashlib.sha256(b"foreign active source").hexdigest(),
                    media_type="text/plain",
                    byte_size=len(b"foreign active source"),
                    display_filename=None,
                    state="ready",
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                )
            )
        result = cleanup(source_session_factory(engine), store, limit=10)
        assert result.reconciled == 1
        assert store.exists(foreign_key)
        assert not store.exists(orphan_key)
    finally:
        store.close()


def test_missing_blob_fails_closed_and_cleanup_retains_import_metadata(private_client) -> None:
    client, engine, root, _, trip_id, headers = private_client
    url = f"/v1/trips/{trip_id}/imports"
    uploaded = client.post(url, headers=headers, content=b"Missing blob synthetic")
    item = uploaded.json()
    with Session(engine) as session:
        stored = session.get(SourceAttachment, UUID(item["source_id"]))
        assert stored is not None
        source = root / stored.object_key
        object_key = stored.object_key
    source.unlink()
    assert client.get(f"{url}/{item['id']}").status_code == 410
    assert client.get(f"{url}/{item['id']}/source").status_code == 410
    store = LocalSourceStore(root)
    try:
        result = cleanup(source_session_factory(engine), store)
        assert result.reconciled == 1
    finally:
        store.close()
    with Session(engine) as session:
        retained = session.get(BookingImport, UUID(item["id"]))
        assert retained is not None
        assert retained.source_id is None
        assert retained.source_sha256 == hashlib.sha256(b"Missing blob synthetic").hexdigest()
        assert (
            session.scalar(
                select(SourceAttachment.id).where(SourceAttachment.object_key == object_key)
            )
            is None
        )
    assert client.get(f"{url}/{item['id']}").json()["source_state"] == "deleted"


def test_expired_source_is_unavailable_but_import_replay_metadata_survives(private_client) -> None:
    client, engine, root, _, trip_id, headers = private_client
    url = f"/v1/trips/{trip_id}/imports"
    uploaded = client.post(url, headers=headers, content=b"Expired synthetic source")
    item = uploaded.json()
    source_id = UUID(item["source_id"])
    with Session(engine) as session:
        session.execute(
            SourceAttachment.__table__.update()
            .where(SourceAttachment.id == source_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()

    metadata = client.get(f"{url}/{item['id']}")
    assert metadata.status_code == 200
    assert metadata.json()["source_state"] == "expired"
    assert client.get(f"{url}/{item['id']}/source").status_code == 410
    replay = client.post(url, headers=headers, content=b"Expired synthetic source")
    assert replay.status_code == 200
    assert replay.json()["id"] == item["id"]
    assert replay.json()["source_state"] == "expired"

    store = LocalSourceStore(root)
    try:
        result = cleanup(source_session_factory(engine), store)
    finally:
        store.close()
    assert result.reconciled == 1
    retained = client.get(f"{url}/{item['id']}")
    assert retained.status_code == 200
    assert retained.json()["source_state"] == "deleted"
    assert retained.json()["sha256"] == hashlib.sha256(b"Expired synthetic source").hexdigest()


def test_cleanup_ready_scan_cursor_reaches_missing_rows_after_healthy_rows(private_client) -> None:
    _, engine, root, owner, trip_id, _ = private_client
    store = LocalSourceStore(root)
    now = datetime.now(UTC)
    ids = [UUID(int=value) for value in range(101, 106)]
    missing_key = None
    try:
        with Session(engine) as session, session.begin():
            for index, source_id in enumerate(ids):
                content = f"source-{index}".encode()
                key = store.new_key()
                if index < 4:
                    store.write_temp(key, content)
                    store.promote(key)
                else:
                    missing_key = key
                session.add(
                    SourceAttachment(
                        id=source_id,
                        owner_id=owner,
                        trip_id=UUID(trip_id),
                        object_key=key,
                        sha256=hashlib.sha256(content).hexdigest(),
                        media_type="text/plain",
                        byte_size=len(content),
                        display_filename=None,
                        state="ready",
                        expires_at=now + timedelta(days=1),
                    )
                )
        first = cleanup(source_session_factory(engine), store, limit=2)
        assert first.inspected == 2 and first.reconciled == 0
        assert first.next_source_id == str(ids[1])
        second = cleanup(
            source_session_factory(engine), store, limit=2, after_source_id=first.next_source_id
        )
        assert second.inspected == 2 and second.reconciled == 0
        assert second.next_source_id == str(ids[3])
        third = cleanup(
            source_session_factory(engine), store, limit=2, after_source_id=second.next_source_id
        )
        assert third.reconciled == 1
        assert third.next_source_id is None
        with Session(engine) as session:
            assert session.get(SourceAttachment, ids[4]) is None
            assert all(
                session.get(SourceAttachment, source_id) is not None for source_id in ids[:4]
            )
        assert missing_key is not None and not store.exists(missing_key)
    finally:
        store.close()


def test_store_rejects_paths_symlinks_and_unsafe_modes(tmp_path: Path) -> None:
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(root)
    with pytest.raises(ValueError):
        LocalSourceStore(link)
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    os.chmod(public, 0o755)
    with pytest.raises(ValueError):
        LocalSourceStore(public)

    store = LocalSourceStore(root)
    outside = tmp_path / "outside"
    outside.write_text("do not read", encoding="utf-8")
    key = store.new_key()
    (root / key).symlink_to(outside)
    try:
        with pytest.raises(ValueError):
            store.read("../outside")
        with pytest.raises(OSError):
            store.read(key)
    finally:
        store.close()


def test_post_promotion_sql_failure_is_recovered_without_losing_import(
    private_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, root, _, trip_id, headers = private_client

    def fail_mark_ready(self, *_args, **_kwargs) -> None:
        raise SQLAlchemyError("synthetic post-promotion SQL failure")

    monkeypatch.setattr(import_routes.SourceLifecycleService, "mark_ready", fail_mark_ready)
    response = client.post(
        f"/v1/trips/{trip_id}/imports", headers=headers, content=b"Synthetic post-promotion failure"
    )
    assert response.status_code == 503
    assert "synthetic" not in response.text
    with Session(engine) as session:
        row = session.scalar(
            select(BookingImport).where(BookingImport.request_key == "request_001")
        )
        assert row is not None and row.source_id is not None
        import_id = row.id
        source_id = row.source_id
        source = session.get(SourceAttachment, row.source_id)
        assert source is not None and source.state == "pending"
        object_key = source.object_key
        session.execute(
            SourceAttachment.__table__.update()
            .where(SourceAttachment.id == source.id)
            .values(created_at=datetime.now(UTC) - timedelta(hours=2))
        )
        session.commit()
    assert (root / object_key).is_file()
    store = LocalSourceStore(root)
    try:
        result = cleanup(source_session_factory(engine), store)
    finally:
        store.close()
    assert result.reconciled == 1
    with Session(engine) as session:
        source = session.get(SourceAttachment, source_id)
        assert source is not None and source.state == "ready"
        assert session.get(BookingImport, import_id) is not None
    assert (root / object_key).is_file()


def test_concurrent_identical_uploads_commit_one_import(private_client) -> None:
    client, engine, _, _, trip_id, headers = private_client
    url = f"/v1/trips/{trip_id}/imports"

    def upload(_key: str):
        return client.post(
            url,
            headers=headers,
            content=b"Concurrent synthetic import",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(upload, ("first", "second")))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BookingImport)) == 1
        assert session.scalar(select(func.count()).select_from(SourceAttachment)) == 1


def test_concurrent_new_key_hash_alias_is_rejected(private_client) -> None:
    client, engine, _, _, trip_id, headers = private_client
    url = f"/v1/trips/{trip_id}/imports"

    def upload(key: str):
        return client.post(
            url,
            headers=headers | {"X-Import-Request-Key": key},
            content=b"Concurrent alias source",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(upload, ("alias_key_1", "alias_key_2")))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert (
        sum(
            response.json().get("error", {}).get("code") == "source_already_imported"
            for response in responses
        )
        == 1
    )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BookingImport)) == 1
        assert session.scalar(select(func.count()).select_from(SourceAttachment)) == 1


def test_pending_promotion_racing_delete_cannot_resurrect_source(
    private_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, root, _, trip_id, headers = private_client
    url = f"/v1/trips/{trip_id}/imports"
    entered = Event()
    release = Event()
    original_promote = LocalSourceStore.promote

    def paused_promote(self: LocalSourceStore, key: str) -> None:
        entered.set()
        if not release.wait(5):
            raise TimeoutError("promotion test release timed out")
        original_promote(self, key)

    monkeypatch.setattr(LocalSourceStore, "promote", paused_promote)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, url, headers=headers, content=b"Promotion delete race")
        try:
            assert entered.wait(5)
            with Session(engine) as session:
                item = session.scalar(
                    select(BookingImport).where(BookingImport.request_key == "request_001")
                )
                assert item is not None
                import_id = item.id
            deleted = client.delete(f"{url}/{import_id}/source", headers=headers)
            assert deleted.status_code == 204
        finally:
            release.set()
        upload = future.result(timeout=5)
    assert upload.status_code == 500
    with Session(engine) as session:
        item = session.get(BookingImport, import_id)
        assert item is not None and item.source_id is None
        assert session.scalar(select(func.count()).select_from(SourceAttachment)) == 0
    assert list(root.iterdir()) == []


def test_cleanup_racing_delete_is_idempotent_and_keeps_import(
    private_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, root, _, trip_id, headers = private_client
    url = f"/v1/trips/{trip_id}/imports"
    uploaded = client.post(url, headers=headers, content=b"Delete cleanup race")
    item = uploaded.json()
    with Session(engine) as session:
        source = session.get(SourceAttachment, UUID(item["source_id"]))
        assert source is not None
        object_key = source.object_key

    entered = Event()
    release = Event()
    original_delete = LocalSourceStore.delete

    def paused_delete(self: LocalSourceStore, key: str, *, temp: bool = False) -> None:
        if key == object_key and not temp and not entered.is_set():
            entered.set()
            if not release.wait(5):
                raise TimeoutError("delete test release timed out")
        original_delete(self, key, temp=temp)

    monkeypatch.setattr(LocalSourceStore, "delete", paused_delete)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.delete, f"{url}/{item['id']}/source", headers=headers)
        try:
            assert entered.wait(5)
            store = LocalSourceStore(root)
            try:
                result = cleanup(source_session_factory(engine), store)
            finally:
                store.close()
            assert result.reconciled == 1
        finally:
            release.set()
        deleted = future.result(timeout=5)
    assert deleted.status_code == 204
    detail = client.get(f"{url}/{item['id']}")
    assert detail.status_code == 200
    assert detail.json()["source_state"] == "deleted"
    assert detail.json()["sha256"] == hashlib.sha256(b"Delete cleanup race").hexdigest()


def test_pdf_parser_enforces_type_pages_text_deadline_and_no_network(tmp_path: Path) -> None:
    blank = tmp_path / "blank.pdf"
    write_pdf(blank)
    with pytest.raises(SourceParseError, match="scanned_pdf"):
        parse_pdf(blank)

    encrypted = tmp_path / "encrypted.pdf"
    write_pdf(encrypted, password="synthetic-password")
    with pytest.raises(SourceParseError, match="encrypted_pdf"):
        parse_pdf(encrypted)

    many_pages = tmp_path / "many.pdf"
    write_pdf(many_pages, pages=101)
    with pytest.raises(SourceParseError, match="too_many_pages"):
        parse_pdf(many_pages)

    long_text = tmp_path / "long-text.pdf"
    write_pdf(long_text, text="x" * 200_001)
    with pytest.raises(SourceParseError, match="too_much_text"):
        parse_pdf(long_text)

    original = {
        name: getattr(socket, name)
        for name in (
            "socket",
            "socketpair",
            "create_connection",
            "getaddrinfo",
            "gethostbyname",
            "gethostbyname_ex",
            "gethostbyaddr",
        )
    }
    try:
        source_parser._disable_network()
        with pytest.raises(OSError, match="Network access is disabled"):
            socket.socket()
        with pytest.raises(OSError, match="Network access is disabled"):
            socket.create_connection(("example.invalid", 443))
    finally:
        for name, value in original.items():
            setattr(socket, name, value)

    expected_limit = (
        "pdf_memory_limit" if source_parser.sys.platform == "darwin" else "allocation_limit_reached"
    )
    with pytest.raises(SourceParseError, match=expected_limit):
        parse_pdf(blank, worker=allocation_limit_worker)

    with pytest.raises(SourceParseError, match="pdf_timeout"):
        parse_pdf(blank, wall_time_seconds=0.01)
