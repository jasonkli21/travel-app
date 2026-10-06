"""Create, inspect, and restore encrypted PostgreSQL/private-store snapshots.

The caller must stop every writer for the full backup window. Restore always
targets a separate empty PostgreSQL database and a new private-store directory.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "database.dump.age"
ARCHIVE_FORMAT = "personal-travel-ops-backup-v1"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


class BackupError(Exception):
    """An operator-safe backup/restore failure."""


def _postgres_connection(database_url: str) -> tuple[str, str, str]:
    try:
        url = make_url(database_url)
    except Exception as exc:
        raise BackupError("DATABASE_URL is not a valid database URL.") from exc
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise BackupError("Encrypted operations currently require PostgreSQL with psycopg.")
    if not url.host or not url.database or not url.username:
        raise BackupError("The database URL must include host, database, and user.")
    safe_query = {key: value for key, value in url.query.items() if key == "sslmode"}
    safe_url = url.set(drivername="postgresql", password=None, query=safe_query)
    rendered = safe_url.render_as_string(hide_password=True)
    return rendered, url.password or "", url.host


def _pgpass_escape(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise BackupError("Database credentials cannot contain line breaks.")
    return value.replace("\\", "\\\\").replace(":", "\\:")


@contextmanager
def _pgpass(database_url: str) -> Iterator[tuple[str, str]]:
    rendered, password, host = _postgres_connection(database_url)
    url = make_url(database_url)
    handle, filename = tempfile.mkstemp(prefix="travel-pgpass-")
    try:
        os.fchmod(handle, 0o600)
        line = ":".join(
            _pgpass_escape(part)
            for part in (
                host,
                str(url.port or 5432),
                url.database or "",
                url.username or "",
                password,
            )
        )
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(line + "\n")
        yield rendered, filename
    finally:
        try:
            os.unlink(filename)
        except FileNotFoundError:
            pass


def _subprocess_environment(passfile: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PGPASSFILE"] = passfile
    return environment


def _require_programs(*programs: str) -> None:
    missing = [program for program in programs if shutil.which(program) is None]
    if missing:
        raise BackupError("Required local tools are missing: " + ", ".join(missing))


def _current_revision(database_url: str) -> str:
    try:
        engine = create_engine(database_url, pool_size=1, max_overflow=0)
        try:
            with engine.connect() as connection:
                revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        finally:
            engine.dispose()
    except Exception as exc:
        raise BackupError("Could not read the database migration revision.") from exc
    if not isinstance(revision, str) or not revision:
        raise BackupError("The database does not have one current Alembic revision.")
    return revision


def _store_inventory(root: Path) -> list[dict[str, Any]]:
    if root.is_symlink() or not root.is_dir():
        raise BackupError("The private store must be an existing real directory.")
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        try:
            info = path.lstat()
        except OSError as exc:
            raise BackupError("Could not inspect the private store.") from exc
        if stat.S_ISLNK(info.st_mode):
            raise BackupError("The private store contains a symbolic link.")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BackupError("The private store contains an unsupported file type.")
        if path.name.endswith(".lock"):
            continue
        relative = path.relative_to(root).as_posix()
        _safe_relative_path(relative)
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as stream:
                while block := stream.read(CHUNK_BYTES):
                    size += len(block)
                    digest.update(block)
        except OSError as exc:
            raise BackupError("Could not hash a private-store object.") from exc
        files.append({"path": relative, "bytes": size, "sha256": digest.hexdigest()})
    return files


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in value
    ):
        raise BackupError("A backup object path is invalid.")
    return path


def _encrypt_database_dump(database_url: str, destination: Path, recipient: str) -> None:
    _require_programs("pg_dump", "age")
    if not recipient.startswith("age1"):
        raise BackupError("Use an age public recipient beginning with age1.")
    destination.touch(mode=0o600, exist_ok=False)
    with _pgpass(database_url) as (dsn, passfile), destination.open("wb") as encrypted:
        environment = _subprocess_environment(passfile)
        age = subprocess.Popen(
            ["age", "-r", recipient],
            stdin=subprocess.PIPE,
            stdout=encrypted,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        assert age.stdin is not None
        dump = subprocess.Popen(
            ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--dbname", dsn],
            stdout=age.stdin,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        age.stdin.close()
        dump_status = dump.wait()
        age_status = age.wait()
    if dump_status != 0 or age_status != 0 or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise BackupError("PostgreSQL dump or age encryption failed; no backup was published.")


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(CHUNK_BYTES):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def create_backup(
    *,
    database_url: str,
    store_dir: Path,
    output_dir: Path,
    recipient: str,
    writes_stopped: bool,
) -> Path:
    if not writes_stopped:
        raise BackupError("Pass --confirm-writes-stopped after stopping every application writer.")
    _require_programs("pg_dump", "age")
    if not recipient.startswith("age1"):
        raise BackupError("Use an age public recipient beginning with age1.")
    store_dir = store_dir.expanduser().absolute()
    output_dir = output_dir.expanduser().absolute()
    if (
        store_dir == output_dir
        or store_dir in output_dir.parents
        or output_dir in store_dir.parents
    ):
        raise BackupError("The output directory and private store must be separate.")
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not output_dir.is_dir() or output_dir.is_symlink():
        raise BackupError("The backup destination must be a real directory.")

    revision_before = _current_revision(database_url)
    files_before = _store_inventory(store_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_id = f"travel-{stamp}-{uuid4().hex[:8]}"
    scratch = Path(tempfile.mkdtemp(prefix="travel-backup-"))
    os.chmod(scratch, 0o700)
    database_ciphertext = scratch / DATABASE_NAME
    partial = output_dir / f".{backup_id}.tar.age.partial"
    published = output_dir / f"{backup_id}.tar.age"
    try:
        _encrypt_database_dump(database_url, database_ciphertext, recipient)
        revision_after = _current_revision(database_url)
        files_after = _store_inventory(store_dir)
        if revision_after != revision_before or files_after != files_before:
            raise BackupError("Database revision or private-store bytes changed during backup.")
        db_size, db_digest = _hash_file(database_ciphertext)
        manifest = {
            "format": ARCHIVE_FORMAT,
            "created_at": datetime.now(UTC).isoformat(),
            "alembic_revision": revision_before,
            "database_ciphertext": {"bytes": db_size, "sha256": db_digest},
            "private_objects": files_before,
        }
        encoded_manifest = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(encoded_manifest) > MAX_MANIFEST_BYTES:
            raise BackupError("Private-store inventory exceeds the supported manifest size.")

        partial.touch(mode=0o600, exist_ok=False)
        with partial.open("wb") as encrypted:
            age = subprocess.Popen(
                ["age", "-r", recipient],
                stdin=subprocess.PIPE,
                stdout=encrypted,
                stderr=subprocess.DEVNULL,
            )
            assert age.stdin is not None
            try:
                with tarfile.open(fileobj=age.stdin, mode="w|") as archive:
                    manifest_info = tarfile.TarInfo(MANIFEST_NAME)
                    manifest_info.size = len(encoded_manifest)
                    manifest_info.mode = 0o600
                    archive.addfile(manifest_info, io.BytesIO(encoded_manifest))
                    archive.add(database_ciphertext, arcname=DATABASE_NAME, recursive=False)
                    for item in files_before:
                        source = store_dir / _safe_relative_path(item["path"])
                        archive.add(
                            source,
                            arcname=f"private/{item['path']}",
                            recursive=False,
                        )
            finally:
                age.stdin.close()
                age_status = age.wait()
        files_final = _store_inventory(store_dir)
        if age_status != 0 or files_final != files_before:
            raise BackupError("Archive encryption failed or private-store bytes changed.")
        os.replace(partial, published)
        return published
    except BaseException:
        partial.unlink(missing_ok=True)
        published.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _read_archive(
    artifact: Path, identity: Path, scratch: Path
) -> tuple[dict[str, Any], Path, Path]:
    _require_programs("age")
    if not artifact.is_file() or artifact.is_symlink() or not identity.is_file():
        raise BackupError("Encrypted backup or age identity file is unavailable.")
    outer = subprocess.Popen(
        ["age", "-d", "-i", str(identity), str(artifact)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert outer.stdout is not None
    private_root = scratch / "private"
    private_root.mkdir(mode=0o700)
    database_ciphertext = scratch / DATABASE_NAME
    manifest: dict[str, Any] | None = None
    expected_members: set[str] | None = None
    seen: set[str] = set()
    try:
        with tarfile.open(fileobj=outer.stdout, mode="r|") as archive:
            for member in archive:
                if not member.isfile() or member.issym() or member.islnk():
                    raise BackupError("Backup contains an unsupported archive entry.")
                if member.name in seen:
                    raise BackupError("Backup contains a duplicate archive path.")
                seen.add(member.name)
                source = archive.extractfile(member)
                if source is None:
                    raise BackupError("Backup archive entry could not be read.")
                if member.name == MANIFEST_NAME and manifest is None:
                    if member.size > MAX_MANIFEST_BYTES:
                        raise BackupError("Backup manifest exceeds the supported size.")
                    try:
                        manifest = json.loads(source.read(MAX_MANIFEST_BYTES + 1))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise BackupError("Backup manifest is invalid.") from exc
                    if not isinstance(manifest, dict) or manifest.get("format") != ARCHIVE_FORMAT:
                        raise BackupError("Backup format is unsupported.")
                    private_objects = manifest.get("private_objects")
                    encrypted_info = manifest.get("database_ciphertext")
                    if not isinstance(private_objects, list) or not isinstance(
                        encrypted_info, dict
                    ):
                        raise BackupError("Backup manifest is incomplete.")
                    expected_members = {MANIFEST_NAME, DATABASE_NAME}
                    expected_members.update(
                        f"private/{_safe_relative_path(item['path']).as_posix()}"
                        for item in private_objects
                        if isinstance(item, dict) and isinstance(item.get("path"), str)
                    )
                    if len(expected_members) != len(private_objects) + 2:
                        raise BackupError("Backup manifest contains invalid or duplicate paths.")
                    continue
                if (
                    manifest is None
                    or expected_members is None
                    or member.name not in expected_members
                ):
                    raise BackupError("Backup contains an unlisted or misplaced object.")
                target = (
                    database_ciphertext
                    if member.name == DATABASE_NAME
                    else scratch / PurePosixPath(member.name)
                )
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                expected = (
                    manifest["database_ciphertext"]
                    if member.name == DATABASE_NAME
                    else next(
                        item
                        for item in manifest["private_objects"]
                        if f"private/{item['path']}" == member.name
                    )
                )
                if not isinstance(expected, dict) or member.size != expected.get("bytes"):
                    raise BackupError("Backup object size does not match its manifest.")
                digest = hashlib.sha256()
                copied = 0
                with target.open("xb") as output:
                    os.chmod(target, 0o600)
                    while block := source.read(CHUNK_BYTES):
                        copied += len(block)
                        digest.update(block)
                        output.write(block)
                if copied != member.size or digest.hexdigest() != expected.get("sha256"):
                    raise BackupError("Backup object hash does not match its manifest.")
        if outer.stdout:
            outer.stdout.close()
        if outer.wait() != 0:
            raise BackupError("Age could not decrypt or authenticate this backup.")
    except BaseException:
        if outer.poll() is None:
            outer.kill()
        outer.wait()
        raise
    if manifest is None or expected_members is None or seen != expected_members:
        raise BackupError("Backup is missing manifest-listed objects.")
    return manifest, database_ciphertext, private_root


def _verify_database_dump(database_ciphertext: Path, identity: Path) -> None:
    _require_programs("age", "pg_restore")
    with database_ciphertext.open("rb") as encrypted:
        age = subprocess.Popen(
            ["age", "-d", "-i", str(identity)],
            stdin=encrypted,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert age.stdout is not None
        restore = subprocess.Popen(
            ["pg_restore", "--list"],
            stdin=age.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        age.stdout.close()
        restore_status = restore.wait()
        age_status = age.wait()
    if restore_status != 0 or age_status != 0:
        raise BackupError("Encrypted PostgreSQL archive could not be verified.")


def verify_backup(artifact: Path, identity: Path) -> dict[str, Any]:
    started = time.monotonic()
    scratch = Path(tempfile.mkdtemp(prefix="travel-verify-"))
    os.chmod(scratch, 0o700)
    try:
        manifest, database_ciphertext, _private_root = _read_archive(
            artifact.expanduser().absolute(), identity.expanduser().absolute(), scratch
        )
        _verify_database_dump(database_ciphertext, identity.expanduser().absolute())
        return {
            "verified": True,
            "alembic_revision": manifest["alembic_revision"],
            "private_objects": len(manifest["private_objects"]),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _assert_empty_database(database_url: str) -> None:
    engine = create_engine(database_url, pool_size=1, max_overflow=0)
    try:
        with engine.connect() as connection:
            remaining = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_class AS relation "
                    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                    "WHERE namespace.nspname NOT LIKE 'pg_%' "
                    "AND namespace.nspname <> 'information_schema' "
                    "AND relation.relkind IN ('r','p','v','m','S','f')"
                )
            )
    except Exception as exc:
        raise BackupError("Could not confirm the restore database is empty.") from exc
    finally:
        engine.dispose()
    if remaining != 0:
        raise BackupError("Restore database is not empty; choose a separate empty database.")


def _restore_database(
    database_url: str, passfile: str, database_ciphertext: Path, identity: Path
) -> None:
    _require_programs("age", "pg_restore")
    dsn, _password, _host = _postgres_connection(database_url)
    environment = _subprocess_environment(passfile)
    with database_ciphertext.open("rb") as encrypted:
        age = subprocess.Popen(
            ["age", "-d", "-i", str(identity)],
            stdin=encrypted,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert age.stdout is not None
        restore = subprocess.Popen(
            [
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                "--dbname",
                dsn,
            ],
            stdin=age.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        age.stdout.close()
        restore_status = restore.wait()
        age_status = age.wait()
    if restore_status != 0 or age_status != 0:
        raise BackupError("PostgreSQL restore failed; keep this destination isolated for review.")


def _validate_restored_database(database_url: str, expected_revision: str) -> dict[str, int]:
    engine = create_engine(database_url, pool_size=1, max_overflow=0)
    try:
        with engine.begin() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != expected_revision:
                raise BackupError("Restored Alembic revision differs from the backup manifest.")
            counts = {
                table: int(connection.scalar(text(f"SELECT count(*) FROM {table}")) or 0)
                for table in ("trips", "reservations", "proposals", "booking_imports")
            }
            connection.exec_driver_sql(
                "CREATE TEMP TABLE phase9_restore_smoke (value integer) ON COMMIT DROP"
            )
            connection.exec_driver_sql("INSERT INTO phase9_restore_smoke VALUES (9)")
            probe = connection.scalar(text("SELECT value FROM phase9_restore_smoke"))
            if probe != 9:
                raise BackupError("Restored database write/read smoke check failed.")
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError("Restored database did not pass the read/write smoke check.") from exc
    finally:
        engine.dispose()
    return counts


def _check_alembic_parity(database_url: str, passfile: str) -> None:
    project_dir = Path(__file__).resolve().parents[1]
    environment = _subprocess_environment(passfile)
    environment.update(
        {
            "DATABASE_URL": database_url,
            "TRAVEL_DEPLOYMENT_MODE": "local",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=project_dir,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise BackupError("Restored schema does not match the current Alembic ORM metadata.")


def restore_backup(
    *,
    artifact: Path,
    identity: Path,
    database_url: str,
    store_destination: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    _assert_empty_database(database_url)
    store_destination = store_destination.expanduser().absolute()
    if store_destination.exists() or store_destination.is_symlink():
        raise BackupError("Restore private-store destination must not already exist.")
    _postgres_connection(database_url)
    scratch = Path(tempfile.mkdtemp(prefix="travel-restore-"))
    os.chmod(scratch, 0o700)
    staged_store: Path | None = None
    try:
        manifest, database_ciphertext, private_root = _read_archive(
            artifact.expanduser().absolute(), identity.expanduser().absolute(), scratch
        )
        _verify_database_dump(database_ciphertext, identity.expanduser().absolute())
        store_destination.parent.mkdir(parents=True, exist_ok=True)
        staged_store = store_destination.parent / f".{store_destination.name}.{uuid4().hex}.restore"
        shutil.copytree(private_root, staged_store, copy_function=shutil.copyfile)
        for path in staged_store.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o600)

        with _pgpass(database_url) as (dsn, passfile):
            _restore_database(database_url, passfile, database_ciphertext, identity)
            counts = _validate_restored_database(database_url, manifest["alembic_revision"])
            _check_alembic_parity(dsn, passfile)
        revision = _current_revision(database_url)
        os.replace(staged_store, store_destination)
        staged_store = None
        return {
            "restored": True,
            "alembic_revision": revision,
            "row_counts": counts,
            "private_objects": len(manifest["private_objects"]),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    finally:
        if staged_store is not None:
            shutil.rmtree(staged_store, ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser(
        "backup", help="create an age-encrypted SQL + private-store backup"
    )
    backup.add_argument("--store-dir", type=Path, required=True)
    backup.add_argument("--output-dir", type=Path, required=True)
    backup.add_argument("--recipient", required=True, help="age public recipient")
    backup.add_argument("--confirm-writes-stopped", action="store_true")

    verify = commands.add_parser("verify", help="decrypt, hash-check, and inspect a backup")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--identity", type=Path, required=True)

    restore = commands.add_parser("restore", help="restore to a separate empty database and store")
    restore.add_argument("--artifact", type=Path, required=True)
    restore.add_argument("--identity", type=Path, required=True)
    restore.add_argument("--store-dir", type=Path, required=True)
    restore.add_argument(
        "--database-url-env",
        default="RESTORE_DATABASE_URL",
        help="environment variable containing the separate restore database URL",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "backup":
            database_url = os.environ.get("DATABASE_URL", "")
            if not database_url:
                raise BackupError("Set DATABASE_URL for the source PostgreSQL database.")
            artifact = create_backup(
                database_url=database_url,
                store_dir=args.store_dir,
                output_dir=args.output_dir,
                recipient=args.recipient,
                writes_stopped=args.confirm_writes_stopped,
            )
            size, digest = _hash_file(artifact)
            payload = {"backup": str(artifact), "bytes": size, "sha256": digest}
        elif args.command == "verify":
            payload = verify_backup(args.artifact, args.identity)
        else:
            database_url = os.environ.get(args.database_url_env, "")
            if not database_url:
                raise BackupError(f"Set {args.database_url_env} to a separate empty database URL.")
            payload = restore_backup(
                artifact=args.artifact,
                identity=args.identity,
                database_url=database_url,
                store_destination=args.store_dir,
            )
        print(json.dumps(payload, sort_keys=True, indent=2))
        return 0
    except BackupError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
