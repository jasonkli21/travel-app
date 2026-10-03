"""A bounded local browser boundary; this is not user authentication."""

import logging
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from personal_travel.api.errors import error_response

logger = logging.getLogger("personal_travel.requests")
MAX_REQUEST_BYTES = 64 * 1024


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

            # Read at most one bounded JSON request before dispatch, regardless of
            # Content-Length. This also handles chunked requests without allocating
            # an unbounded body in FastAPI's JSON parser.
            body = bytearray()
            while True:
                message = await receive()
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
