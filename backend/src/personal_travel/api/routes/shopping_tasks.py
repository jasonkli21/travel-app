"""Thin Travel host for explicit Shopping command review, confirmation and recovery."""

import time
from typing import Any, Literal, cast
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session

from personal_travel.api.dependencies import OwnerDependency, SessionDependency
from personal_travel.auth.contracts import VerifiedPrincipal
from personal_travel.auth.google_oidc import cloud_run_service_id_token
from personal_travel.clients.task_control import (
    SourceTaskConfirmation,
    SourceTaskControlClient,
    SourceTaskStatus,
    TaskControlRejected,
    TaskControlUnavailable,
    source_task_status,
    submitted_source_task,
)
from personal_travel.clients.task_wire import (
    COMMAND_READ_FINGERPRINT,
    DelegatedCommandRead,
    DelegatedSourceCommand,
)
from personal_travel.config import Settings
from personal_travel.services.shopping_commands import ShoppingCommandService, TripTaskChoice

router = APIRouter(tags=["Delegated Shopping tasks"])
PREFIX = "/v1/shopping-tasks"


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.auth_settings_provider())


def _principal(request: Request) -> VerifiedPrincipal:
    if _settings(request).task_control is None:
        raise HTTPException(404, "not_found")
    principal = request.scope.get("principal")
    auth_time = getattr(principal, "authenticated_at", None)
    if (
        not isinstance(principal, VerifiedPrincipal)
        or auth_time is None
        or not -30 <= int(time.time()) - auth_time <= 300
    ):
        raise HTTPException(401, "recent_authentication_required")
    return principal


def _relay(
    request: Request, method: str, path: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    principal, task = _principal(request), _settings(request).task_control
    if task is None:
        raise HTTPException(404, "not_found")
    client = getattr(request.app.state, "task_control_client", None) or SourceTaskControlClient(
        application_id="travel",
        base_url=task.base_url,
        audience=task.audience,
        issuer=task.issuer,
        key_id=task.key_id,
        private_key=task.private_key.get_secret_value(),
        service_account=task.service_account,
        token_fetcher=cloud_run_service_id_token,
        client_type=httpx.Client,
    )
    try:
        return client.request(principal, method, path, body)
    except TaskControlRejected as error:
        raise HTTPException(error.status, "task_control_rejected") from error
    except TaskControlUnavailable as error:
        raise HTTPException(503, "task_control_unavailable") from error


@router.get(PREFIX + "/capabilities")
def capabilities(request: Request) -> dict[str, bool]:
    auth_time = getattr(request.scope.get("principal"), "authenticated_at", None)
    return {
        "enabled": _settings(request).task_control is not None,
        "recent_authentication": auth_time is not None
        and -30 <= int(time.time()) - auth_time <= 300,
    }


@router.get(PREFIX + "/choices")
def choices(request: Request) -> dict[str, Any]:
    return _relay(request, "GET", "/v1/federation/grants/choices")


@router.post(PREFIX + "/commands", response_model=DelegatedSourceCommand, status_code=201)
def create(
    request: Request, choice: TripTaskChoice, session: SessionDependency, owner_id: OwnerDependency
) -> DelegatedSourceCommand:
    _principal(request)
    task = _settings(request).task_control
    if task is None:
        raise HTTPException(404, "not_found")
    result = ShoppingCommandService(session, owner_id).create(choice, task.operation_fingerprint)
    session.commit()
    return result


@router.post(
    "/internal/personal-ai/commands/read",
    response_model=DelegatedSourceCommand,
    include_in_schema=False,
)
async def read(
    request: Request, session: SessionDependency, owner_id: OwnerDependency
) -> DelegatedSourceCommand:
    _principal(request)
    try:
        payload = DelegatedCommandRead.model_validate_json(await request.body())
        if (
            payload.destination_application_id != "travel"
            or payload.workspace_id is not None
            or request.headers.get("x-task-contract-fingerprint") != COMMAND_READ_FINGERPRINT
        ):
            raise ValueError
    except ValueError as error:
        raise HTTPException(409, "task_contract_unsupported") from error
    return ShoppingCommandService(session, owner_id).current(payload.command_id)


@router.post(PREFIX + "/commands/{command_id}/submit", response_model=SourceTaskStatus)
def submit(
    request: Request, command_id: UUID, session: SessionDependency, owner_id: OwnerDependency
) -> SourceTaskStatus:
    _principal(request)
    service = ShoppingCommandService(session, owner_id)
    service.owned(command_id)
    result = _relay(
        request,
        "POST",
        "/v1/mutations/commands",
        {"source_application_id": "travel", "command_id": str(command_id)},
    )
    try:
        status = submitted_source_task(result, "travel", command_id)
    except TaskControlUnavailable as error:
        raise HTTPException(503, "task_control_response_invalid") from error
    service.bind(command_id, status.proposal_id)
    session.commit()
    return status


def _proposal(session: Session, owner_id: str, command_id: UUID) -> UUID:
    row = ShoppingCommandService(session, owner_id).owned(command_id)
    if row.proposal_id is None:
        raise HTTPException(409, "command_not_submitted")
    return row.proposal_id


@router.post(PREFIX + "/commands/{command_id}/{action}", response_model=SourceTaskStatus)
def control(
    request: Request,
    command_id: UUID,
    action: Literal["validate", "confirm", "apply", "reconcile"],
    session: SessionDependency,
    owner_id: OwnerDependency,
    confirmation: SourceTaskConfirmation | None = None,
) -> SourceTaskStatus:
    _principal(request)
    if (action in {"confirm", "apply"}) != (confirmation is not None):
        raise HTTPException(422, "task_confirmation_required")
    proposal = _proposal(session, owner_id, command_id)
    result = _relay(
        request,
        "POST",
        f"/v1/mutations/{proposal}/{action}",
        confirmation.model_dump(mode="json") if confirmation else None,
    )
    try:
        return source_task_status(result, expected_proposal_id=proposal)
    except TaskControlUnavailable as error:
        raise HTTPException(503, "task_control_response_invalid") from error


@router.get(PREFIX + "/commands/{command_id}", response_model=SourceTaskStatus)
def status(
    request: Request, command_id: UUID, session: SessionDependency, owner_id: OwnerDependency
) -> SourceTaskStatus:
    _principal(request)
    proposal = _proposal(session, owner_id, command_id)
    try:
        return source_task_status(
            _relay(request, "GET", f"/v1/mutations/{proposal}"), expected_proposal_id=proposal
        )
    except TaskControlUnavailable as error:
        raise HTTPException(503, "task_control_response_invalid") from error
