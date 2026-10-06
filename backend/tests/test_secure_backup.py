from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.secure_backup import (
    BackupError,
    _pgpass_escape,
    _postgres_connection,
    _safe_relative_path,
    _store_inventory,
    create_backup,
)


def test_backup_connection_removes_password_and_unneeded_query_parameters() -> None:
    dsn, password, host = _postgres_connection(
        "postgresql+psycopg://travel:s%40cret@db.example.test:5444/travel"
        "?sslmode=require&application_name=private-value"
    )
    assert dsn == "postgresql://travel@db.example.test:5444/travel?sslmode=require"
    assert password == "s@cret"
    assert host == "db.example.test"
    assert "secret" not in dsn
    assert "***" not in dsn
    assert "private-value" not in dsn


def test_backup_connection_rejects_non_postgresql_urls() -> None:
    with pytest.raises(BackupError, match="require PostgreSQL"):
        _postgres_connection("sqlite:///travel.db")


def test_pgpass_values_escape_delimiters() -> None:
    assert _pgpass_escape("db:node\\blue") == "db\\:node\\\\blue"
    with pytest.raises(BackupError, match="line breaks"):
        _pgpass_escape("line\nbreak")


@pytest.mark.parametrize("value", ["../secret", "/absolute", "a/../b", "a\\b", ""])
def test_backup_paths_reject_traversal(value: str) -> None:
    with pytest.raises(BackupError, match="path is invalid"):
        _safe_relative_path(value)


def test_private_store_inventory_hashes_files_and_omits_coordination_locks(
    tmp_path: Path,
) -> None:
    store = tmp_path / "private"
    store.mkdir()
    (store / "opaque-object").write_bytes(b"private bytes")
    (store / "opaque-object.lock").write_bytes(b"coordination only")

    assert _store_inventory(store) == [
        {
            "path": "opaque-object",
            "bytes": 13,
            "sha256": hashlib.sha256(b"private bytes").hexdigest(),
        }
    ]


def test_private_store_inventory_rejects_symlinks(tmp_path: Path) -> None:
    store = tmp_path / "private"
    store.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"do not follow")
    (store / "link").symlink_to(outside)

    with pytest.raises(BackupError, match="symbolic link"):
        _store_inventory(store)


def test_backup_requires_operator_to_acknowledge_stopped_writers(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="confirm-writes-stopped"):
        create_backup(
            database_url="",
            store_dir=tmp_path / "missing-store",
            output_dir=tmp_path / "backups",
            recipient="age1synthetic",
            writes_stopped=False,
        )
