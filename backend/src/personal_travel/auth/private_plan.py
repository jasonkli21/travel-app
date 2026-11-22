"""Independent service and human authentication for the isolated reverse hop."""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from starlette.datastructures import Headers

from personal_travel.api.schemas.private_plan import SHA256_PATTERN, PrivatePlanSourceRequest
from personal_travel.clients.task_wire import (
    VerifiedDelegatedUser,
    verify_delegated_user,
)


class PrivatePlanServiceVerifier(Protocol):
    def __call__(self, token: str, *, audience: str, expected_service_account: str) -> None: ...


@dataclass(frozen=True, slots=True, repr=False)
class PrivatePlanTrust:
    """One separately registered PAS target and Travel client on the isolated host."""

    target_id: str
    client_namespace: str
    service_audience: str
    service_account: str
    delegation_issuer: str
    delegation_key_id: str
    delegation_public_key: Ed25519PublicKey
    human_issuer: str = "https://accounts.google.com"

    def __post_init__(self) -> None:
        identifiers = (
            self.service_audience,
            self.service_account,
            self.delegation_issuer,
            self.delegation_key_id,
            self.human_issuer,
        )
        if (
            any(
                not value or value.strip() != value or not value.isprintable()
                for value in identifiers
            )
            or not 1 <= len(self.target_id) <= 200
            or not all(character.isprintable() for character in self.target_id)
            or self.target_id.strip() != self.target_id
            or re.fullmatch(SHA256_PATTERN, self.client_namespace) is None
        ):
            raise ValueError("private_plan_registration_invalid")


def authenticate_private_plan_source(
    headers: Headers,
    body: dict[str, Any],
    payload: PrivatePlanSourceRequest,
    trust: PrivatePlanTrust,
    verify_service: PrivatePlanServiceVerifier,
    *,
    clock: Callable[[], float] = time.time,
) -> VerifiedDelegatedUser:
    authorizations = headers.getlist("authorization")
    assertions = headers.getlist("x-user-delegation")
    if (
        len(authorizations) != 1
        or not authorizations[0].startswith("Bearer ")
        or len(assertions) != 1
        or any(name in headers for name in ("cookie", "origin", "x-user-id-token", "x-owner-id"))
        or (payload.target_id, payload.client_namespace)
        != (trust.target_id, trust.client_namespace)
    ):
        raise ValueError("private_plan_source_denied")

    token = authorizations[0][len("Bearer ") :]
    if not token or len(token) > 8192 or any(char.isspace() for char in token):
        raise ValueError("private_plan_source_denied")
    verify_service(
        token, audience=trust.service_audience, expected_service_account=trust.service_account
    )
    return verify_delegated_user(
        assertions[0],
        body,
        public_key=trust.delegation_public_key,
        issuer=trust.delegation_issuer,
        audience=trust.service_audience,
        key_id=trust.delegation_key_id,
        now=int(clock()),
        allowed_user_issuers=frozenset({trust.human_issuer}),
    )
