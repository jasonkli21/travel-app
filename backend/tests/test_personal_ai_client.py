import asyncio

import httpx
import pytest

import personal_travel.clients.personal_ai as personal_ai
from personal_travel.clients.personal_ai import PersonalAIClient, PersonalAIError, PersonalAIHealth


def _client_with_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: httpx.MockTransport,
) -> PersonalAIClient:
    async_client = httpx.AsyncClient

    def create_client(*, timeout: float) -> httpx.AsyncClient:
        return async_client(transport=handler, timeout=timeout)

    monkeypatch.setattr(personal_ai.httpx, "AsyncClient", create_client)
    return PersonalAIClient(base_url="http://personal-ai.test", timeout_seconds=0.1)


def test_personal_ai_health_returns_typed_response(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"status": "ok", "service": "personal-ai-api"},
            request=request,
        )
    )
    client = _client_with_transport(monkeypatch, transport)

    result = asyncio.run(client.health())

    assert result == PersonalAIHealth(status="ok", service="personal-ai-api")


def test_personal_ai_health_reports_http_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(503, request=request))
    client = _client_with_transport(monkeypatch, transport)

    with pytest.raises(PersonalAIError, match="is unavailable"):
        asyncio.run(client.health())


def test_personal_ai_health_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("request timed out", request=request)

    client = _client_with_transport(monkeypatch, httpx.MockTransport(timeout))

    with pytest.raises(PersonalAIError, match="is unavailable"):
        asyncio.run(client.health())


def test_personal_ai_health_reports_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="{bad", request=request)
    )
    client = _client_with_transport(monkeypatch, transport)

    with pytest.raises(PersonalAIError, match="invalid health JSON"):
        asyncio.run(client.health())


@pytest.mark.parametrize(
    "payload",
    [[], {"service": "personal-ai-api"}, {"status": "ok", "service": 7}],
)
def test_personal_ai_health_reports_invalid_payload_shape(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload, request=request)
    )
    client = _client_with_transport(monkeypatch, transport)

    with pytest.raises(PersonalAIError, match="invalid health response"):
        asyncio.run(client.health())
