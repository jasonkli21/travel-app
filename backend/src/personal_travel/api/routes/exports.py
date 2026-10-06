"""Explicit trip snapshot downloads in calendar, static HTML, and JSON formats."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, Response

from personal_travel.api.dependencies import OwnerDependency
from personal_travel.api.schemas.attachments import TripExportRequest
from personal_travel.config import get_settings
from personal_travel.db.session import SessionFactory
from personal_travel.services.errors import not_found
from personal_travel.services.source_store import LocalSourceStore
from personal_travel.services.trip_exports import build_export, render_export

router = APIRouter(prefix="/trips", tags=["exports"])


@router.post("/{trip_id}/exports")
def create_trip_export(
    trip_id: UUID,
    payload: TripExportRequest,
    request: Request,
    owner_id: OwnerDependency,
) -> Response:
    settings = get_settings()
    store = None
    if payload.include_documents:
        if not settings.private_attachments_enabled or settings.travel_auth_mode != "google_oidc":
            raise not_found("trip document export")
        store = LocalSourceStore(settings.private_source_dir)
    try:
        factory = getattr(request.app.state, "auth_session_factory", None) or SessionFactory
        projection = build_export(
            factory,
            owner_id=owner_id,
            trip_id=trip_id,
            options=payload,
        )
        artifact = render_export(
            projection,
            export_format=payload.format,
            include_documents=payload.include_documents,
            include_linked_reservations=payload.include_linked_reservations,
            store=store,
        )
        return Response(
            artifact.data,
            media_type=artifact.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{artifact.filename}"',
                "X-Trip-Revision": str(projection.data["metadata"]["trip_revision"]),
                "X-Trip-Generated-At": str(projection.data["metadata"]["generated_at"]),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    finally:
        if store is not None:
            store.close()
