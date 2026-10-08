"""Bounded PDF extraction in a disposable, memory-limited process."""

from __future__ import annotations

import ctypes
import logging
import multiprocessing as mp
import resource
import socket
import sys
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Final

from pypdf import PdfReader

MAX_PDF_PAGES: Final = 100
MAX_TEXT_CHARS: Final = 200_000
PARSER_SECONDS: Final = 8
PARSER_MEMORY_BYTES: Final = 512 * 1024 * 1024
PARSER_ADDRESS_SPACE_BYTES: Final = 1024 * 1024 * 1024
_DARWIN_PROC_PIDTASKINFO: Final = 4


class _ProcTaskInfo(ctypes.Structure):
    """Prefix-compatible Darwin proc_taskinfo used to read a worker's RSS."""

    _fields_ = [
        ("virtual_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("total_user", ctypes.c_uint64),
        ("total_system", ctypes.c_uint64),
        ("threads_user", ctypes.c_uint64),
        ("threads_system", ctypes.c_uint64),
        ("policy", ctypes.c_int32),
        ("faults", ctypes.c_int32),
        ("pageins", ctypes.c_int32),
        ("cow_faults", ctypes.c_int32),
        ("messages_sent", ctypes.c_int32),
        ("messages_received", ctypes.c_int32),
        ("syscalls_mach", ctypes.c_int32),
        ("syscalls_unix", ctypes.c_int32),
        ("context_switches", ctypes.c_int32),
        ("thread_count", ctypes.c_int32),
        ("running_threads", ctypes.c_int32),
        ("priority", ctypes.c_int32),
    ]


def _darwin_resident_bytes(pid: int) -> int | None:
    """Read RSS with libproc; macOS maps a huge shared cache, so RLIMIT_AS is unusable."""
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        query = libproc.proc_pidinfo
        query.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.POINTER(_ProcTaskInfo),
            ctypes.c_int,
        ]
        query.restype = ctypes.c_int
        info = _ProcTaskInfo()
        length = query(
            pid,
            _DARWIN_PROC_PIDTASKINFO,
            0,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
    except (AttributeError, OSError):
        return None
    if length != ctypes.sizeof(info):
        return None
    return int(info.resident_size)


class SourceParseError(Exception):
    """A safe, bounded parser failure code suitable for an API response."""


def _deny_network(*_args: object, **_kwargs: object) -> None:
    raise OSError("Network access is disabled in the PDF parser.")


def _disable_network() -> None:
    # pypdf does not fetch document links. These guards also make that property
    # explicit for this worker if a future parser helper tries to open a URL.
    socket.create_connection = _deny_network  # type: ignore[assignment]
    socket.socket = _deny_network  # type: ignore[assignment,misc]
    socket.socketpair = _deny_network  # type: ignore[assignment]
    socket.getaddrinfo = _deny_network  # type: ignore[assignment]
    socket.gethostbyname = _deny_network  # type: ignore[assignment]
    socket.gethostbyname_ex = _deny_network  # type: ignore[assignment]
    socket.gethostbyaddr = _deny_network  # type: ignore[assignment]


def _set_limit(which: int, maximum: int) -> None:
    soft, hard = resource.getrlimit(which)
    ceiling = maximum if hard == resource.RLIM_INFINITY else min(maximum, hard)
    resource.setrlimit(which, (ceiling, ceiling))


def _send(connection: Connection, code: str, text: str = "") -> None:
    connection.send((code, text))


def _receive_result(connection: Connection, failure_code: str) -> tuple[str, str]:
    try:
        value = connection.recv()
    except EOFError as exc:
        raise SourceParseError(failure_code) from exc
    if not isinstance(value, tuple) or len(value) != 2:
        raise SourceParseError(failure_code)
    code, text = value
    if not isinstance(code, str) or not isinstance(text, str):
        raise SourceParseError(failure_code)
    return code, text


def _worker(path: str, connection: Connection, require_text: bool = True) -> None:
    try:
        limits = (
            (resource.RLIMIT_CORE, 0, "parser_core_limit_failed"),
            (resource.RLIMIT_CPU, PARSER_SECONDS, "parser_cpu_limit_failed"),
        )
        if sys.platform != "darwin":
            limits += (
                (resource.RLIMIT_AS, PARSER_ADDRESS_SPACE_BYTES, "parser_address_limit_failed"),
                (resource.RLIMIT_DATA, PARSER_MEMORY_BYTES, "parser_data_limit_failed"),
            )
        for limit, maximum, failure_code in limits:
            try:
                if limit == resource.RLIMIT_CORE:
                    resource.setrlimit(limit, (0, 0))
                else:
                    _set_limit(limit, maximum)
            except (OSError, ValueError):
                _send(connection, failure_code)
                return
        try:
            _disable_network()
        except (OSError, ValueError):
            _send(connection, "parser_network_setup_failed")
            return
        logging.getLogger("pypdf").setLevel(logging.CRITICAL)
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            _send(connection, "encrypted_pdf")
            return
        if len(reader.pages) > MAX_PDF_PAGES:
            _send(connection, "too_many_pages")
            return
        if not require_text:
            _send(connection, "ok")
            return
        pieces: list[str] = []
        count = 0
        for page in reader.pages:
            part = page.extract_text(extraction_mode="plain") or ""
            count += len(part)
            if count > MAX_TEXT_CHARS:
                _send(connection, "too_much_text")
                return
            pieces.append(part)
        text = "\n".join(pieces)
        _send(connection, "ok" if text.strip() else "scanned_pdf", text)
    except BaseException:
        # Parser traces may include document-controlled values. Only a fixed
        # failure code crosses back into the API process.
        try:
            _send(connection, "invalid_pdf")
        except (BrokenPipeError, OSError):
            pass
    finally:
        connection.close()


def _image_worker(path: str, media_type: str, connection: Connection) -> None:
    """Check untrusted image structure in a disposable resource-limited process."""
    try:
        limits = (
            (resource.RLIMIT_CORE, 0, "image_core_limit_failed"),
            (resource.RLIMIT_CPU, 4, "image_cpu_limit_failed"),
        )
        if sys.platform != "darwin":
            limits += (
                (resource.RLIMIT_AS, PARSER_ADDRESS_SPACE_BYTES, "image_address_limit_failed"),
                (resource.RLIMIT_DATA, PARSER_MEMORY_BYTES, "image_data_limit_failed"),
            )
        for limit, maximum, failure_code in limits:
            try:
                if limit == resource.RLIMIT_CORE:
                    resource.setrlimit(limit, (0, 0))
                else:
                    _set_limit(limit, maximum)
            except (OSError, ValueError):
                _send(connection, failure_code)
                return
        _disable_network()
        from personal_travel.services.attachment_validation import (
            AttachmentValidationError,
            _validate_jpeg,
            _validate_png,
        )

        data = Path(path).read_bytes()
        if media_type == "image/png":
            _validate_png(data)
        elif media_type == "image/jpeg":
            _validate_jpeg(data)
        else:
            _send(connection, "unsupported_image_type")
            return
        _send(connection, "ok")
    except AttachmentValidationError as exc:
        _send(connection, str(exc.args[0]) if exc.args else "invalid_image")
    except BaseException:
        # Image data and parser details remain inside the disposable worker.
        try:
            _send(connection, "invalid_image")
        except (BrokenPipeError, OSError):
            pass
    finally:
        connection.close()


def parse_pdf(
    path: Path,
    *,
    wall_time_seconds: float = PARSER_SECONDS,
    worker: Callable[[str, Connection], None] | None = None,
    require_text: bool = True,
) -> str:
    """Extract text, killing the child if its wall clock or process limits fail."""
    if wall_time_seconds <= 0 or wall_time_seconds > PARSER_SECONDS:
        raise ValueError("The parser deadline must be between zero and eight seconds.")
    context = mp.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    target = worker or _worker
    arguments = (str(path), child) if worker is not None else (str(path), child, require_text)
    process = context.Process(target=target, args=arguments)
    started = time.monotonic()
    child_closed = False
    try:
        process.start()
        child.close()
        child_closed = True
        deadline = started + wall_time_seconds
        result: tuple[str, str] | None = None
        while result is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceParseError("pdf_timeout")
            if sys.platform == "darwin" and process.is_alive():
                resident = _darwin_resident_bytes(process.pid or -1)
                if resident is None:
                    process.terminate()
                    raise SourceParseError("pdf_memory_monitor_failed")
                if resident > PARSER_MEMORY_BYTES:
                    process.terminate()
                    raise SourceParseError("pdf_memory_limit")
            if parent.poll(min(remaining, 0.05)):
                result = _receive_result(parent, "pdf_parser_failed")
            elif not process.is_alive():
                raise SourceParseError("pdf_parser_failed")

        code, text = result
        if code != "ok":
            raise SourceParseError(code)
        if len(text) > MAX_TEXT_CHARS:
            raise SourceParseError("too_much_text")
        return text
    except SourceParseError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise SourceParseError("pdf_parser_failed") from exc
    finally:
        if not child_closed:
            child.close()
        parent.close()
        if process.pid is not None:
            process.join(0.1)
            if process.is_alive():
                process.terminate()
                process.join(0.5)
            if process.is_alive():
                process.kill()
            process.join()


def validate_image(path: Path, media_type: str, *, wall_time_seconds: float = 5) -> None:
    """Run structural image checks with a wall clock and process resource limits."""
    if wall_time_seconds <= 0 or wall_time_seconds > 5:
        raise ValueError("The image parser deadline must be between zero and five seconds.")
    context = mp.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_image_worker, args=(str(path), media_type, child))
    child_closed = False
    started = time.monotonic()
    try:
        process.start()
        child.close()
        child_closed = True
        deadline = started + wall_time_seconds
        result: tuple[str, str] | None = None
        while result is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceParseError("image_timeout")
            if sys.platform == "darwin" and process.is_alive():
                resident = _darwin_resident_bytes(process.pid or -1)
                if resident is None:
                    process.terminate()
                    raise SourceParseError("image_memory_monitor_failed")
                if resident > PARSER_MEMORY_BYTES:
                    process.terminate()
                    raise SourceParseError("image_memory_limit")
            if parent.poll(min(remaining, 0.05)):
                result = _receive_result(parent, "image_parser_failed")
            elif not process.is_alive():
                raise SourceParseError("image_parser_failed")
        code, _ = result
        if code != "ok":
            raise SourceParseError(code)
    except SourceParseError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise SourceParseError("image_parser_failed") from exc
    finally:
        if not child_closed:
            child.close()
        parent.close()
        if process.pid is not None:
            process.join(0.1)
            if process.is_alive():
                process.terminate()
                process.join(0.5)
            if process.is_alive():
                process.kill()
                process.join()


def validate_pdf(path: Path, *, wall_time_seconds: float = PARSER_SECONDS) -> None:
    """Structure-check a document PDF without requiring extractable text."""
    parse_pdf(path, wall_time_seconds=wall_time_seconds, require_text=False)
