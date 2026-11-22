"""Internal Travel reverse-hop contract for isolated PAS plan previews."""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

PRIVATE_PLAN_LANE = "user_owned_plan"
PRIVATE_PLAN_SOURCE_PATH = "/internal/personal-ai/plan-source"
PRIVATE_PLAN_SOURCE_MAX_BYTES = 4096
EXPECTED_REVISION_PATTERN = r"^(0|[1-9][0-9]{0,9})$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class PrivatePlanSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["travel-private-plan-source-v1"]
    purpose: Literal["personal_ai_context"]
    destination_application_id: Literal["travel"]
    workspace_id: None
    lane: Literal["user_owned_plan"]
    target_id: str = Field(min_length=1, max_length=200)
    client_namespace: str = Field(pattern=SHA256_PATTERN)
    conversation_id: UUID
    operation: Literal["current", "verify"]
    expected_source_version: str | None = Field(default=None, pattern=EXPECTED_REVISION_PATTERN)

    @model_validator(mode="after")
    def verify_requires_expected_source_version(self) -> Self:
        if (self.operation == "verify") != (self.expected_source_version is not None):
            raise ValueError("private_plan_source_version_invalid")
        return self
