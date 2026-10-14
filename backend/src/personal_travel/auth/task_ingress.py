"""Finite signed ingress: independent IAM identity plus an existing active Google owner."""

import asyncio
import base64
import json
import time
from functools import partial
from typing import Any

from anyio.to_thread import run_sync
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException
from sqlalchemy import select
from starlette.datastructures import Headers
from starlette.types import Message, Receive

from personal_travel.auth.contracts import VerifiedPrincipal
from personal_travel.auth.google_oidc import verify_google_service_token
from personal_travel.clients.task_wire import verify_delegated_user
from personal_travel.config import Settings
from personal_travel.models.auth import AuthIdentity


async def authenticate_task_ingress(
    headers: Headers,
    receive: Receive,
    settings: Settings,
    session_factory: Any,
    state: Any,
    *,
    command_read: bool,
) -> tuple[VerifiedPrincipal, Receive]:
    task = settings.task_control
    if task is None:
        raise HTTPException(404, "not_found")
    data = bytearray()
    async with asyncio.timeout(3):
        while True:
            message = await receive()
            if message["type"] != "http.request":
                raise HTTPException(400, "task_request_invalid")
            data.extend(message.get("body", b""))
            if len(data) > 8192:
                raise HTTPException(413, "task_request_too_large")
            if not message.get("more_body", False):
                break
    try:
        body = json.loads(data)
        if not isinstance(body, dict) or body.get("workspace_id") is not None:
            raise ValueError
        if not command_read and (
            body.get("purpose") != "shopping_trip_context"
            or body.get("destination_application_id") != "shopping"
        ):
            raise ValueError
        auth, assertions = headers.getlist("authorization"), headers.getlist("x-user-delegation")
        if len(auth) != 1 or len(assertions) != 1 or not auth[0].startswith("Bearer "):
            raise ValueError
        verifier = getattr(state, "task_service_verifier", None) or verify_google_service_token
        async with asyncio.timeout(3):
            await run_sync(
                partial(
                    verifier,
                    auth[0][7:],
                    audience=settings.personal_ai_context_service_iam_audience,
                    expected_service_account=settings.personal_ai_context_service_account,
                ),
                abandon_on_cancel=True,
            )
        identity = verify_delegated_user(
            assertions[0],
            body,
            public_key=Ed25519PublicKey.from_public_bytes(
                base64.b64decode(
                    task.pai_public_key + "=" * (-len(task.pai_public_key) % 4),
                    altchars=b"-_",
                    validate=True,
                )
            ),
            issuer=task.pai_issuer,
            audience=settings.personal_ai_context_service_iam_audience,
            key_id=task.pai_key_id,
            now=int(time.time()),
            allowed_user_issuers=frozenset({"https://accounts.google.com"}),
        )

        def lookup() -> VerifiedPrincipal:
            with session_factory() as session:
                row = session.scalar(
                    select(AuthIdentity).where(
                        AuthIdentity.issuer == identity.issuer,
                        AuthIdentity.subject == identity.subject,
                        AuthIdentity.status == "active",
                    )
                )
                if row is None:
                    raise ValueError
                now = int(time.time())
                return VerifiedPrincipal(
                    issuer=identity.issuer,
                    subject=identity.subject,
                    owner_id=row.owner_id,
                    email=row.email,
                    issued_at=now,
                    expires_at=now + 60,
                    authenticated_at=identity.authenticated_at,
                )

        principal = await run_sync(lookup)
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(401, "task_identity_denied") from error
    delivered = False

    async def replay() -> Message:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": bytes(data), "more_body": False}
        return await receive()

    return principal, replay
