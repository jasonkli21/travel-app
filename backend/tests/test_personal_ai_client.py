import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

import personal_travel.clients.personal_ai as personal_ai
from personal_travel.clients.personal_ai import (
    PersonalAIClient,
    PersonalAIError,
    PersonalAIHealth,
    PersonalAIProposalUnknown,
)


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


def _proposal_result(
    *,
    state: str = "proposed",
    trip_handle: str = "h_triphandle000000001",
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "schema_version": "itinerary-proposal-v1",
        "proposal_id": str(uuid4()),
        "state": state,
        "policy_version": "itinerary-proposal-policy-v2",
        "support_mode": "context_only",
        "trip_handle": trip_handle,
        "operations": (
            [
                {
                    "kind": "set_item_times",
                    "item_handle": "h_itemhandle000000001",
                    "start_time": "09:30",
                }
            ]
            if state == "proposed"
            else []
        ),
        "operation_support": [],
        "citations": [],
        "failure_code": None if state in {"running", "proposed"} else "no_research_evidence",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }


def test_personal_ai_proposal_parses_wire_json_and_reconciles_running_by_same_key() -> None:
    key = uuid4()
    methods: list[tuple[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        methods.append((request.method, request.url.path))
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["idempotency_key"] == str(key)
            return httpx.Response(200, json=_proposal_result(state="running"), request=request)
        return httpx.Response(200, json=_proposal_result(), request=request)

    transport = httpx.MockTransport(handle)
    client = PersonalAIClient(
        base_url="http://personal-ai.test", timeout_seconds=1, transport=transport
    )

    result = asyncio.run(
        client.create_itinerary_proposal(
            payload={
                "schema_version": "itinerary-proposal-v1",
                "idempotency_key": str(key),
            },
            idempotency_key=key,
        )
    )

    assert result.state == "proposed"
    assert result.operations[0].kind == "set_item_times"
    assert result.operations[0].model_fields_set == {"kind", "item_handle", "start_time"}
    assert methods == [
        ("POST", "/v1/travel/itinerary-proposals"),
        ("GET", f"/v1/travel/itinerary-proposals/by-key/{key}"),
    ]


def test_personal_ai_proposal_never_retries_post_after_ambiguous_timeout() -> None:
    key = uuid4()
    methods: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "POST":
            raise httpx.ReadTimeout("ambiguous", request=request)
        return httpx.Response(404, request=request)

    client = PersonalAIClient(
        base_url="http://personal-ai.test",
        timeout_seconds=1,
        transport=httpx.MockTransport(handle),
    )
    with pytest.raises(PersonalAIProposalUnknown):
        asyncio.run(client.create_itinerary_proposal(payload={"safe": True}, idempotency_key=key))

    assert methods == ["POST", "GET"]


def _research_session(
    session_id: str, state: str, *, answer: str | None = None
) -> dict[str, object]:
    expires_at = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    payload: dict[str, object] = {
        "schema_version": "research-v1",
        "id": session_id,
        "state": state,
        "expires_at": expires_at,
        "failure_code": None,
        "answer": answer,
        "citations": [],
    }
    if state == "completed":
        payload["citations"] = [
            {
                "number": 1,
                "evidence_id": str(uuid4()),
                "source_observation_id": str(uuid4()),
                "url": "https://example.test/source",
                "title": "Example source",
                "observed_at": datetime.now(UTC).isoformat(),
                "expires_at": expires_at,
            }
        ]
    return payload


def test_personal_ai_research_creates_runs_and_reads_cited_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = str(uuid4())
    key = uuid4()
    requests: list[httpx.Request] = []
    terminal = {
        "schema_version": "research-v1",
        "session_id": session_id,
        "state": "completed",
        "failure_code": None,
    }
    stream = (
        "event: research.started\n"
        f'data: {{"schema_version":"research-v1","session_id":"{session_id}",'
        '"state":"running"}\n\n'
        "event: research.terminal\n"
        f"data: {json.dumps(terminal)}\n\n"
    )
    result_payload = _research_session(session_id, "completed", answer="A cited answer.")

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/research" and request.method == "POST":
            return httpx.Response(
                201,
                json=_research_session(session_id, "pending"),
                request=request,
            )
        if request.url.path.endswith("/run"):
            return httpx.Response(200, text=stream, request=request)
        if request.url.path == f"/v1/research/{session_id}" and request.method == "GET":
            return httpx.Response(200, json=result_payload, request=request)
        return httpx.Response(404, request=request)

    client = _client_with_transport(monkeypatch, httpx.MockTransport(handle))
    result = asyncio.run(
        client.research(
            question="Trip context: Kyoto\nQuestion: Find a quiet dinner",
            freshness="current",
            idempotency_key=key,
        )
    )

    assert result.state == "completed"
    assert result.answer == "A cited answer."
    assert result.citations[0].url == "https://example.test/source"
    assert [request.method for request in requests] == ["POST", "POST", "GET"]
    assert json.loads(requests[0].content)["idempotency_key"] == str(key)


def test_personal_ai_research_rejects_wrong_session_sse_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = str(uuid4())
    wrong_session_id = str(uuid4())

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/research":
            return httpx.Response(
                201,
                json=_research_session(session_id, "pending"),
                request=request,
            )
        if request.url.path.endswith("/run"):
            return httpx.Response(
                200,
                text=(
                    "event: research.terminal\n"
                    f'data: {{"schema_version":"research-v1","session_id":"{wrong_session_id}",'
                    '"state":"failed","failure_code":"upstream_error"}\n\n'
                ),
                request=request,
            )
        return httpx.Response(200, json=_research_session(session_id, "failed"), request=request)

    client = _client_with_transport(monkeypatch, httpx.MockTransport(handle))
    with pytest.raises(PersonalAIError, match="mismatched research event"):
        asyncio.run(
            client.research(
                question="Question",
                freshness="general",
                idempotency_key=uuid4(),
            )
        )


def test_personal_ai_research_accepts_durable_result_when_terminal_frame_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = str(uuid4())
    detail = _research_session(session_id, "completed", answer="Durable cited result.")

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/research":
            return httpx.Response(
                201,
                json=_research_session(session_id, "pending"),
                request=request,
            )
        if request.url.path.endswith("/run"):
            return httpx.Response(
                200,
                text=(
                    "event: research.started\n"
                    f'data: {{"schema_version":"research-v1","session_id":"{session_id}",'
                    '"state":"running"}\n\n'
                ),
                request=request,
            )
        return httpx.Response(200, json=detail, request=request)

    client = _client_with_transport(monkeypatch, httpx.MockTransport(handle))
    result = asyncio.run(
        client.research(
            question="Question",
            freshness="general",
            idempotency_key=uuid4(),
        )
    )

    assert result.state == "completed"
    assert result.answer == "Durable cited result."


def test_personal_ai_research_rejects_oversized_sse_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = str(uuid4())

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/research":
            return httpx.Response(
                201,
                json=_research_session(session_id, "pending"),
                request=request,
            )
        if request.url.path.endswith("/run"):
            return httpx.Response(
                200,
                text=f"event: research.started\ndata: {'x' * 16_500}\n\n",
                request=request,
            )
        return httpx.Response(200, json=_research_session(session_id, "failed"), request=request)

    client = _client_with_transport(monkeypatch, httpx.MockTransport(handle))
    with pytest.raises(PersonalAIError, match="oversized research stream"):
        asyncio.run(
            client.research(
                question="Question",
                freshness="general",
                idempotency_key=uuid4(),
            )
        )


class SlowStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        # Each chunk arrives well inside the HTTP read timeout, but the request
        # never terminates. Only the end-to-end deadline can bound this stream.
        while True:
            await asyncio.sleep(0.01)
            yield b": heartbeat\n\n"


@pytest.mark.asyncio
async def test_research_deadline_bounds_a_trickling_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = str(uuid4())

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/research":
            return httpx.Response(201, json=_research_session(session_id, "pending"))
        return httpx.Response(200, stream=SlowStream())

    client = _client_with_transport(monkeypatch, httpx.MockTransport(handle))
    with pytest.raises(PersonalAIError, match="deadline"):
        await client.research(question="Bounded", freshness="current", idempotency_key=uuid4())


class OversizedStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.consumed = 0

    async def __aiter__(self):
        for _ in range(1000):
            self.consumed += 4096
            yield b"x" * 4096


@pytest.mark.asyncio
async def test_research_json_bound_stops_reading_before_buffering_full_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = OversizedStream()
    client = _client_with_transport(
        monkeypatch, httpx.MockTransport(lambda _request: httpx.Response(201, stream=stream))
    )
    with pytest.raises(PersonalAIError, match="oversized"):
        await client.research(question="Bounded", freshness="current", idempotency_key=uuid4())
    assert stream.consumed <= personal_ai.MAX_RESEARCH_RESPONSE_BYTES + 4096


@pytest.mark.parametrize("state", ["pending", "running", "failed", "insufficient", "expired"])
def test_noncompleted_research_never_exposes_answer_or_citations(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    session_id = str(uuid4())
    payload = _research_session(session_id, state, answer="PRIVATE UPSTREAM TEXT")
    client = _client_with_transport(
        monkeypatch, httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    )
    if state in {"pending", "running"}:
        with pytest.raises(PersonalAIError):
            asyncio.run(
                client.research(question="Question", freshness="current", idempotency_key=uuid4())
            )
    else:
        result = asyncio.run(
            client.research(question="Question", freshness="current", idempotency_key=uuid4())
        )
        assert result.answer is None
        assert result.citations == []
