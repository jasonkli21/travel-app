"""Opt-in reverse hop for the isolated Travel private-plan preview."""

import asyncio
import json
from collections.abc import Callable
from functools import partial
from typing import Any

from anyio.to_thread import run_sync
from fastapi import FastAPI, HTTPException, Request, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from personal_travel.api.routes.personal_ai_context import _context_records
from personal_travel.api.schemas.personal_ai_context import (
    ContextEntityReference,
    ContextReadRequest,
    ContextReadResponse,
)
from personal_travel.api.schemas.private_plan import (
    PRIVATE_PLAN_SOURCE_MAX_BYTES,
    PRIVATE_PLAN_SOURCE_PATH,
    PrivatePlanSourceRequest,
)
from personal_travel.auth.google_oidc import IdentityProviderUnavailable
from personal_travel.auth.private_plan import (
    PrivatePlanServiceVerifier,
    PrivatePlanTrust,
    authenticate_private_plan_source,
)
from personal_travel.config import Settings
from personal_travel.services.errors import DomainError
from personal_travel.services.private_plan import active_plan_identity, resolve_plan_trip

MAX_REQUEST_BYTES = 2048
PRIVATE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate property")
    return value


def mount_private_plan_source(
    app: FastAPI,
    *,
    settings: Settings,
    trust: PrivatePlanTrust,
    sessions: Callable[[], Session],
    verify_service: PrivatePlanServiceVerifier,
) -> None:
    """Mount only on the isolated preview app with explicitly supplied source trust."""
    if not settings.personal_ai_plan_enabled:
        return
    if settings.deployment_mode != "local" or app.middleware_stack is not None:
        raise ValueError("private_plan_source_requires_isolated_host")

    def project_source(request: Request, body: dict[str, Any]) -> str:
        payload = PrivatePlanSourceRequest.model_validate(body)
        delegation = authenticate_private_plan_source(
            request.headers, body, payload, trust, verify_service
        )
        with sessions() as session:
            identity = active_plan_identity(
                session, issuer=delegation.issuer, subject=delegation.subject
            )
            _, trip = resolve_plan_trip(
                session,
                identity=identity,
                trust=trust,
                conversation_id=payload.conversation_id,
            )
            if payload.operation == "verify" and payload.expected_source_version != str(
                trip.revision
            ):
                raise DomainError(
                    "private_plan_source_stale", "Private plan source changed.", status_code=409
                )
            v2_request = ContextReadRequest(
                operation=payload.operation,
                workspace_id=None,
                purpose="personal_ai_context",
                entity_refs=(
                    ContextEntityReference(
                        entity_type="trip", entity_id=str(trip.id), application_id="travel"
                    ),
                ),
                fields=(
                    ("trip_title", "trip_revision")
                    if payload.operation == "current"
                    else ("trip_revision",)
                ),
                max_results=1,
                max_bytes=PRIVATE_PLAN_SOURCE_MAX_BYTES,
            )
            records = _context_records(trip, v2_request)
            projection = ContextReadResponse(items=records).model_dump_json()
            if len(records) != 1 or len(projection.encode("utf-8")) > v2_request.max_bytes:
                raise DomainError(
                    "private_plan_source_unavailable",
                    "Private plan source unavailable.",
                    status_code=503,
                )
            return projection

    async def read_private_plan_source(request: Request) -> Response:
        media = request.headers.getlist("content-type")
        if (
            len(media) != 1
            or media[0].split(";", 1)[0].strip().lower() != "application/json"
            or request.url.query
        ):
            raise HTTPException(400, "private_plan_source_denied", headers=PRIVATE_HEADERS)
        try:
            async with asyncio.timeout(3):
                data = bytearray()
                async for chunk in request.stream():
                    if len(data) + len(chunk) > MAX_REQUEST_BYTES:
                        raise HTTPException(
                            413, "private_plan_source_denied", headers=PRIVATE_HEADERS
                        )
                    data.extend(chunk)
                body = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
                projection = await run_sync(
                    partial(project_source, request, body), abandon_on_cancel=True
                )
        except (ValueError, RecursionError, ClientDisconnect):
            raise HTTPException(
                401, "private_plan_source_denied", headers=PRIVATE_HEADERS
            ) from None
        except (TimeoutError, IdentityProviderUnavailable, SQLAlchemyError):
            raise HTTPException(
                503, "private_plan_source_unavailable", headers=PRIVATE_HEADERS
            ) from None
        except DomainError as exc:
            raise HTTPException(exc.status_code, exc.code, headers=PRIVATE_HEADERS) from None
        return Response(projection, media_type="application/json", headers=PRIVATE_HEADERS)

    app.add_api_route(
        PRIVATE_PLAN_SOURCE_PATH,
        read_private_plan_source,
        methods=["POST"],
        include_in_schema=False,
    )
