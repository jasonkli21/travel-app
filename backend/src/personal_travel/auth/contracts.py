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
