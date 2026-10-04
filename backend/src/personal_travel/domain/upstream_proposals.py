"""Pinned strict DTOs for personal-ai-system's itinerary-proposal-v1 API."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from personal_travel.domain.proposals import LocalTimeValue, ProposalOperation

UPSTREAM_REVISION = "8535cad3a146b1a19cab0958c439f170d19b8095"
SCHEMA_VERSION = "itinerary-proposal-v1"
CONTEXT_VERSION = "travel-itinerary-context-v1"
POLICY_VERSION = "itinerary-proposal-policy-v2"

ProposalHandle = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=10, max_length=66, pattern=r"^h_[A-Za-z0-9_-]{8,64}$"
    ),
]
EvidenceHandle = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=10, max_length=66, pattern=r"^e_[A-Za-z0-9_-]{8,64}$"
    ),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProposalItemContext(StrictModel):
    handle: ProposalHandle
    label: str = Field(min_length=1, max_length=240)
    item_type: Literal["activity", "food", "lodging", "transport", "flight", "note"]
    status: Literal["tentative", "planned", "booked", "completed", "cancelled"]
    start_time: LocalTimeValue | None
    end_time: LocalTimeValue | None
    protected: bool
    removable: bool

    @model_validator(mode="after")
    def protect_completed(self) -> "ProposalItemContext":
        if self.status in {"booked", "completed"} and not self.protected:
            raise ValueError("booked and completed items must be protected")
        return self


class ProposalDayContext(StrictModel):
    handle: ProposalHandle
    day_index: Annotated[int, Field(strict=True, ge=1, le=366)]
    date: date
    items: tuple[ProposalItemContext, ...] = Field(max_length=5000)


class ProposalCandidateContext(StrictModel):
    handle: ProposalHandle
    label: str = Field(min_length=1, max_length=240)


class TravelItineraryContext(StrictModel):
    schema_version: Literal["travel-itinerary-context-v1"] = "travel-itinerary-context-v1"
    trip_handle: ProposalHandle
    title: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date
    timezone: str = Field(min_length=1, max_length=64)
    days: tuple[ProposalDayContext, ...] = Field(min_length=1, max_length=366)
    candidates: tuple[ProposalCandidateContext, ...] = Field(max_length=500)
    removable_item_handles: tuple[ProposalHandle, ...] = Field(max_length=25)

    @model_validator(mode="after")
    def validate_context(self) -> "TravelItineraryContext":
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        start, end = self.start_date, self.end_date
        span = (end - start).days + 1
        if span < 1 or span > 366 or len(self.days) != span:
            raise ValueError("trip date range and day projection disagree")
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("unknown trip timezone") from error
        handles = [self.trip_handle]
        by_item: dict[str, ProposalItemContext] = {}
        for index, day in enumerate(self.days, start=1):
            if day.day_index != index or day.date != start + timedelta(days=index - 1):
                raise ValueError("trip days must be contiguous and ordered")
            handles.append(day.handle)
            for item in day.items:
                handles.append(item.handle)
                by_item[item.handle] = item
        handles.extend(candidate.handle for candidate in self.candidates)
        if len(handles) != len(set(handles)):
            raise ValueError("context handles must be unique across kinds")
        if len(self.removable_item_handles) != len(set(self.removable_item_handles)):
            raise ValueError("duplicate removable handle")
        for handle in self.removable_item_handles:
            allowlisted_item = by_item.get(handle)
            if (
                allowlisted_item is None
                or allowlisted_item.protected
                or not allowlisted_item.removable
            ):
                raise ValueError("removal allowlist contains an ineligible item")
        if any(
            item.removable != (item.handle in self.removable_item_handles)
            for item in by_item.values()
        ):
            raise ValueError("removal flags and allowlist disagree")
        return self


class ProposalCitation(StrictModel):
    evidence_handle: EvidenceHandle
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=300)
    observed_at: datetime
    expires_at: datetime

    @field_validator("observed_at", "expires_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("citation timestamps require a timezone")
        return value.astimezone(UTC)


class OperationEvidenceSupport(StrictModel):
    operation_index: Annotated[int, Field(strict=True, ge=0, le=24)]
    evidence_handles: tuple[EvidenceHandle, ...] = Field(max_length=24)


class UpstreamProposalResult(StrictModel):
    schema_version: Literal["itinerary-proposal-v1"] = "itinerary-proposal-v1"
    proposal_id: UUID
    state: Literal["running", "proposed", "insufficient", "uncited", "expired", "failed"]
    policy_version: Literal["itinerary-proposal-policy-v2"] = "itinerary-proposal-policy-v2"
    support_mode: Literal["context_only", "research_evidence"]
    trip_handle: ProposalHandle
    operations: tuple[ProposalOperation, ...] = Field(max_length=25)
    operation_support: tuple[OperationEvidenceSupport, ...] = Field(max_length=25)
    citations: tuple[ProposalCitation, ...] = Field(max_length=24)
    failure_code: str | None = Field(default=None, max_length=80, pattern=r"^[a-z0-9_]+$")
    created_at: datetime
    expires_at: datetime

    @field_validator("created_at", "expires_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("proposal timestamps require a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_response(self) -> "UpstreamProposalResult":
        if self.expires_at > self.created_at + timedelta(hours=24):
            raise ValueError("proposal lifetime exceeds 24 hours")
        if self.state in {"proposed", "running"} and self.failure_code is not None:
            raise ValueError("successful response cannot include failure")
        if self.state == "proposed" and not self.operations:
            raise ValueError("proposed response requires operations")
        if self.state not in {"proposed", "running"} and not self.failure_code:
            raise ValueError("terminal failure response requires failure code")
        if self.state != "proposed" and (
            self.operations or self.operation_support or self.citations
        ):
            raise ValueError("non-proposal response cannot include operations or citations")
        if self.support_mode == "context_only" and (self.operation_support or self.citations):
            raise ValueError("context-only response cannot claim evidence")
        evidence = {citation.evidence_handle for citation in self.citations}
        if len(evidence) != len(self.citations):
            raise ValueError("duplicate evidence citation")
        support = {item.operation_index: item.evidence_handles for item in self.operation_support}
        if len(support) != len(self.operation_support):
            raise ValueError("duplicate operation support entry")
        if any(
            index >= len(self.operations) or any(handle not in evidence for handle in handles)
            for index, handles in support.items()
        ):
            raise ValueError("operation support references unknown citation")
        if self.support_mode == "research_evidence" and self.state == "proposed":
            if set(index for index, handles in support.items() if handles) != set(
                range(len(self.operations))
            ):
                raise ValueError("every evidence-backed operation needs a citation")
        if any(self.expires_at > citation.expires_at for citation in self.citations):
            raise ValueError("proposal outlives evidence")
        if any(
            citation.observed_at > self.created_at or citation.expires_at <= citation.observed_at
            for citation in self.citations
        ):
            raise ValueError("invalid citation timestamps")
        return self
