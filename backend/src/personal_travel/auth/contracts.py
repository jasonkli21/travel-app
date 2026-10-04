from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VerifiedPrincipal:
    issuer: str
    subject: str
    owner_id: str
    email: str
    issued_at: int
    expires_at: int
    nonce: str | None = None


@dataclass(frozen=True, slots=True)
class PersonalAIAuthContext:
    """Separate verified Google user identity from Cloud Run transport identity."""

    user_id_token: str
    service_audience: str
    service_account: str
