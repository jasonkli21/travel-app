"""Resolve only consented trip export fields into immutable Shopping note commands."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from personal_travel.clients.task_wire import DelegatedSourceCommand
from personal_travel.models.auth import AuthIdentity
from personal_travel.models.source_command import DelegatedSourceCommand as CommandRow
from personal_travel.models.trip import Trip
from personal_travel.services.errors import DomainError


class TripTaskChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: UUID
    trip_id: UUID
    expected_trip_revision: int = Field(strict=True, ge=0)
    context_choice_id: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    shopping_project_id: UUID
    expected_project_revision: int = Field(strict=True, ge=1, le=10**18 - 1)


class ShoppingCommandService:
    def __init__(self, session: Session, owner_id: str):
        self.session, self.owner_id = session, owner_id

    def owned(self, command_id: UUID) -> CommandRow:
        identity = self.session.get(AuthIdentity, self.owner_id)
        if identity is None or identity.status != "active":
            raise DomainError(
                "task_identity_denied", "The account is unavailable.", status_code=401
            )
        row = self.session.get(CommandRow, command_id)
        if row is None or row.owner_id != self.owner_id:
            raise DomainError("command_not_found", "The command was not found.", status_code=404)
        return row

    def current(self, command_id: UUID) -> DelegatedSourceCommand:
        command = DelegatedSourceCommand.model_validate(self.owned(command_id).command)
        self._trip(UUID(command.source_entity_id), command.expected_source_version)
        return command

    def _trip(self, trip_id: UUID, version: str) -> Trip:
        trip = self.session.scalar(
            select(Trip)
            .where(
                Trip.id == trip_id,
                Trip.owner_id == self.owner_id,
            )
            .with_for_update()
        )
        if trip is None or not trip.cross_domain_sharing_allowed or str(trip.revision) != version:
            raise DomainError(
                "trip_consent_or_revision_changed", "Shared trip context changed.", status_code=409
            )
        return trip

    def create(self, choice: TripTaskChoice, operation_fingerprint: str) -> DelegatedSourceCommand:
        if self.session.get_bind().dialect.name == "postgresql":
            key = int.from_bytes(sha256(choice.command_id.bytes).digest()[:8], "big", signed=True)
            self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        existing = self.session.get(CommandRow, choice.command_id)
        if existing is not None:
            command = DelegatedSourceCommand.model_validate(self.owned(choice.command_id).command)
            if (
                command.source_entity_id != str(choice.trip_id)
                or command.expected_source_version != str(choice.expected_trip_revision)
                or command.context_choice_id != choice.context_choice_id
                or command.target_entity_id != choice.shopping_project_id
                or command.expected_domain_version != str(choice.expected_project_revision)
            ):
                raise DomainError(
                    "command_idempotency_conflict",
                    "The command key has different inputs.",
                    status_code=409,
                )
            return command
        trip = self._trip(choice.trip_id, str(choice.expected_trip_revision))
        content = (
            f"Trip destination: {trip.destination or 'unspecified'}; "
            f"dates: {trip.start_date.isoformat()} to {trip.end_date.isoformat()}."
        )
        command = DelegatedSourceCommand(
            source_application_id="travel",
            command_id=choice.command_id,
            context_choice_id=choice.context_choice_id,
            source_entity_type="trip",
            source_entity_id=str(trip.id),
            expected_source_version=str(trip.revision),
            target_entity_id=choice.shopping_project_id,
            expected_domain_version=str(choice.expected_project_revision),
            operation_id="project.append_context_note",
            operation_version=1,
            operation_contract_fingerprint=operation_fingerprint,
            payload_schema_id="shopping-context-note-v1",
            payload={"content": content},
            rationale="Append only the explicitly shared destination and trip dates.",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        self.session.add(
            CommandRow(
                command_id=command.command_id,
                owner_id=self.owner_id,
                command=command.model_dump(mode="json"),
                created_at=datetime.now(UTC),
            )
        )
        self.session.flush()
        return command

    def bind(self, command_id: UUID, proposal_id: UUID) -> None:
        row = self.owned(command_id)
        if row.proposal_id is not None and row.proposal_id != proposal_id:
            raise DomainError(
                "command_proposal_conflict", "The command is already bound.", status_code=409
            )
        row.proposal_id = proposal_id
        self.session.flush()
