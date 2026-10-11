"""Read-only Travel context API and thin host bridge to shared Personal AI chat."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from personal_travel.api.dependencies import OwnerDependency, SessionDependency
from personal_travel.api.schemas.personal_ai_context import (
    ContextEntityReference,
    ContextFieldSensitivity,
    ContextPermissionDependency,
    ContextReadRecord,
    ContextReadRequest,
    ContextReadResponse,
    ContextSourceReference,
    SidecarConversationResponse,
    SidecarCreateRequest,
    SidecarHistoryResponse,
    SidecarPreviewResponse,
    SidecarTurnRequest,
    SidecarTurnResponse,
)
from personal_travel.auth.contracts import PersonalAIAuthContext
from personal_travel.clients.personal_ai import (
    PersonalAIClient,
    PersonalAISidecarError,
    PersonalAISidecarUnknown,
)
from personal_travel.config import Settings, get_settings
from personal_travel.domain.upstream_personal_ai_context import PersonalAISidecarConversation
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.place import Place
from personal_travel.models.reservation import Reservation
from personal_travel.models.trip import Trip
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.trips import TripService

router = APIRouter(prefix="/v1", tags=["Personal AI context"])

ContextPayloadValue = str | int | float | bool | None
ContextPayload = dict[str, ContextPayloadValue]

TRIP_FIELDS = frozenset(
    {
        "trip_title",
        "destination",
        "start_date",
        "end_date",
        "timezone",
        "itinerary_overview",
        "trip_revision",
    }
)
ENTITY_FIELDS = TRIP_FIELDS | frozenset(
    {
        "day_date",
        "day_title",
        "item_title",
        "item_type",
        "item_status",
        "item_schedule",
        "place_name",
        "place_category",
        "place_address",
        "place_source",
        "place_attribution",
        "reservation_summary",
    }
)
RESEARCH_FIELDS = frozenset({"passage", "observed_at", "expires_at", "citations"})


def _opaque(kind: str, identifier: str) -> str:
    return hashlib.sha256(f"{kind}\0{identifier}".encode()).hexdigest()


def _fingerprint(payload: ContextPayload) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _project(source: Mapping[str, ContextPayloadValue], fields: tuple[str, ...]) -> ContextPayload:
    return {key: value for key, value in source.items() if key in fields and value is not None}


def _trip_permission(trip: Trip) -> ContextPermissionDependency:
    return ContextPermissionDependency(
        permission_id=f"travel_context_read:{trip.id}",
        version=str(trip.revision),
        purpose="confirm current owner access and Travel source revision before disclosure",
    )


def _record(
    trip: Trip,
    *,
    entity_type: str,
    entity_id: UUID | str,
    payload: ContextPayload,
    authority: Literal[
        "authoritative", "user_asserted", "derived", "external", "client_supplied", "unknown"
    ] = "authoritative",
    observed_at: datetime | None = None,
    sensitivity: Literal["public", "personal", "sensitive", "restricted", "unknown"] = "sensitive",
    extra_refs: tuple[ContextSourceReference, ...] = (),
) -> ContextReadRecord:
    identifier = str(entity_id)
    opaque = _opaque(entity_type, identifier)
    fingerprint = _fingerprint(payload)
    return ContextReadRecord(
        entity_type=entity_type,
        entity_id=identifier,
        source_id=f"travel:{opaque}",
        source_version=str(trip.revision),
        authority=authority,
        observed_at=(observed_at or trip.updated_at).astimezone(UTC).isoformat(),
        sensitivity=sensitivity,
        payload=payload,
        source_refs=(
            ContextSourceReference(
                kind="record",
                reference_id=f"travel-record:{opaque}",
                fingerprint=fingerprint,
            ),
            *extra_refs,
        ),
        permission_dependencies=(_trip_permission(trip),),
        field_sensitivity=tuple(
            ContextFieldSensitivity(field=name, sensitivity=sensitivity)
            for name, value in payload.items()
            if value is not None
        ),
    )


def _trip_overview(trip: Trip) -> str:
    values = []
    for day in trip.days:
        for item in day.items:
            schedule = f" at {item.starts_at.isoformat()}" if item.starts_at else ""
            values.append(f"{day.date.isoformat()}: {item.title} ({item.status}){schedule}"[:240])
            if len(values) >= 12:
                return " | ".join(values)[:1_800]
    return " | ".join(values)[:1_800]


def _schedule(item: ItineraryItem) -> str | None:
    if item.starts_at is None and item.ends_at is None:
        return None
    start = item.starts_at.isoformat() if item.starts_at else "unscheduled"
    end = item.ends_at.isoformat() if item.ends_at else None
    return f"{start}–{end}" if end else start


def _place_record(trip: Trip, place: Place, fields: tuple[str, ...]) -> ContextReadRecord:
    payload = _project(
        {
            "place_name": place.name,
            "place_category": place.category,
            "place_address": place.address,
            "place_source": place.provider_source_name,
            "place_attribution": place.provider_source_attribution,
            "trip_revision": trip.revision,
        },
        fields,
    )
    refs: tuple[ContextSourceReference, ...] = ()
    authority: Literal["user_asserted", "external"] = "user_asserted"
    if place.provider_source_url:
        from personal_travel.domain.urls import validate_http_url

        try:
            validate_http_url(place.provider_source_url)
        except ValueError:
            pass
        else:
            refs = (
                ContextSourceReference(
                    kind="url",
                    reference_id=f"place-source:{_opaque('url', place.provider_source_url)}",
                    uri=place.provider_source_url,
                    fingerprint=_fingerprint(payload),
                ),
            )
            authority = "external"
    return _record(
        trip,
        entity_type="place",
        entity_id=place.id,
        payload=payload,
        authority=authority,
        observed_at=place.updated_at,
        extra_refs=refs,
    )


def _reservation_summary(reservation: Reservation) -> str:
    schedule = [
        value.isoformat()
        for value in (reservation.starts_at, reservation.ends_at)
        if value is not None
    ]
    parts = [
        reservation.reservation_type.replace("_", " "),
        reservation.provider_name,
        reservation.status,
    ]
    if schedule:
        parts.append(" – ".join(schedule))
    if reservation.place is not None:
        parts.append(f"place {reservation.place.name}")
    return " · ".join(parts)[:500]


def _selected_trip(payload: ContextReadRequest) -> UUID:
    refs = [ref for ref in payload.entity_refs if ref.entity_type == "trip"]
    if len(refs) != 1:
        raise HTTPException(status_code=422, detail="context_trip_reference_required")
    try:
        return UUID(refs[0].entity_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="context_entity_reference_invalid") from error


def _context_records(trip: Trip, request: ContextReadRequest) -> tuple[ContextReadRecord, ...]:
    fields = tuple(request.fields)
    if len(fields) != len(set(fields)):
        raise HTTPException(status_code=422, detail="context_fields_invalid")
    allowed = {
        "current": TRIP_FIELDS,
        "entity": ENTITY_FIELDS,
        "verify": frozenset({"trip_revision"}),
    }.get(request.operation)
    if allowed is None or not set(fields).issubset(allowed):
        raise HTTPException(status_code=422, detail="context_fields_not_allowed")
    trip_source = {
        "trip_title": trip.title,
        "destination": None,
        "start_date": trip.start_date.isoformat(),
        "end_date": trip.end_date.isoformat(),
        "timezone": trip.timezone,
        "itinerary_overview": _trip_overview(trip),
        "trip_revision": trip.revision,
    }
    if request.operation in {"current", "verify"}:
        return (
            _record(
                trip,
                entity_type="trip",
                entity_id=trip.id,
                payload=_project(
                    {"trip_revision": trip.revision}
                    if request.operation == "verify"
                    else trip_source,
                    fields,
                ),
            ),
        )

    selected = {
        (ref.entity_type, ref.entity_id)
        for ref in request.entity_refs
        if ref.entity_type != "research_session"
    }
    found = {("trip", str(trip.id))}
    records: list[ContextReadRecord] = []
    if ("trip", str(trip.id)) in selected:
        payload = _project(trip_source, fields)
        if payload:
            records.append(_record(trip, entity_type="trip", entity_id=trip.id, payload=payload))

    places: dict[str, Place] = {}
    for day in trip.days:
        for item in day.items:
            if item.place is not None:
                places[str(item.place.id)] = item.place
        day_key = ("day", str(day.id))
        if day_key in selected:
            found.add(day_key)
            records.append(
                _record(
                    trip,
                    entity_type="day",
                    entity_id=day.id,
                    payload=_project(
                        {
                            "day_date": day.date.isoformat(),
                            "day_title": day.title,
                            "trip_revision": trip.revision,
                        },
                        fields,
                    ),
                    observed_at=day.updated_at,
                )
            )
        for item in day.items:
            item_key = ("itinerary_item", str(item.id))
            if item_key not in selected:
                continue
            found.add(item_key)
            place = item.place
            source = {
                "day_date": day.date.isoformat(),
                "day_title": day.title,
                "item_title": item.title,
                "item_type": item.item_type,
                "item_status": item.status,
                "item_schedule": _schedule(item),
                "trip_revision": trip.revision,
            }
            records.append(
                _record(
                    trip,
                    entity_type="itinerary_item",
                    entity_id=item.id,
                    payload=_project(source, fields),
                    observed_at=item.updated_at,
                )
            )
    for saved in trip.saved_places:
        if saved.place is not None:
            places[str(saved.place.id)] = saved.place
    for reservation in trip.reservations:
        if reservation.place is not None:
            places[str(reservation.place.id)] = reservation.place
    for entity_type, identifier in selected:
        if entity_type != "place":
            continue
        place = places.get(identifier)
        if place is None:
            continue
        found.add((entity_type, identifier))
        records.append(_place_record(trip, place, fields))

    include_reservations = "reservation_summary" in fields and (
        ("trip", str(trip.id)) in selected
        or any(
            entity_type in {"day", "itinerary_item", "place", "reservation"}
            for entity_type, _ in selected
        )
    )
    for reservation in trip.reservations:
        key = ("reservation", str(reservation.id))
        selected_reservation = key in selected
        linked_to_selected = (
            (reservation.place_id is not None and ("place", str(reservation.place_id)) in selected)
            or any(
                ("day", str(day.id)) in selected
                and any(item.reservation_id == reservation.id for item in day.items)
                for day in trip.days
            )
            or any(
                ("itinerary_item", str(item.id)) in selected
                and item.reservation_id == reservation.id
                for day in trip.days
                for item in day.items
            )
        )
        if selected_reservation:
            found.add(key)
        if (
            selected_reservation
            or (include_reservations and linked_to_selected)
            or (include_reservations and ("trip", str(trip.id)) in selected)
        ):
            records.append(
                _record(
                    trip,
                    entity_type="reservation",
                    entity_id=reservation.id,
                    payload=_project(
                        {
                            "reservation_summary": _reservation_summary(reservation),
                            "trip_revision": trip.revision,
                        },
                        fields,
                    ),
                    observed_at=reservation.updated_at,
                    sensitivity="sensitive",
                )
            )
    if selected - found:
        raise not_found("trip context entity")
    return tuple(records[: request.max_results])


def _context_client(request: Request, *, timeout_seconds: float | None = None) -> PersonalAIClient:
    auth: PersonalAIAuthContext | None = request.scope.get("personal_ai_auth_context")
    return PersonalAIClient(auth_context=auth, timeout_seconds=timeout_seconds)


def _require_enabled(settings: Settings) -> None:
    if not settings.personal_ai_context_enabled:
        raise HTTPException(status_code=404, detail="not_found")


def _launch_context(
    trip: Trip, conversation_id: UUID, turn: SidecarTurnRequest
) -> dict[str, object]:
    trip_ref = ContextEntityReference(
        entity_type="trip", entity_id=str(trip.id), application_id="travel", workspace_id=None
    )
    allowed = {("trip", str(trip.id))}
    allowed.update(("day", str(day.id)) for day in trip.days)
    allowed.update(("itinerary_item", str(item.id)) for day in trip.days for item in day.items)
    allowed.update(
        ("place", str(item.place_id))
        for day in trip.days
        for item in day.items
        if item.place_id is not None
    )
    allowed.update(("place", str(item.place_id)) for item in trip.saved_places)
    allowed.update(
        ("place", str(item.place_id)) for item in trip.reservations if item.place_id is not None
    )
    allowed.update(("reservation", str(item.id)) for item in trip.reservations)
    refs = [trip_ref]
    for ref in turn.entity_refs:
        key = (ref.entity_type, ref.entity_id)
        if ref.entity_type == "research_session":
            try:
                UUID(ref.entity_id)
            except ValueError as error:
                raise HTTPException(
                    status_code=422, detail="context_entity_reference_invalid"
                ) from error
            refs.append(ref)
        elif key not in allowed:
            raise not_found("trip context entity")
        elif key != ("trip", str(trip.id)):
            refs.append(ref)
    return {
        "schema_version": "application-launch-v1",
        "application_id": "travel",
        "workspace_id": None,
        "entity_refs": [item.model_dump(mode="json") for item in dict.fromkeys(refs)],
        "view_id": turn.view_id,
        "conversation_id": str(conversation_id),
        "client_context": {},
    }


def _turn_payload(trip: Trip, conversation_id: UUID, turn: SidecarTurnRequest) -> dict[str, object]:
    return {
        "content": turn.content,
        "idempotency_key": str(turn.idempotency_key),
        "choice": {"mode": "automatic"},
        "launch_context": _launch_context(trip, conversation_id, turn),
        "excluded_source_classes": list(turn.excluded_source_classes),
    }


@router.post("/integrations/personal-ai/context", response_model=ContextReadResponse)
async def read_personal_ai_context(
    payload: ContextReadRequest,
    request: Request,
    session: SessionDependency,
    owner_id: OwnerDependency,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ContextReadResponse:
    _require_enabled(settings)
    if payload.operation == "search":
        if not payload.entity_refs or any(
            ref.entity_type != "research_session" for ref in payload.entity_refs
        ):
            raise HTTPException(status_code=422, detail="research_session_reference_required")
        if not set(payload.fields).issubset(RESEARCH_FIELDS):
            raise HTTPException(status_code=422, detail="context_fields_not_allowed")
        client = _context_client(request, timeout_seconds=3.5)
        now = datetime.now(UTC)
        records = []
        selected = payload.entity_refs[: min(payload.max_results, 3)]
        try:
            async with asyncio.timeout(4):
                research_results = await asyncio.gather(
                    *(client.get_research_context(UUID(ref.entity_id)) for ref in selected)
                )
        except (PersonalAISidecarError, TimeoutError) as error:
            raise HTTPException(status_code=503, detail="selected_research_unavailable") from error
        for result in research_results:
            if (
                result.state != "completed"
                or not result.answer
                or not result.citations
                or result.expires_at <= now
                or any(citation.expires_at <= now for citation in result.citations)
            ):
                continue
            expiry = min(result.expires_at, *(item.expires_at for item in result.citations))
            observed = min(item.observed_at for item in result.citations)
            citations = [
                {
                    "title": item.title,
                    "url": item.url,
                    "observed_at": item.observed_at.isoformat(),
                    "expires_at": item.expires_at.isoformat(),
                }
                for item in result.citations[:12]
            ]
            refs = tuple(
                ContextSourceReference(
                    kind="url",
                    reference_id=f"research-citation:{_opaque('url', item.url)}",
                    uri=item.url,
                    fingerprint=_fingerprint(
                        {
                            "title": item.title,
                            "observed_at": item.observed_at.isoformat(),
                            "expires_at": item.expires_at.isoformat(),
                        }
                    ),
                )
                for item in result.citations[:12]
            )
            records.append(
                ContextReadRecord(
                    entity_type="research_session",
                    entity_id=str(result.id),
                    source_id=f"travel-research:{_opaque('research_session', str(result.id))}",
                    source_version=f"research-v1:{result.revision}",
                    authority="external",
                    observed_at=observed.isoformat(),
                    expires_at=expiry.isoformat(),
                    sensitivity="public",
                    payload=_project(
                        {
                            "passage": result.answer[:8_000],
                            "observed_at": observed.isoformat(),
                            "expires_at": expiry.isoformat(),
                            "citations": json.dumps(
                                citations, ensure_ascii=False, separators=(",", ":")
                            )[:8_000],
                        },
                        payload.fields,
                    ),
                    source_refs=refs,
                    field_sensitivity=tuple(
                        ContextFieldSensitivity(field=name, sensitivity="public")
                        for name in payload.fields
                    ),
                )
            )
        response = ContextReadResponse(items=tuple(records))
    else:
        if payload.workspace_id is not None:
            raise HTTPException(status_code=400, detail="context_workspace_unsupported")
        trip_id = _selected_trip(payload)
        trip = TripService(session, owner_id).get(trip_id)
        response = ContextReadResponse(items=_context_records(trip, payload))
    if len(response.items) > payload.max_results:
        raise HTTPException(status_code=413, detail="context_result_limit_exceeded")
    if len(response.model_dump_json().encode("utf-8")) > payload.max_bytes:
        raise HTTPException(status_code=413, detail="context_response_too_large")
    return response


@router.post(
    "/trips/{trip_id}/assistant/conversations",
    response_model=SidecarConversationResponse,
    status_code=201,
)
async def create_sidecar_conversation(
    trip_id: UUID,
    payload: SidecarCreateRequest,
    request: Request,
    session: SessionDependency,
    owner_id: OwnerDependency,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SidecarConversationResponse:
    _require_enabled(settings)
    TripService(session, owner_id).get(trip_id)
    conversation_id = await _context_client(request).create_sidecar_conversation(
        title=payload.title, idempotency_key=payload.idempotency_key
    )
    return PersonalAISidecarConversation(id=conversation_id, application_id="travel")


@router.get(
    "/trips/{trip_id}/assistant/conversations/{conversation_id}",
    response_model=SidecarHistoryResponse,
)
async def get_sidecar_conversation(
    trip_id: UUID,
    conversation_id: UUID,
    request: Request,
    session: SessionDependency,
    owner_id: OwnerDependency,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SidecarHistoryResponse:
    _require_enabled(settings)
    TripService(session, owner_id).get(trip_id)
    try:
        return await _context_client(request).get_sidecar_conversation(conversation_id)
    except PersonalAISidecarError as error:
        raise DomainError(
            "personal_ai_unavailable",
            "The assistant conversation could not be loaded.",
            status_code=503,
        ) from error


@router.post(
    "/trips/{trip_id}/assistant/conversations/{conversation_id}/preview",
    response_model=SidecarPreviewResponse,
)
async def preview_sidecar_context(
    trip_id: UUID,
    conversation_id: UUID,
    turn: SidecarTurnRequest,
    request: Request,
    session: SessionDependency,
    owner_id: OwnerDependency,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SidecarPreviewResponse:
    _require_enabled(settings)
    trip = TripService(session, owner_id).get(trip_id)
    return await _context_client(request).preview_sidecar_context(
        conversation_id=conversation_id,
        payload=_turn_payload(trip, conversation_id, turn),
    )


@router.post(
    "/trips/{trip_id}/assistant/conversations/{conversation_id}/continue",
    response_model=SidecarTurnResponse,
)
async def continue_sidecar_turn(
    trip_id: UUID,
    conversation_id: UUID,
    turn: SidecarTurnRequest,
    request: Request,
    session: SessionDependency,
    owner_id: OwnerDependency,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SidecarTurnResponse:
    _require_enabled(settings)
    trip = TripService(session, owner_id).get(trip_id)
    try:
        message_id, answer = await _context_client(request).continue_sidecar_turn(
            conversation_id=conversation_id,
            payload=_turn_payload(trip, conversation_id, turn),
        )
    except PersonalAISidecarUnknown as error:
        raise DomainError(
            "personal_ai_turn_unknown",
            "The assistant turn outcome is unknown. Retry with the same request key.",
            status_code=503,
        ) from error
    except PersonalAISidecarError as error:
        raise DomainError(
            "personal_ai_unavailable",
            "The assistant could not complete this request.",
            status_code=503,
        ) from error
    return SidecarTurnResponse(message_id=message_id, answer=answer)
