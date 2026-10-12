"""Strict consumer models for the Personal AI context and sidecar contract."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ContextSourceClass = Literal[
    "global_profile",
    "domain_profile",
    "domain_current",
    "domain_history",
    "ai_memory",
    "conversation",
    "external_research",
    "tool_result",
    "client_context",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PersonalAISidecarConversation(_StrictModel):
    id: UUID
    application_id: Literal["travel"]
    workspace_id: None = None


class PersonalAISidecarActionPolicy(_StrictModel):
    copy_allowed: bool
    draft_insert_target_ids: tuple[str, ...] = Field(default=(), max_length=16)


class PersonalAIFederationEntity(_StrictModel):
    entity_type: str = Field(min_length=1, max_length=80)
    entity_id: str = Field(min_length=1, max_length=200)
    application_id: str = Field(min_length=2, max_length=42)
    workspace_id: str | None = Field(default=None, max_length=100)


class PersonalAIFederationSource(_StrictModel):
    application_id: str = Field(min_length=2, max_length=42)
    workspace_id: str | None = Field(default=None, max_length=100)


class PersonalAIFederationSelection(_StrictModel):
    grant_id: UUID
    issuance_id: UUID
    revision: int = Field(ge=1)
    epoch: int = Field(ge=1)
    source: PersonalAIFederationSource
    category: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1, max_length=160)
    entities: tuple[PersonalAIFederationEntity, ...] = Field(min_length=1, max_length=16)


class PersonalAIFederationContextChoice(_StrictModel):
    provider_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,80}$")
    fields: tuple[str, ...] = Field(min_length=1, max_length=32)
    federation: PersonalAIFederationSelection


class PersonalAISidecarFederationChoice(_StrictModel):
    id: str = Field(min_length=1, max_length=100)
    category_label: str = Field(min_length=1, max_length=100)
    choice: PersonalAIFederationContextChoice


class PersonalAISidecarFederationChoices(_StrictModel):
    choices: tuple[PersonalAISidecarFederationChoice, ...] = Field(max_length=100)


class PersonalAISidecarMessage(_StrictModel):
    id: UUID
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)
    status: Literal["completed", "failed"]


class PersonalAISidecarHistory(_StrictModel):
    id: UUID
    application_id: Literal["travel"]
    workspace_id: None = None
    messages: tuple[PersonalAISidecarMessage, ...] = Field(max_length=40)


class PersonalAISidecarPreviewSource(_StrictModel):
    category: str = Field(min_length=1, max_length=48)
    source_class: ContextSourceClass | None = None
    operation: str = Field(min_length=1, max_length=32)
    fields: tuple[str, ...] = Field(default=(), max_length=32)
    max_results: int = Field(ge=1, le=50)
    max_tokens: int | None = Field(default=None, ge=1, le=128_000)


class PersonalAISidecarPreviewExclusion(_StrictModel):
    category: str = Field(min_length=1, max_length=48)
    reason: str = Field(min_length=1, max_length=64)


class PersonalAISidecarPreview(_StrictModel):
    selected_message_count: int = Field(ge=0, le=20_000)
    planned_sources: tuple[PersonalAISidecarPreviewSource, ...] = Field(default=(), max_length=16)
    excluded_sources: tuple[PersonalAISidecarPreviewExclusion, ...] = Field(
        default=(), max_length=64
    )
