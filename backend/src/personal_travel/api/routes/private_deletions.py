"""Authenticated bounded retry for upstream private-source deletion intents."""

from __future__ import annotations

from fastapi import APIRouter, Request

from personal_travel.api.dependencies import OwnerDependency
from personal_travel.auth.contracts import PersonalAIAuthContext
from personal_travel.clients.personal_ai import PersonalAIClient
from personal_travel.config import get_settings
from personal_travel.db.session import SessionFactory
from personal_travel.services.errors import DomainError
from personal_travel.services.private_deletion import PrivateDeletionService

router = APIRouter(prefix="/private-import-deletion-intents", tags=["private import cleanup"])


@router.post("/retry")
async def retry_private_deletions(request: Request, owner_id: OwnerDependency) -> dict[str, int]:
    settings = get_settings()
    if not settings.private_imports_enabled or settings.travel_auth_mode != "google_oidc":
        raise DomainError(
            "private_cleanup_unavailable", "Private-source cleanup is unavailable.", status_code=404
        )
    auth_context: PersonalAIAuthContext | None = request.scope.get("personal_ai_auth_context")
    if auth_context is None:
        raise DomainError(
            "private_cleanup_auth_unavailable",
            "Verified AI service credentials are required to retry private-source cleanup.",
            status_code=503,
        )
    factory = getattr(request.app.state, "auth_session_factory", None) or SessionFactory
    client = PersonalAIClient(
        timeout_seconds=settings.personal_ai_extraction_timeout_seconds,
        auth_context=auth_context,
    )
    return await PrivateDeletionService(factory, client).retry_pending(owner_id)
