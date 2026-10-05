from __future__ import annotations

import hashlib
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

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
    assert (
        client.post(
            url,
            headers=headers | {"X-Import-Request-Key": "request_002"},
            content=b"Booking synthetic reservation",
        ).json()["id"]
        == item["id"]
    )
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

    other = client.post(
        url,
        headers=headers | {"X-Import-Request-Key": "request_003"},
        content=b"Another synthetic source",
    )
    assert other.status_code == 200, other.text
    assert client.delete(f"/v1/trips/{trip_id}", headers=headers).status_code == 204
    with Session(engine) as session:
        source = session.get(SourceAttachment, UUID(other.json()["source_id"]))
        assert source and source.trip_id is None and source.owner_id == owner
        assert source.object_key and (root / source.object_key).exists()
        assert session.get(BookingImport, UUID(other.json()["id"])) is None
    store = LocalSourceStore(root)
    try:
        with Session(engine) as session:
            assert cleanup(session, store) >= 1
            assert cleanup(session, store) == 0
    finally:
        store.close()
    with Session(engine) as session:
        assert session.get(SourceAttachment, UUID(other.json()["source_id"])) is None


def test_upload_auth_precedes_body_access_and_local_mode_stays_disabled(private_client) -> None:
    client, _, _, _, trip_id, headers = private_client
    url = f"/v1/trips/{trip_id}/imports"
    client.cookies.clear()
    assert client.post(url, content=b"x" * (1024 * 1024 + 1), headers=headers).status_code == 401

    local = Settings()
    client.app.state.auth_settings_provider = lambda: local
    monkeypatch_settings = import_routes.get_settings
    import_routes.get_settings = lambda: local
    try:
        assert client.post(url, content=b"private", headers=headers).status_code == 404
    finally:
        import_routes.get_settings = monkeypatch_settings


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
        with Session(engine) as session:
            assert cleanup(session, store, owner_id=owner, limit=10) == 1
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
        with Session(engine) as session:
            assert cleanup(session, store, limit=10) == 1
        assert store.exists(foreign_key)
        assert not store.exists(orphan_key)
    finally:
        store.close()


def test_missing_blob_fails_closed_and_cleanup_finalizes_metadata(private_client) -> None:
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
        with Session(engine) as session:
            assert cleanup(session, store) == 1
    finally:
        store.close()
    with Session(engine) as session:
        assert (
            session.scalar(
                select(SourceAttachment.id).where(SourceAttachment.object_key == object_key)
            )
            is None
        )


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


def test_promote_failure_compensates_sql_and_blob(
    private_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, root, _, trip_id, headers = private_client

    def fail_promotion(self: LocalSourceStore, key: str) -> None:
        raise OSError("synthetic promotion failure with private detail")

    monkeypatch.setattr(LocalSourceStore, "promote", fail_promotion)
    response = client.post(
        f"/v1/trips/{trip_id}/imports", headers=headers, content=b"Synthetic promotion failure"
    )
    assert response.status_code == 500
    assert "private detail" not in response.text
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BookingImport)) == 0
        assert session.scalar(select(func.count()).select_from(SourceAttachment)) == 0
    assert list(root.iterdir()) == []


def test_concurrent_identical_uploads_commit_one_import(private_client) -> None:
    client, engine, _, _, trip_id, headers = private_client
    url = f"/v1/trips/{trip_id}/imports"

    def upload(key: str):
        return client.post(
            url,
            headers=headers | {"X-Import-Request-Key": key},
            content=b"Concurrent synthetic import",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(upload, ("concurrent_1", "concurrent_2")))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BookingImport)) == 1
        assert session.scalar(select(func.count()).select_from(SourceAttachment)) == 1


def test_pdf_parser_enforces_type_pages_text_deadline_and_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    with monkeypatch.context() as patch:
        patch.setattr(source_parser.sys, "platform", "darwin")
        patch.setattr(
            source_parser,
            "_darwin_resident_bytes",
            lambda _pid: source_parser.PARSER_MEMORY_BYTES + 1,
        )
        with pytest.raises(SourceParseError, match="pdf_memory_limit"):
            parse_pdf(blank)

    with pytest.raises(SourceParseError, match="pdf_timeout"):
        parse_pdf(blank, wall_time_seconds=0.01)
