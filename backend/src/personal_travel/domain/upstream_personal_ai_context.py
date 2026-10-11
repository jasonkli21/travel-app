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
