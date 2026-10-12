"""Versioned typed boundary for Personal AI context and sidecar requests.

The wire contract deliberately contains no Travel ORM models and no writable
Travel operations. Browser supplied identifiers remain selectors; owner access
comes from the independently verified user identity in middleware.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_travel.domain.upstream_personal_ai_context import (
    PersonalAISidecarActionPolicy,
    PersonalAISidecarConversation,
    PersonalAISidecarHistory,
    PersonalAISidecarPreview,
)

type SidecarConversationResponse = PersonalAISidecarConversation
type SidecarActionPolicyResponse = PersonalAISidecarActionPolicy
type SidecarHistoryResponse = PersonalAISidecarHistory
type SidecarPreviewResponse = PersonalAISidecarPreview

__all__ = [
    "ContextEntityReference",
    "ContextFieldSensitivity",
    "ContextPermissionDependency",
    "ContextReadRecord",
    "ContextReadRequest",
    "ContextReadResponse",
    "ContextSourceReference",
    "SidecarConversationResponse",
    "SidecarActionPolicyResponse",
    "SidecarCreateRequest",
    "SidecarHistoryResponse",
    "SidecarPreviewResponse",
    "SidecarTurnRequest",
    "SidecarTurnResponse",
]


class ContextEntityReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: str = Field(min_length=1, max_length=80)
    entity_id: str = Field(min_length=1, max_length=200)
    application_id: str = Field(min_length=2, max_length=42)
    workspace_id: str | None = Field(default=None, max_length=100)


class ContextSourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["record", "conversation_message", "evidence", "url", "tool_invocation"]
    reference_id: str = Field(min_length=1, max_length=512)
    uri: str | None = Field(default=None, max_length=2048)
    fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ContextPermissionDependency(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    permission_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=160)
    source_application_id: str | None = Field(default=None, min_length=2, max_length=42)
    destination_application_id: str | None = Field(default=None, min_length=2, max_length=42)


class ContextFieldSensitivity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    sensitivity: Literal["public", "personal", "sensitive", "restricted", "unknown"]


class ContextReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["travel-context-read-v2"] = "travel-context-read-v2"
    application_id: Literal["travel"] = "travel"
    workspace_id: None = None
    operation: Literal["current", "entity", "verify", "federation"]
    purpose: Literal["personal_ai_context", "shopping_trip_context"] = "personal_ai_context"
    destination_application_id: Literal["shopping"] | None = None
    entity_refs: tuple[ContextEntityReference, ...] = Field(default=(), max_length=16)
    fields: tuple[str, ...] = Field(min_length=1, max_length=32)
    max_results: int = Field(ge=1, le=50)
    max_bytes: int = Field(ge=1, le=65_536)

    @model_validator(mode="after")
    def references_are_scoped(self) -> ContextReadRequest:
        if len(set(self.entity_refs)) != len(self.entity_refs):
            raise ValueError("context_entity_references_must_be_unique")
        if any(
            reference.application_id != "travel" or reference.workspace_id is not None
            for reference in self.entity_refs
        ):
            raise ValueError("context_entity_reference_scope_invalid")
        if not self.entity_refs:
            raise ValueError("context_entity_reference_required")
        if self.operation == "federation" or self.purpose == "shopping_trip_context":
            expected_fields = (
                ("trip_revision",)
                if self.operation == "verify"
                else ("destination", "start_date", "end_date")
            )
            if (
                self.operation not in {"federation", "verify"}
                or self.purpose != "shopping_trip_context"
                or self.destination_application_id != "shopping"
                or len(self.entity_refs) != 1
                or self.entity_refs[0].entity_type != "trip"
                or self.fields != expected_fields
                or self.max_results != 1
            ):
                raise ValueError("travel_federation_export_scope_invalid")
        elif self.destination_application_id is not None:
            raise ValueError("context_destination_scope_invalid")
        return self


class ContextSelectionBinding(BaseModel):
    """Travel-owned proof that a derived reservation is linked to an anchor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: Literal["trip", "day", "itinerary_item", "place"]
    entity_id: str = Field(min_length=1, max_length=200)
    relationship: Literal[
        "trip_reservation_summary",
        "day_reservation_summary",
        "itinerary_item_reservation_summary",
        "place_reservation_summary",
    ]

    @model_validator(mode="after")
    def relationship_matches_anchor(self) -> ContextSelectionBinding:
        if self.relationship.split("_reservation_summary", 1)[0] != self.entity_type:
            raise ValueError("context_selection_binding_mismatch")
        return self


class ContextReadRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: str = Field(min_length=1, max_length=80)
    entity_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=200)
    source_version: str = Field(min_length=1, max_length=100)
    authority: Literal[
        "authoritative", "user_asserted", "derived", "external", "client_supplied", "unknown"
    ]
    observed_at: str | None = None
    effective_at: str | None = None
    expires_at: str | None = None
    sensitivity: Literal["public", "personal", "sensitive", "restricted", "unknown"]
    payload: dict[str, str | int | float | bool | None] = Field(max_length=32)
    source_refs: tuple[ContextSourceReference, ...] = Field(min_length=1, max_length=16)
    permission_dependencies: tuple[ContextPermissionDependency, ...] = Field(
        default=(), max_length=16
    )
    field_sensitivity: tuple[ContextFieldSensitivity, ...] = Field(default=(), max_length=32)
    selection_binding: ContextSelectionBinding | None = None


class ContextReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["travel-context-read-v2"] = "travel-context-read-v2"
    application_id: Literal["travel"] = "travel"
    workspace_id: None = None
    items: tuple[ContextReadRecord, ...] = Field(default=(), max_length=50)
    complete: bool = True
    authoritative_empty_entities: tuple[str, ...] = Field(default=(), max_length=16)
    continuation: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def completeness_is_explicit(self) -> ContextReadResponse:
        if self.complete and self.continuation is not None:
            raise ValueError("travel_context_completeness_invalid")
        if not self.complete and not self.continuation:
            raise ValueError("travel_context_continuation_required")
        if len(set(self.authoritative_empty_entities)) != len(self.authoritative_empty_entities):
            raise ValueError("travel_context_empty_entity_duplicate")
        return self


class SidecarCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(default="Travel assistant", min_length=1, max_length=200)
    idempotency_key: UUID


class SidecarTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str = Field(min_length=1, max_length=20_000)
    idempotency_key: UUID
    entity_refs: tuple[ContextEntityReference, ...] = Field(default=(), max_length=16)
    view_id: Literal["travel.trip", "travel.day", "travel.place"] = "travel.trip"
    excluded_source_classes: tuple[
        Literal[
            "global_profile",
            "domain_profile",
            "domain_current",
            "domain_history",
            "ai_memory",
            "external_research",
            "conversation",
            "client_context",
            "tool_result",
        ],
        ...,
    ] = Field(default=(), max_length=9)

    @model_validator(mode="after")
    def references_are_scoped(self) -> SidecarTurnRequest:
        if len(set(self.entity_refs)) != len(self.entity_refs):
            raise ValueError("context_entity_references_must_be_unique")
        if any(
            reference.application_id != "travel" or reference.workspace_id is not None
            for reference in self.entity_refs
        ):
            raise ValueError("context_entity_reference_scope_invalid")
        if len(set(self.excluded_source_classes)) != len(self.excluded_source_classes):
            raise ValueError("context_source_exclusions_must_be_unique")
        return self


class SidecarTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: UUID
    answer: str = Field(min_length=1, max_length=20_000)
