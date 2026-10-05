"""Host/origin gate, request identity and a bounded pre-routing body boundary."""

import asyncio
import logging
import re
from datetime import UTC, datetime
from hmac import compare_digest
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from anyio.to_thread import run_sync
from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from personal_travel.api.errors import error_response
from personal_travel.auth.contracts import PersonalAIAuthContext, VerifiedPrincipal
from personal_travel.auth.google_oidc import (
    IdentityProviderUnavailable,
    InvalidIdentityToken,
    principal_from_google_token,
)
from personal_travel.auth.sessions import ActiveSession, load_active_session, matches_digest
from personal_travel.config import Settings, get_settings
from personal_travel.db.session import SessionFactory

logger = logging.getLogger("personal_travel.requests")
MAX_REQUEST_BYTES = 64 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
UPLOAD_PATH = re.compile(r"^/v1/trips/[0-9a-fA-F-]{36}/imports$")
EXTRACTION_PATH = re.compile(r"^/v1/trips/[0-9a-fA-F-]{36}/imports/[0-9a-fA-F-]{36}/extract$")
COMPARISON_PATH = re.compile(
    r"^/v1/trips/[0-9a-fA-F-]{36}/research/(?:compare|comparisons/[0-9a-fA-F-]{36}/candidates/[0-9a-fA-F-]{36}/save)$"
)
UPLOAD_SECONDS = 30


class UploadTooLarge(Exception):
    pass


class UploadDeadlineExceeded(Exception):
    pass


class LocalBoundaryMiddleware:
    def __init__(self, app: ASGIApp, *, hosts: list[str], origins: list[str]) -> None:
        self.app = app
        self.hosts = set(hosts)
        self.origins = set(origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        request_id = str(uuid4())
        started = monotonic()
        status = 500
        response_started = False

        async def send_response(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = request_id
                response_headers["Cache-Control"] = "no-store"
                response_headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        async def reject(code: int, name: str, message: str) -> None:
            await error_response(code, name, message)(scope, receive, send_response)

        try:
            try:
                host = urlsplit(f"//{headers.get('host', '')}")
                valid_host = (
                    host.hostname in self.hosts
                    and host.username is None
                    and host.password is None
                    and not host.path
                    and not host.query
                    and not host.fragment
                    and "\\" not in headers.get("host", "")
                )
                _ = host.port  # Validate the port syntax as well as the hostname.
            except ValueError:
                valid_host = False
            if not valid_host:
                await reject(400, "invalid_host", "This host is not allowed by the travel service.")
                return
            origin = headers.get("origin")
            if (origin is not None and origin not in self.origins) or (
                origin is None and headers.get("sec-fetch-site") == "cross-site"
            ):
                await reject(403, "invalid_origin", "This browser origin is not allowed.")
                return

            app = scope.get("app")
            state = getattr(app, "state", None)
            settings_provider = getattr(state, "auth_settings_provider", None) or get_settings
            settings: Settings = settings_provider()
            session_factory = getattr(state, "auth_session_factory", None) or SessionFactory
            request = Request(scope)
            path = scope.get("path", "")
            method = scope.get("method", "GET").upper()
            session_cookie = request.cookies.get(settings.auth_session_cookie_name)
            supplied_authorization = headers.get("authorization")
            supplied_user_token = headers.get("x-user-id-token")
            if headers.get("x-owner-id") is not None:
                await reject(400, "invalid_identity_header", "Owner identity is server managed.")
                return

            if method == "OPTIONS":
                scope["request_id"] = request_id
                await self.app(scope, receive, send_response)
                return

            if settings.travel_auth_mode == "local":
                if (
                    supplied_authorization is not None
                    or supplied_user_token is not None
                    or session_cookie is not None
                ):
                    await reject(
                        401,
                        "invalid_credentials",
                        "Credentials are not accepted while local mode is active.",
                    )
                    return
                current = int(datetime.now(UTC).timestamp())
                scope["principal"] = VerifiedPrincipal(
                    issuer="local",
                    subject=settings.owner_id,
                    owner_id=settings.owner_id,
                    email="",
                    issued_at=current,
                    expires_at=current + settings.auth_session_ttl_seconds,
                )
                scope["auth_session"] = None
            else:
                active_session: ActiveSession | None = None
                public_auth = (
                    (path == "/v1/auth/session" and method == "GET")
                    or (path == "/v1/auth/google/start" and method == "GET")
                    or (path == "/v1/auth/google/callback" and method == "POST")
                )
                if session_cookie is not None:
                    try:

                        def lookup_session() -> ActiveSession | None:
                            with session_factory() as db_session:
                                return load_active_session(db_session, session_cookie)

                        async with asyncio.timeout(5):
                            active_session = await run_sync(lookup_session, abandon_on_cancel=True)
                    except TimeoutError:
                        await reject(
                            503,
                            "authentication_unavailable",
                            "Authentication storage is temporarily unavailable.",
                        )
                        return
                    except SQLAlchemyError:
                        await reject(
                            503,
                            "authentication_unavailable",
                            "Authentication storage is temporarily unavailable.",
                        )
                        return
                    if active_session is not None and (
                        active_session.principal.email
                        != settings.google_oauth_allowed_email_normalized
                    ):
                        active_session = None
                    if active_session is None and not public_auth:
                        await reject(401, "session_expired", "Sign in again to continue.")
                        return
                needs_principal = path.startswith("/v1/") and not public_auth
                if needs_principal and active_session is None:
                    await reject(401, "authentication_required", "Sign in to continue.")
                    return
                if supplied_authorization is not None:
                    await reject(
                        400,
                        "invalid_identity_header",
                        "Travel API requests use the server-managed session cookie.",
                    )
                    return
                if active_session is not None:
                    scope["principal"] = active_session.principal
                    scope["auth_session"] = active_session
                else:
                    scope["principal"] = None
                    scope["auth_session"] = None

                unsafe = method in {"POST", "PUT", "PATCH", "DELETE"}
                is_oauth_callback = path == "/v1/auth/google/callback" and method == "POST"
                if unsafe and not is_oauth_callback:
                    if origin not in self.origins:
                        await reject(
                            403,
                            "invalid_origin",
                            "A same-origin request is required for this change.",
                        )
                        return
                    if active_session is None:
                        await reject(401, "authentication_required", "Sign in to continue.")
                        return
                    csrf_cookie = request.cookies.get(settings.auth_csrf_cookie_name, "")
                    csrf_header = headers.get("x-csrf-token", "")
                    if (
                        not csrf_cookie
                        or not csrf_header
                        or not compare_digest(csrf_cookie, csrf_header)
                        or not matches_digest(csrf_header, active_session.csrf_token_hash)
                    ):
                        await reject(403, "csrf_rejected", "The request could not be verified.")
                        return

                is_research_comparison_operation = COMPARISON_PATH.fullmatch(path) is not None
                is_research_operation = (
                    path.startswith("/v1/trips/")
                    and "/research" in path
                    and not is_research_comparison_operation
                )
                is_proposal_operation = path.startswith("/v1/trips/") and "/proposals" in path
                is_extraction_operation = (
                    path.startswith("/v1/trips/")
                    and "/imports/" in path
                    and path.endswith("/extract")
                )
                is_import_cleanup_operation = (
                    path.startswith("/v1/trips/")
                    and "/imports/" in path
                    and (
                        path.endswith("/confirm")
                        or path.endswith("/reject")
                        or (path.endswith("/source") and method == "DELETE")
                    )
                )
                is_deletion_retry_operation = (
                    path == "/v1/private-import-deletion-intents/retry" and method == "POST"
                )
                ai_operation = (
                    (is_research_operation and settings.personal_ai_research_enabled)
                    or (
                        is_research_comparison_operation
                        and settings.personal_ai_comparisons_enabled
                    )
                    or (is_proposal_operation and settings.personal_ai_proposals_enabled)
                    or (is_extraction_operation and settings.personal_ai_extractions_enabled)
                    or is_import_cleanup_operation
                    or is_deletion_retry_operation
                )
                if supplied_user_token is not None and not ai_operation:
                    await reject(
                        400,
                        "invalid_identity_header",
                        "User identity tokens are accepted only for configured AI operations.",
                    )
                    return
                if (
                    ai_operation
                    and settings.travel_auth_mode == "google_oidc"
                    and settings.personal_ai_auth_mode == "google_cloud_run_iam"
                    and active_session is not None
                ):
                    user_token = supplied_user_token or ""
                    if not user_token:
                        await reject(
                            401,
                            "ai_identity_required",
                            "A current verified AI user identity is required.",
                        )
                        return
                    try:
                        async with asyncio.timeout(4):
                            ai_principal = await run_sync(
                                principal_from_google_token,
                                user_token,
                                settings,
                                abandon_on_cancel=True,
                            )
                    except TimeoutError:
                        await reject(
                            503,
                            "identity_provider_unavailable",
                            "Google identity verification is temporarily unavailable.",
                        )
                        return
                    except IdentityProviderUnavailable:
                        await reject(
                            503,
                            "identity_provider_unavailable",
                            "Google identity verification is temporarily unavailable.",
                        )
                        return
                    except InvalidIdentityToken:
                        await reject(
                            401,
                            "invalid_ai_identity",
                            "The AI user identity could not be verified.",
                        )
                        return
                    if ai_principal.owner_id != active_session.principal.owner_id:
                        await reject(
                            403,
                            "ai_identity_mismatch",
                            "The AI identity does not match the signed-in travel owner.",
                        )
                        return
                    scope["ai_user_id_token"] = user_token
                    scope["personal_ai_auth_context"] = PersonalAIAuthContext(
                        user_id_token=user_token,
                        service_audience=settings.personal_ai_service_iam_audience,
                        service_account=settings.personal_ai_service_account,
                    )

            upload = method == "POST" and UPLOAD_PATH.fullmatch(path) is not None
            if upload:
                if (
                    not settings.private_imports_enabled
                    or settings.travel_auth_mode != "google_oidc"
                ):
                    await reject(
                        404, "private_imports_disabled", "Private imports are unavailable."
                    )
                    return
                media_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                if media_type not in {"text/plain", "application/pdf"}:
                    await reject(415, "unsupported_media_type", "Use plain text or PDF.")
                    return
                limit = 1024 * 1024 if media_type == "text/plain" else MAX_UPLOAD_BYTES
                content_length = headers.get("content-length")
                if content_length is not None:
                    try:
                        if int(content_length) > limit or int(content_length) < 0:
                            await reject(
                                413, "request_too_large", "The upload exceeds its size limit."
                            )
                            return
                    except ValueError:
                        await reject(400, "invalid_length", "Invalid Content-Length.")
                        return
                count = 0
                deadline = monotonic() + UPLOAD_SECONDS

                async def bounded_receive() -> Message:
                    nonlocal count
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise UploadDeadlineExceeded
                    try:
                        async with asyncio.timeout(remaining):
                            part = await receive()
                    except TimeoutError as exc:
                        raise UploadDeadlineExceeded from exc
                    if part["type"] == "http.request":
                        count += len(part.get("body", b""))
                        if count > limit:
                            raise UploadTooLarge
                    return part

                scope["request_id"] = request_id
                try:
                    await self.app(scope, bounded_receive, send_response)
                except UploadTooLarge:
                    if not response_started:
                        await reject(413, "request_too_large", "The upload exceeds its size limit.")
                except UploadDeadlineExceeded:
                    if not response_started:
                        await reject(408, "upload_timeout", "The upload took too long.")
                return

            # Read at most one bounded JSON request before dispatch, regardless of
            # Content-Length. This also handles chunked requests without allocating
            # an unbounded body in FastAPI's JSON parser.
            body = bytearray()
            extraction_deadline = None
            if method == "POST" and EXTRACTION_PATH.fullmatch(path):
                extraction_deadline = (
                    asyncio.get_running_loop().time()
                    + settings.personal_ai_extraction_timeout_seconds
                )
                scope["booking_extraction_deadline"] = extraction_deadline
            while True:
                if extraction_deadline is None:
                    message = await receive()
                else:
                    remaining = extraction_deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        await reject(
                            408,
                            "extraction_timeout",
                            "Booking extraction exceeded its total time limit.",
                        )
                        return
                    try:
                        async with asyncio.timeout(remaining):
                            message = await receive()
                    except TimeoutError:
                        await reject(
                            408,
                            "extraction_timeout",
                            "Booking extraction exceeded its total time limit.",
                        )
                        return
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > MAX_REQUEST_BYTES:
                    await reject(413, "request_too_large", "The request body exceeds 64 KiB.")
                    return
                if not message.get("more_body", False):
                    break
            delivered = False

            async def replay() -> Message:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            scope["request_id"] = request_id
            await self.app(scope, replay, send_response)
        except Exception as exc:
            # Starlette's outer server-error handler re-raises unexpected errors
            # to the server, whose traceback can expose SQL parameters/secrets.
            # Handle them here before headers; sanitize any late response failure.
            logger.error("request id=%s error_type=%s", request_id, type(exc).__name__)
            if response_started:
                raise RuntimeError("Travel response failed after headers were sent.") from None
            await reject(
                500, "internal_error", "The travel service could not complete the request."
            )
        finally:
            # Route templates exclude IDs, query strings, user text and secrets.
            route = getattr(scope.get("route"), "path", "unmatched")
            logger.info(
                "request id=%s method=%s route=%s status=%s duration_ms=%.1f",
                request_id,
                scope["method"],
                route,
                status,
                (monotonic() - started) * 1000,
            )
